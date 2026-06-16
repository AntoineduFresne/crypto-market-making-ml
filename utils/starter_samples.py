from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd


BID = 0
ASK = 1


@dataclass(frozen=True)
class SampleConfig:
    market_data_root: Path = Path("/market_data")
    labels: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    target_label: str = "SOLUSDT"
    source_suffix: str = "tokyo"
    start_date: str = "20260201"
    end_date: str = "20260430"
    latency_us: int = 10_000
    select_window_us: int = 100_000
    best_price_trades: bool = True
    rebate_bps: float = 0.0
    profit_horizon_us: int = 1_000_000
    workers: int = 8


def iter_dates(start: str, end: str):
    current = datetime.strptime(start, "%Y%m%d").date()
    last = datetime.strptime(end, "%Y%m%d").date()
    while current <= last:
        yield current.strftime("%Y%m%d")
        current += timedelta(days=1)


@lru_cache(maxsize=None)
def _source_dir(root: str, label: str, source_suffix: str, probe_day: str) -> Path:
    short_layout_dir = Path(root) / label
    if (short_layout_dir / f"{probe_day}_trades.parquet").exists():
        return short_layout_dir

    matches: list[Path] = []
    for top_dir in Path(root).iterdir():
        if not top_dir.is_dir():
            continue
        try:
            children = list(top_dir.iterdir())
        except PermissionError:
            continue
        for child in children:
            if not child.is_dir() or not child.name.startswith(label) or child.name.endswith("-L0"):
                continue
            if list(child.glob(f"clean_*_{source_suffix}_{probe_day}_trades_lz4.parquet")):
                matches.append(child)
    if len(matches) != 1:
        raise RuntimeError(f"expected one clean source directory for {label}, got {len(matches)}")
    return matches[0]


def md_path(label: str, day: str, kind: str, config: SampleConfig) -> Path:
    source_dir = _source_dir(
        str(config.market_data_root),
        label,
        config.source_suffix,
        config.start_date,
    )
    short_layout_path = source_dir / f"{day}_{kind}.parquet"
    if short_layout_path.exists():
        return short_layout_path

    matches = sorted(source_dir.glob(f"clean_*_{config.source_suffix}_{day}_{kind}_lz4.parquet"))
    if len(matches) != 1:
        raise RuntimeError(f"expected one clean file for {label} {day} {kind}, got {len(matches)}")
    return matches[0]


def read_day(day: str, config: SampleConfig) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    books, trades = {}, {}
    for label in config.labels:
        book = pd.read_parquet(
            md_path(label, day, "orderbook_10", config),
            columns=["server_time", "bid_price_1", "ask_price_1"],
        )
        trade = pd.read_parquet(
            md_path(label, day, "trades", config),
            columns=["server_time", "aggressor_dir", "price", "volume"],
        )

        book = book[
            (book["server_time"] > 0)
            & (book["bid_price_1"] > 0)
            & (book["ask_price_1"] > book["bid_price_1"])
        ].sort_values("server_time", kind="mergesort")
        trade = trade[
            (trade["server_time"] > 0)
            & (trade["price"] > 0)
            & (trade["volume"] > 0)
        ].sort_values("server_time", kind="mergesort")

        books[label] = book.reset_index(drop=True)
        trades[label] = trade.reset_index(drop=True)
    return books, trades


def add_asof_mid(
    samples: pd.DataFrame,
    book: pd.DataFrame,
    left_col: str,
    out_col: str,
) -> pd.DataFrame:
    mid = book.assign(mid=(book["bid_price_1"] + book["ask_price_1"]) / 2)[
        ["server_time", "mid"]
    ]
    return pd.merge_asof(
        samples.sort_values(left_col, kind="mergesort"),
        mid.rename(columns={"server_time": f"{out_col}_time", "mid": out_col}),
        left_on=left_col,
        right_on=f"{out_col}_time",
        direction="backward",
    )


