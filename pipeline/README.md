# pipeline/

The standalone training and prediction pipeline (the review asked for one). No notebook, no cache:
raw market data in, models or selections out. Deterministic: fixed seeds and the exact feature
builders from the notebook (`features.py`; the notebook's "pipeline check" cell verifies
column-by-column equality). Use the pinned versions in `requirements.txt` (python 3.10).

Train (from the repository root):

    python pipeline/train.py --data-root data/market_data --problem 1 --model gbt --out p1_gbt.joblib
    python pipeline/train.py --problem 2 --model nn --out p2_nn.joblib

Predict on new dates:

    python pipeline/predict.py --model solution/p1_gbt.joblib --start 20260501 --end 20260531 --out may.parquet

A sample is selected when the model output is positive. `--problem 1` = all fills (17 features),
`--problem 2` = deep fills only (19 features, the two hidden-liquidity columns included).
