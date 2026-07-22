"""Standalone prediction: a trained model + raw market data in, selections out.

    python pipeline/predict.py --model solution/p1_gbt.joblib --data-root data/market_data \
        --start 20260501 --end 20260531 --out selections_may.parquet

Output: one row per candidate fill with the model score and selected = (score > 0).
A sample is selected when the model output is positive (the task's rule).
Run from the repository root."""
import argparse
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from pipeline.features import NN_RISK, build_features  # noqa: E402
from utils.starter_samples import build_samples  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", required=True)
    p.add_argument("--data-root", default=str(REPO / "data/market_data"))
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--out", default="selections.parquet")
    p.add_argument("--workers", type=int, default=8)
    a = p.parse_args()

    bundle = joblib.load(a.model)
    feats = bundle["feats"]
    problem = bundle.get("problem", 2 if len(feats) > 17 else 1)
    deep = problem == 2
    print(f"{Path(a.model).name}: problem {problem}, {len(feats)} features, {a.start}..{a.end}")

    samples = build_samples(market_data_root=Path(a.data_root), start_date=a.start, end_date=a.end,
                            latency_us=10_000, select_window_us=100_000,
                            best_price_trades=(not deep), rebate_bps=0.0,
                            profit_horizon_us=1_000_000, workers=a.workers)
    F = build_features(samples, Path(a.data_root), deep=deep)

    if "model" in bundle:
        pred = bundle["model"].predict(F[feats])
    else:
        X = F[feats].astype(np.float32).copy()
        X[bundle.get("risk", NN_RISK)] = np.log1p(X[bundle.get("risk", NN_RISK)])
        Xs = np.nan_to_num(bundle["scaler"].transform(X), nan=0.0)
        pred = bundle["net"].predict(Xs)

    out = samples[["sample_id", "date", "server_time", "dir", "price", "volume",
                   "profit_1s_with_rebate"]].copy()
    out["pred_bps"] = pred.astype(np.float32)
    out["selected"] = pred > 0
    out.to_parquet(a.out, index=False)
    r = out.loc[out.selected, "profit_1s_with_rebate"]
    print(f"saved {a.out}: {len(out):,} fills, {int(out.selected.sum()):,} selected "
          f"({100 * out.selected.mean():.2f}%), selected total {r.sum():+.4f}, mean {1e4 * r.mean():+.4f} bps")


if __name__ == "__main__":
    main()