def build_day_samples(day: str, config: SampleConfig) -> pd.DataFrame:
    books, trades_by_label = read_day(day, config)
    sol_book = books[config.target_label]
    trades = trades_by_label[config.target_label].copy()

    trades["dir"] = np.where(trades["aggressor_dir"].astype(int) == BID, "ask", "bid")
    trades["events_before_server_time"] = trades["server_time"].astype("int64") - config.latency_us

    prior_book = sol_book.rename(
        columns={
            "server_time": "prior_book_time",
            "bid_price_1": "prior_bid_price_1",
            "ask_price_1": "prior_ask_price_1",
        }
    )
    trades = pd.merge_asof(
        trades.sort_values("events_before_server_time", kind="mergesort"),
        prior_book,
        left_on="events_before_server_time",
        right_on="prior_book_time",
        direction="backward",
    ).dropna(subset=["prior_bid_price_1", "prior_ask_price_1"])

    if not config.best_price_trades:
        deeper_bid = (trades["dir"] == "bid") & (trades["price"] < trades["prior_bid_price_1"])
        deeper_ask = (trades["dir"] == "ask") & (trades["price"] > trades["prior_ask_price_1"])
        trades = trades[deeper_bid | deeper_ask].copy()

    trades["bucket"] = trades["server_time"].astype("int64") // config.select_window_us
    samples = (
        trades.sort_values("server_time", kind="mergesort")
        .drop_duplicates(["dir", "bucket"], keep="first")
        .reset_index(drop=True)
    )

    samples["future_time"] = samples["server_time"].astype("int64") + config.profit_horizon_us
    future_book = sol_book.rename(
        columns={
            "server_time": "future_book_time",
            "bid_price_1": "future_bid_price_1",
            "ask_price_1": "future_ask_price_1",
        }
    )
    samples = pd.merge_asof(
        samples.sort_values("future_time", kind="mergesort"),
        future_book,
        left_on="future_time",
        right_on="future_book_time",
        direction="forward",
    ).dropna(subset=["future_bid_price_1", "future_ask_price_1"])

    raw_profit = np.where(
        samples["dir"] == "bid",
        (samples["future_bid_price_1"] - samples["price"]) / samples["price"],
        (samples["price"] - samples["future_ask_price_1"]) / samples["price"],
    )
    samples["profit_1s_with_rebate"] = raw_profit + config.rebate_bps / 10_000.0

    samples = add_asof_mid(samples, sol_book, "events_before_server_time", "sol_mid_at_cutoff")
    samples["one_second_before_cutoff"] = (
        samples["events_before_server_time"] - config.profit_horizon_us
    )
    samples = add_asof_mid(samples, sol_book, "one_second_before_cutoff", "sol_mid_1s_before_cutoff")
    samples["sol_mid_move_1s_bps"] = (
        (samples["sol_mid_at_cutoff"] - samples["sol_mid_1s_before_cutoff"])
        / samples["sol_mid_1s_before_cutoff"]
        * 10_000.0
    )

    samples["date"] = day
    out = samples[
        [
            "date",
            "server_time",
            "events_before_server_time",
            "dir",
            "price",
            "volume",
            "profit_1s_with_rebate",
            "sol_mid_at_cutoff",
            "sol_mid_1s_before_cutoff",
            "sol_mid_move_1s_bps",
        ]
    ].dropna().reset_index(drop=True)
    out["sample_id"] = config.target_label + ":" + day + ":" + out.index.astype(str).str.zfill(9)
    return out[
        [
            "sample_id",
            "date",
            "server_time",
            "events_before_server_time",
            "dir",
            "price",
            "volume",
            "profit_1s_with_rebate",
            "sol_mid_at_cutoff",
            "sol_mid_1s_before_cutoff",
            "sol_mid_move_1s_bps",
        ]
    ]


