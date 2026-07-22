"""Feature builders for the standalone pipeline. Exact ports of the notebook's builders
(_feat_day and _hb_day): same reads, same causality (every feature uses only events with
server_time <= the fill's cutoff), same NaN handling. The notebook cell 'pipeline check'
verifies column-by-column equality against the notebook's training table."""
from pathlib import Path

import numpy as np
import pandas as pd

FEATS = ["imb1", "dimb", "devavg", "btc", "eth", "btc_dev", "eth_dev",                      # direction
         "vol", "absmove", "rate", "spread", "depth", "flips", "absdispq", "btc_amv", "eth_amv", "supp"]  # risk
FEATS_DEEP = FEATS + ["repl_opp", "exc_my"]
NN_RISK = ["vol", "absmove", "rate", "spread", "flips", "absdispq", "btc_amv", "eth_amv"]

SAMPLE_COLS = ["sample_id", "date", "events_before_server_time", "dir", "price", "volume",
               "sol_mid_move_1s_bps", "profit_1s_with_rebate"]


def load_book(root: Path, sym: str, day: str, size_levels: int = 1, price_levels: int = 1) -> pd.DataFrame:
    cols = ["server_time", "bid_price_1", "ask_price_1"]
    cols += [f"bid_size_{i}" for i in range(1, size_levels + 1)]
    cols += [f"ask_size_{i}" for i in range(1, size_levels + 1)]
    cols += [f"bid_price_{i}" for i in range(2, price_levels + 1)]
    cols += [f"ask_price_{i}" for i in range(2, price_levels + 1)]
    b = pd.read_parquet(Path(root) / f"{sym}/{day}_orderbook_10.parquet", columns=list(dict.fromkeys(cols)))
    return b[(b.server_time > 0) & (b.bid_price_1 > 0) & (b.ask_price_1 > b.bid_price_1)
             & (b.ask_price_1 < 4 * b.bid_price_1)].sort_values("server_time", kind="mergesort")


def load_trades(root: Path, sym: str, day: str) -> pd.DataFrame:
    tr = pd.read_parquet(Path(root) / f"{sym}/{day}_trades.parquet",
                         columns=["server_time", "aggressor_dir", "price", "volume"])
    return tr[(tr.server_time > 0) & (tr.price > 0) & (tr.volume > 0)].sort_values("server_time", kind="mergesort")


def asof(times, vals, cutoffs, lag_us: int = 0):
    idx = np.searchsorted(times, np.asarray(cutoffs) - lag_us, side="right") - 1
    out = np.full(len(idx), np.nan)
    ok = idx >= 0
    out[ok] = np.asarray(vals)[idx[ok]]
    return out


def side_sign(dir_series):
    return np.where(np.asarray(dir_series) == "bid", 1.0, -1.0)


def imb1_of(book: pd.DataFrame):
    b1, a1 = book.bid_size_1.to_numpy(), book.ask_size_1.to_numpy()
    return (b1 - a1) / (b1 + a1)


def feat_day(root: Path, day: str, s: pd.DataFrame) -> pd.DataFrame:
    """The 17 problem-1 features for one day's samples (exact port of the notebook's _feat_day)."""
    c = s["events_before_server_time"].to_numpy()
    sgn = side_sign(s["dir"])
    bk = load_book(root, "SOLUSDT", day, size_levels=10, price_levels=10)
    with np.errstate(invalid="ignore", divide="ignore"):
        imb_a = imb1_of(bk)
    bt = bk.server_time.to_numpy()
    cur = asof(bt, imb_a, c)
    prev = asof(bt, imb_a, c, 1_000_000)
    bid, ask = bk.bid_price_1.to_numpy(), bk.ask_price_1.to_numpy()
    mid = (bid + ask) / 2
    ihi = np.searchsorted(bt, c, "right")
    ilo = np.searchsorted(bt, c - 1_000_000, "right")
    S = np.concatenate([[0.0], np.nancumsum(imb_a)])
    Cn = np.concatenate([[0.0], np.cumsum(~np.isnan(imb_a))])
    nn_w = Cn[ihi] - Cn[ilo]
    avg = np.where(nn_w > 0, (S[ihi] - S[ilo]) / np.maximum(nn_w, 1), np.nan)
    flip = np.concatenate([[0.0], (np.diff(mid) != 0).astype(float)])
    Fc = np.concatenate([[0.0], np.cumsum(flip)])
    flips = (Fc[ihi] - Fc[ilo]).astype(float)
    flips[ihi == 0] = np.nan
    q_same = np.where(sgn > 0, asof(bt, bid, c), asof(bt, ask, c))
    PB = [f"bid_price_{i}" for i in range(1, 11)]
    SB = [f"bid_size_{i}" for i in range(1, 11)]
    PA = [f"ask_price_{i}" for i in range(1, 11)]
    SA = [f"ask_size_{i}" for i in range(1, 11)]
    idx = np.maximum(ihi - 1, 0)
    okb = ihi > 0
    Pc = s["price"].to_numpy()[:, None]
    bp = bk[PB].to_numpy()[idx]
    bsz = bk[SB].to_numpy()[idx]
    apx = bk[PA].to_numpy()[idx]
    asz = bk[SA].to_numpy()[idx]
    supp = np.where(sgn > 0, np.where(bp <= Pc, bsz, 0.0).sum(1), np.where(apx >= Pc, asz, 0.0).sum(1))
    supp[~okb] = np.nan
    cross = {}
    for nm, sym in (("btc", "BTCUSDT"), ("eth", "ETHUSDT")):
        xb = load_book(root, sym, day, size_levels=1)
        xt = xb.server_time.to_numpy()
        with np.errstate(invalid="ignore", divide="ignore"):
            xi = imb1_of(xb)
        cross[nm] = asof(xt, xi, c)
        Sx = np.concatenate([[0.0], np.nancumsum(xi)])
        Cx = np.concatenate([[0.0], np.cumsum(~np.isnan(xi))])
        xhi = np.searchsorted(xt, c, "right")
        xlo = np.searchsorted(xt, c - 1_000_000, "right")
        xw = Cx[xhi] - Cx[xlo]
        cross[nm + "_dev"] = cross[nm] - np.where(xw > 0, (Sx[xhi] - Sx[xlo]) / np.maximum(xw, 1), np.nan)
        xmid = (xb.bid_price_1.to_numpy() + xb.ask_price_1.to_numpy()) / 2
        cross[nm + "_amv"] = np.abs(asof(xt, xmid, c) - asof(xt, xmid, c, 1_000_000)) / asof(xt, xmid, c) * 1e4
    tt = load_trades(root, "SOLUSDT", day).server_time.to_numpy()
    rate = (np.searchsorted(tt, c, "right") - np.searchsorted(tt, c - 1_000_000, "right")).astype(float)
    ret = s["profit_1s_with_rebate"].to_numpy()
    dt = s["date"].to_numpy()
    return pd.DataFrame({"sample_id": s["sample_id"].to_numpy(),
                         "imb1": (cur * sgn).astype(np.float32), "dimb": ((cur - prev) * sgn).astype(np.float32),
                         "devavg": ((cur - avg) * sgn).astype(np.float32),
                         "btc": (cross["btc"] * sgn).astype(np.float32), "eth": (cross["eth"] * sgn).astype(np.float32),
                         "btc_dev": (cross["btc_dev"] * sgn).astype(np.float32),
                         "eth_dev": (cross["eth_dev"] * sgn).astype(np.float32),
                         "vol": s["volume"].to_numpy().astype(np.float32),
                         "absmove": np.abs(s["sol_mid_move_1s_bps"].to_numpy()).astype(np.float32),
                         "rate": rate.astype(np.float32),
                         "spread": (asof(bt, (ask - bid) / mid, c) * 1e4).astype(np.float32),
                         "depth": np.log1p(asof(bt, bk.bid_size_1.to_numpy() + bk.ask_size_1.to_numpy(), c)).astype(np.float32),
                         "flips": flips.astype(np.float32),
                         "supp": np.log1p(supp).astype(np.float32),
                         "absdispq": (np.abs(s["price"].to_numpy() - q_same) / asof(bt, mid, c) * 1e4).astype(np.float32),
                         "btc_amv": cross["btc_amv"].astype(np.float32), "eth_amv": cross["eth_amv"].astype(np.float32),
                         "y_bps": (ret * 1e4).astype(np.float32), "y_ret": ret.astype(np.float32),
                         "month": np.array([d[:6] for d in dt]), "date": dt})


