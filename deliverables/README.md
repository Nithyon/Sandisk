# Final die-yield deliverables

This directory completes the five outputs in the project brief using the
frozen Logistic Regression Model A and Model B settings.

| Brief requirement | Artifact |
|---|---|
| Trained Model A | `models/model_A_logreg.joblib` |
| Trained Model B | `models/model_B_logreg.joblib` |
| Per-die failure probabilities | `predictions/test_probabilities_model_*.csv` and `predictions/validation_probabilities_model_*.csv` |
| Side-by-side metrics | `model_comparison.csv` and `reports/model_A_vs_B_metrics.png` |
| Model A per-die explanation | `reports/model_A_per_die_explanation.png` and its contribution CSV |
| Model A spatial contribution | `reports/model_A_spatial_contribution.png` |
| Model B block analysis | `reports/model_B_block_pattern_analysis.png` and block-component CSV |
| Imbalance and overlap analysis | `reports/class_imbalance_and_probability_overlap.png` and `FINAL_ANALYSIS.md` |

The `.joblib` files contain the fitted scaler statistics, PCA transforms,
classifier, threshold, eligibility rule, and feature order. Use
`predict_final_models.py` from the repository root to load them.

Test metrics apply only to eligible dies (`old_label == 0`). Validation exports
contain all dies: previously failed dies receive probability 1.0, while the
model supplies probabilities for eligible dies.

These are reproducibility results because the shared test set was evaluated in
earlier experiments. They package the frozen models and regenerate the required
artifacts without claiming a fresh untouched test evaluation.
