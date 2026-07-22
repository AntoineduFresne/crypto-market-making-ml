"""Standalone training: raw market data in, fitted model out. No notebook, no cache.

    python pipeline/train.py --data-root data/market_data --problem 1 --model gbt --out p1_gbt.joblib

Deterministic: fixed seeds, deterministic feature build, pinned versions (requirements.txt).
Run from the repository root (it imports utils/starter_samples.py for the sample generation)."""
import argparse
import sys
from copy import deepcopy
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from pipeline.features import FEATS, FEATS_DEEP, NN_RISK, build_features  # noqa: E402
from utils.starter_samples import build_samples  # noqa: E402

NN_MAX_EPOCHS, NN_PATIENCE = 60, 5


def make_gbt():
    return HistGradientBoostingRegressor(loss="squared_error", learning_rate=0.05, max_iter=300,
                                         max_leaf_nodes=63, min_samples_leaf=1000, l2_regularization=1.0,
                                         random_state=0, early_stopping=True, validation_fraction=0.1,
                                         n_iter_no_change=20)


def nn_matrix(df, feats, scaler=None):
    X = df[feats].astype(np.float32).copy()
    X[NN_RISK] = np.log1p(X[NN_RISK])
    if scaler is None:
        scaler = StandardScaler().fit(X)
    return np.nan_to_num(scaler.transform(X), nan=0.0).astype(np.float32, copy=False), scaler


def nn_fit(tr, feats):
    days = np.sort(tr["date"].unique())
    vd = days[-max(1, len(days) // 10):]
    fit, val = tr[~tr["date"].isin(vd)], tr[tr["date"].isin(vd)]
    Xf, scaler = nn_matrix(fit, feats)
    yf = fit["y_bps"].to_numpy()
    Xv, _ = nn_matrix(val, feats, scaler)
    yv = val["y_bps"].to_numpy()
    net = MLPRegressor(hidden_layer_sizes=(32, 16), activation="relu", solver="adam",
                       batch_size=8192, learning_rate_init=1e-3, shuffle=False, random_state=0)
    rng = np.random.default_rng(0)
    best_mse, best_w, since = np.inf, None, 0
    for ep in range(NN_MAX_EPOCHS):
        order = rng.permutation(len(Xf))
        net.partial_fit(Xf[order], yf[order])
        mse = float(np.mean((net.predict(Xv) - yv) ** 2))
        if mse < best_mse:
            best_mse, best_w, since = mse, (deepcopy(net.coefs_), deepcopy(net.intercepts_)), 0
        else:
            since += 1
        if since >= NN_PATIENCE:
            break
    net.coefs_, net.intercepts_ = best_w
    print(f"net: {len(days)} train days (last {len(vd)} = temporal validation), "
          f"stopped after {ep + 1} epochs, best val MSE {best_mse:.3f}")
    return scaler, net


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-root", default=str(REPO / "data/market_data"))
    p.add_argument("--start", default="20260201")
    p.add_argument("--end", default="20260430")
    p.add_argument("--problem", type=int, choices=(1, 2), default=1)
    p.add_argument("--model", choices=("gbt", "nn"), default="gbt")
    p.add_argument("--out", default=None)
    p.add_argument("--workers", type=int, default=8)
    a = p.parse_args()

    out = Path(a.out) if a.out else Path(f"p{a.problem}_{a.model}.joblib")
    feats = FEATS if a.problem == 1 else FEATS_DEEP
    print(f"problem {a.problem} ({'all fills' if a.problem == 1 else 'deep fills'}), "
          f"model {a.model}, {len(feats)} features, {a.start}..{a.end}")

    samples = build_samples(market_data_root=Path(a.data_root), start_date=a.start, end_date=a.end,
                            latency_us=10_000, select_window_us=100_000,
                            best_price_trades=(a.problem == 1), rebate_bps=0.0,
                            profit_horizon_us=1_000_000, workers=a.workers)
    print(f"samples: {len(samples):,}")
    F = build_features(samples, Path(a.data_root), deep=(a.problem == 2))
    print(f"features: {len(F):,} rows x {len(feats)} columns")

    if a.model == "gbt":
        model = make_gbt().fit(F[feats], F["y_bps"])
        joblib.dump({"model": model, "feats": feats, "problem": a.problem}, out)
    else:
        scaler, net = nn_fit(F, feats)
        joblib.dump({"net": net, "scaler": scaler, "feats": feats, "risk": NN_RISK, "problem": a.problem}, out)
    print(f"saved {out}")


if __name__ == "__main__":
    main()
