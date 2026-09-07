# Final Model A / Model B analysis

## Input and output contract

- Model A uses 500 die-level measurements compressed to 100 principal components, plus two spatial-context features.
- Model B uses the complete Model A representation plus 2,000 block readings compressed independently to 100 principal components.
- Each saved model returns one failure probability per eligible die (`old_label == 0`). The probability files retain wafer and die coordinates.

## Shared test-set comparison

| Model | Fail-F1 | Precision | Recall | PR-AUC | ROC-AUC | Accuracy | Threshold |
|---|---:|---:|---:|---:|---:|---:|---:|
| Model A | 0.535168 | 0.902062 | 0.380435 | 0.532226 | 0.864897 | 0.972023 | 0.90 |
| Model B | 0.537741 | 0.842349 | 0.394928 | 0.549992 | 0.878931 | 0.971256 | 0.90 |

Adding block readings changed Fail-F1 by +0.002572 and PR-AUC by +0.017766. This directly measures the value of block-level information over the same die and spatial representation.

## Imbalance and overlapping distributions

The eligible test population contains 1,380 new failures and 31,218 passes, a failure prevalence of 4.23%. Accuracy is therefore dominated by passes and cannot be used alone. Fail-F1 measures the precision/recall trade-off at the frozen threshold, while PR-AUC measures ranking quality across thresholds and is the main threshold-independent metric for the rare failure class.

The probability-overlap figure shows that many new failures receive scores in the same range as passes. This is consistent with `marginal_fail_fraction: 0.65`: most synthetic failures were deliberately generated to be close to the pass distribution. Class balancing helps the model pay attention to the rare class, but it cannot fully separate observations whose features overlap. The high precision and lower recall show the frozen threshold favors reliable failure alerts while missing a substantial fraction of subtle failures.

## Interpretation artifacts

- `reports/model_A_per_die_explanation.png` explains one high-confidence failed die through signed feature contributions.
- `reports/model_A_spatial_contribution.png` maps the spatial part of Model A across a wafer.
- `reports/model_B_block_pattern_analysis.png` compares the 2,000-reading profiles of new failures and passes.
- `reports/model_B_block_component_importance.csv` ranks the block PCA components used by Model B.

These are reproducibility results. The shared test set had already been evaluated during earlier experiments; this build packages the frozen models and regenerates their artifacts rather than claiming a new untouched test evaluation.