def build_samples(
    *,
    market_data_root: Path = Path("/market_data"),
    labels: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT"),
    target_label: str = "SOLUSDT",
    source_suffix: str = "tokyo",
    start_date: str = "20260201",
    end_date: str = "20260430",
    latency_us: int = 10_000,
    select_window_us: int = 100_000,
    best_price_trades: bool = True,
    rebate_bps: float = 0.0,
    profit_horizon_us: int = 1_000_000,
    workers: int = 8,
) -> pd.DataFrame:
    config = SampleConfig(
        market_data_root=market_data_root,
        labels=labels,
        target_label=target_label,
        source_suffix=source_suffix,
        start_date=start_date,
        end_date=end_date,
        latency_us=latency_us,
        select_window_us=select_window_us,
        best_price_trades=best_price_trades,
        rebate_bps=rebate_bps,
        profit_horizon_us=profit_horizon_us,
        workers=workers,
    )
    if config.workers <= 0:
        raise ValueError("workers must be positive")

    days = list(iter_dates(config.start_date, config.end_date))
    workers = min(config.workers, len(days))

    if workers == 1:
        frames: dict[str, pd.DataFrame] = {}
        total_days = len(days)
        for done, day in enumerate(days, start=1):
            day_samples = build_day_samples(day, config)
            frames[day] = day_samples
            print(
                f"[{done}/{total_days} {done / total_days:.1%}] "
                f"{day} samples={len(day_samples):,}",
                flush=True,
            )
        return pd.concat([frames[day] for day in days], ignore_index=True)

    frames: dict[str, pd.DataFrame] = {}
    total_days = len(days)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(build_day_samples, day, config): day for day in days}
        for done, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            day = futures[future]
            day_samples = future.result()
            frames[day] = day_samples
            print(
                f"[{done}/{total_days} {done / total_days:.1%}] "
                f"{day} samples={len(day_samples):,}",
                flush=True,
            )

    return pd.concat([frames[day] for day in days], ignore_index=True)


def toy_strategy_stats(samples: pd.DataFrame, threshold_bps: float = 0.0) -> pd.DataFrame:
    data = samples.copy()
    price_diff_bps = data["sol_mid_move_1s_bps"]
    select_bid = (data["dir"] == "bid") & (price_diff_bps > threshold_bps)
    select_ask = (data["dir"] == "ask") & (price_diff_bps < -threshold_bps)
    data["toy_selected"] = select_bid | select_ask
    data["month"] = data["date"].str[:6]

    def selected_stats(group: pd.DataFrame) -> pd.Series:
        selected = group[group["toy_selected"]]
        profit = selected["profit_1s_with_rebate"]
        return pd.Series(
            {
                "samples": len(group),
                "selected": len(selected),
                "sum_profit_1s": profit.sum(),
                "avg_bps": profit.mean() * 10_000.0 if len(profit) else 0.0,
                "p50_bps": profit.quantile(0.50) * 10_000.0 if len(profit) else 0.0,
                "p90_bps": profit.quantile(0.90) * 10_000.0 if len(profit) else 0.0,
            }
        )

    return data.groupby("month").apply(selected_stats).reset_index()


def monthly_profit_summary(samples: pd.DataFrame) -> pd.DataFrame:
    data = samples.copy()
    data["month"] = data["date"].str[:6]
    rows = []
    for month, group in data.groupby("month", sort=True):
        profit = group["profit_1s_with_rebate"]
        count = len(group)
        zero_count = int((profit == 0.0).sum())
        positive_count = int((profit > 0.0).sum())
        negative_count = int((profit < 0.0).sum())
        rows.append(
            {
                "month": month,
                "count": count,
                "sum_total": profit.sum(),
                "sum_positive": profit[profit > 0.0].sum(),
                "zero_pct": zero_count / count * 100.0,
                "positive_pct": positive_count / count * 100.0,
                "negative_pct": negative_count / count * 100.0,
            }
        )
    return pd.DataFrame(rows)