def hb_day(root: Path, day: str, s: pd.DataFrame) -> pd.DataFrame:
    """The two hidden-liquidity columns for one day (exact port of the notebook's _hb_day)."""
    c = s["events_before_server_time"].to_numpy()
    sgn = side_sign(s["dir"])
    HB_SB = [f"bid_size_{i}" for i in range(1, 11)]
    HB_SA = [f"ask_size_{i}" for i in range(1, 11)]
    bk = load_book(root, "SOLUSDT", day, size_levels=10)
    bt = bk.server_time.to_numpy()
    Vb = bk[HB_SB].sum(axis=1).to_numpy()
    Va = bk[HB_SA].sum(axis=1).to_numpy()
    vb_c, vb_p = asof(bt, Vb, c), asof(bt, Vb, c, 1_000_000)
    va_c, va_p = asof(bt, Va, c), asof(bt, Va, c, 1_000_000)
    tr = load_trades(root, "SOLUSDT", day)
    tt = tr.server_time.to_numpy()
    tv = tr.volume.to_numpy()
    ad = tr.aggressor_dir.to_numpy()
    Cb = np.concatenate([[0.0], np.cumsum(np.where(ad == 1, tv, 0.0))])
    Ca = np.concatenate([[0.0], np.cumsum(np.where(ad == 0, tv, 0.0))])
    hi = np.searchsorted(tt, c, "right")
    lo = np.searchsorted(tt, c - 1_000_000, "right")
    cons_b, cons_a = Cb[hi] - Cb[lo], Ca[hi] - Ca[lo]
    repl_b = vb_c - vb_p + cons_b
    repl_a = va_c - va_p + cons_a
    exc_b = np.maximum(0.0, cons_b - vb_p)
    exc_a = np.maximum(0.0, cons_a - va_p)
    bid = sgn > 0
    return pd.DataFrame({"sample_id": s["sample_id"].to_numpy(),
                         "repl_opp": np.where(bid, repl_a, repl_b).astype(np.float32),
                         "exc_my": np.log1p(np.where(bid, exc_b, exc_a)).astype(np.float32)})


def build_features(samples: pd.DataFrame, market_data_root: Path, deep: bool = False) -> pd.DataFrame:
    """Features for a samples table (any date range). deep=True adds the two hidden-liquidity columns."""
    samp_by_day = dict(tuple(samples[SAMPLE_COLS].groupby("date", sort=True)))
    F = pd.concat([feat_day(market_data_root, d, samp_by_day[d]) for d in sorted(samp_by_day)], ignore_index=True)
    if deep:
        hb = pd.concat([hb_day(market_data_root, d, samp_by_day[d]) for d in sorted(samp_by_day)], ignore_index=True)
        assert (hb["sample_id"].to_numpy() == F["sample_id"].to_numpy()).all(), "sample_id misalignment"
        F["repl_opp"] = hb["repl_opp"].to_numpy()
        F["exc_my"] = hb["exc_my"].to_numpy()
    return F
