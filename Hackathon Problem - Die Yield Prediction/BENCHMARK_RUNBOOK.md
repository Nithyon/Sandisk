# Die Yield Benchmark Runbook

Run all commands from the project directory in PowerShell:

```powershell
Set-Location "path\to\Sandisk\Hackathon Problem - Die Yield Prediction"
.\.venv\Scripts\Activate.ps1
```

## Recreate the environment

The checked-in requirements use the official CUDA 12.8 PyTorch wheel source. Torchvision is intentionally absent because these models do not use it.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-benchmark.txt
```

## Verify GPU support

```powershell
python -m scripts.verify_gpu
```

All three checks must succeed. A failed GPU check is printed explicitly; the experiment runner never silently falls back to CPU. Use `--device cpu` only as an intentional choice.

## Prepare compact caches

Preprocessing calculates spatial features on complete wafers before applying eligibility in the training runner. It creates one float32 block matrix and one compressed Model A Parquet table per split. It does not modify the CSVs.

```powershell
python -m scripts.prepare_cache --split train
python -m scripts.prepare_cache --split validation
```

Do not prepare the labeled test cache while tuning. When parameters and the probability threshold are frozen, the explicit command is:

```powershell
python -m scripts.prepare_cache --split test --final-test
```

## Run the requested grouped cross-validation

Each outer fold is prediction-only. Model selection and early stopping use a separate wafer-grouped split drawn only from the corresponding outer training fold.

Start with A1 and A2 and inspect their OOF metrics before proceeding:

```powershell
python -m scripts.run_experiment --experiment A1 --device gpu
python -m scripts.run_experiment --experiment A2 --device gpu
```

Then run the remaining Model A baselines:

```powershell
python -m scripts.run_experiment --experiment A3 --device cpu
python -m scripts.run_experiment --experiment A4 --device gpu
```

Run Model B in priority order:

```powershell
python -m scripts.run_experiment --experiment B1 --device gpu
Set-Content -Path pca50.json -Value '{"pca_components":50}'
Set-Content -Path pca100.json -Value '{"pca_components":100}'
python -m scripts.run_experiment --experiment B2 --device gpu --overrides pca50.json
python -m scripts.run_experiment --experiment B2 --device gpu --overrides pca100.json

python -m scripts.run_experiment --experiment AE32 --device gpu
python -m scripts.run_experiment --experiment AE64 --device gpu

Set-Content -Path ae32.json -Value '{"latent_dim":32}'
Set-Content -Path ae64.json -Value '{"latent_dim":64}'
python -m scripts.run_experiment --experiment B3a --device gpu --overrides ae32.json
python -m scripts.run_experiment --experiment B3a --device gpu --overrides ae64.json
python -m scripts.run_experiment --experiment B3b --device gpu --overrides ae32.json
python -m scripts.run_experiment --experiment B5 --device gpu
python -m scripts.run_experiment --experiment B4 --device gpu --overrides ae32.json
python -m scripts.run_experiment --experiment B6 --device gpu --overrides ae32.json
```

Only retain B3b if its measured grouped-validation Fail-F1 or PR-AUC improves over B3a. Select AE-32 versus AE-64 using downstream B3a results, not reconstruction loss.

## Fast smoke runs

To verify wiring without claiming benchmark metrics, create an override such as:

```powershell
Set-Content -Path a1-smoke.json -Value '{"iterations":2}'
python -m scripts.run_experiment --experiment A1 --device cpu --folds 2 --overrides a1-smoke.json
```

Smoke-run metrics are real for that altered configuration but must not be presented as official benchmark results. Keep the override in the experiment notes.

## Outputs

- `cache/<split>/model_a.parquet`: macro and spatial data.
- `cache/<split>/blocks.float32.npy`: canonical row-aligned block matrix.
- `results/benchmark_results.csv`: completed measured runs only.
- `results/predictions/<experiment>_oof.csv`: grouped out-of-fold probabilities.
- `results/logs/<experiment>_cv.json`: effective run metadata.
- `results/models/`: written only when `--save-fold-models` is used, plus AE fold encoders.

Run tests with:

```powershell
python -m pytest -q
```
