# Crypto Market-Making ML Task

## Brief

You are given market data for three instruments (`BTCUSDT`, `ETHUSDT`,
`SOLUSDT`) over three months. The goal is to build a classifier that decides
which SOLUSDT maker trades should be accepted. For every candidate trade, the
model is given the trade side, price, volume, and only market data that would
have been available before the trade after a fixed latency. The objective is to
select trades with the highest total 1-second maker-side profit.

The public period is `2026-02-01` through `2026-04-30`. The final solution will
be evaluated on a hidden test period of length one month with the same data
format. The main evaluation metric is total 1-second profit over all selected
samples. We will also look at secondary diagnostics such as average profit,
profit distribution, and number of selected samples, but these are less
important than total selected profit.

## Working Notes

You are free to use whatever helps you work effectively: IDEs, libraries,
notebooks, command-line tools, LLMs, or chat assistants. The choice of tooling
is not what we are evaluating; we are more interested in your reasoning.

Please include a short writeup with your code. A few paragraphs is enough:
summarize your path to the final approach, the alternatives you considered, the
tradeoffs that mattered, and any data patterns that influenced your choices. If
you used an LLM extensively, you can include chat transcripts, but they are not
required.

## Instructions

Download the market data and open the starter notebook:

```bash
./copy_data.sh
.venv/bin/jupyter notebook notebooks/starter_samples.ipynb
```

`copy_data.sh` creates `.venv`, installs `requirements.txt`, runs the Python
downloader from that environment, verifies the archive SHA-256 hash, and unpacks
the data into `data/market_data`.

The data script downloads the archive with parallel Python HTTP range requests,
using 8 workers and 64 MiB chunks by default. The market data is about 15 GB
compressed and about 21 GB after unpacking.

If the download is interrupted, run the same command again. Completed chunks are
kept under `data/.download_parts/` and reused on the next run.

After successful unpacking, the compressed archive is deleted. Use
`./copy_data.sh --keep-archive` to keep it.

Unpacking requires `zstd` and `tar`.

## Data Format

After unpacking, files are organized as:

```text
data/market_data/{SYMBOL}/{YYYYMMDD}_orderbook_10.parquet
data/market_data/{SYMBOL}/{YYYYMMDD}_trades.parquet
```

`orderbook_10.parquet` contains top-10 book states:

- `server_time`, `symbol`, `symbol_id`
- for levels `1..10`: `bid_price_i`, `bid_size_i`, `ask_price_i`, `ask_size_i`

`trades.parquet` contains public trades:

- `server_time`, `symbol`, `symbol_id`
- `aggressor_dir`: `0 = Bid` aggressor, `1 = Ask` aggressor
- `price`, `volume`

The files intentionally do not contain receive timestamps. Use `server_time`
only.

## Samples And Profit

The starter notebook generates SOLUSDT samples from trades. By default:

- `LATENCY_US = 10_000`
- `SELECT_WINDOW_US = 100_000`
- `BEST_PRICE_TRADES = True`
- `REBATE_BPS = 0.0`
- `PROFIT_HORIZON_US = 1_000_000`

For a SOLUSDT trade at time `T`, the sample has:

- `server_time = T`
- `events_before_server_time = T - LATENCY_US`
- `dir`: maker side, `bid` or `ask`
- `price`
- `volume`
- `profit_1s_with_rebate`

The maker side is the opposite side of the trade aggressor:

```text
maker ask if aggressor_dir == Bid
maker bid if aggressor_dir == Ask
```

Samples are the first SOLUSDT trade in each `SELECT_WINDOW_US` bucket per maker side.
Features for a sample may use only events with:

```text
event.server_time <= events_before_server_time
```

The 1-second profit target is:

```text
maker bid: (bid_price_1(T + 1s) - price) / price + REBATE_BPS / 10000
maker ask: (price - ask_price_1(T + 1s)) / price + REBATE_BPS / 10000
```

`bid_price_1(T + 1s)` and `ask_price_1(T + 1s)` are taken from the first
SOLUSDT orderbook row with `server_time >= T + 1s`.

Profit is a normalized return. One basis point is:

```text
1 bps = 0.01% = 0.0001
```

So `profit_1s_with_rebate = 0.0001` means `+1 bps`, and
`profit_1s_with_rebate = -0.0001` means `-1 bps`.

## Notebook

Start with:

```text
notebooks/starter_samples.ipynb
```

The notebook:

- sets the main sample-generation parameters as global variables
- builds samples in parallel
- prints monthly sample/profit summaries
- plots the profit distribution
- shows a toy one-feature strategy

You can use the notebook as a starting point, but the final solution can be any
Python code that trains a model and produces predictions for the hidden test
period.

## Task

1. Train a model that decides which SOLUSDT samples to select. A sample is
   selected when your model output is positive. Maximize:

```text
sum(profit_1s_with_rebate of selected samples)
```

2. Extension: repeat the task with best-price trades filtered out. In the
   starter notebook set:

```python
BEST_PRICE_TRADES = False
```

This keeps only trades strictly deeper than the best price observed at
`events_before_server_time`. Train and evaluate a model for this stricter sample
set as well.

During final evaluation, the same sample-generation logic will be applied to a
hidden one-month test period. The main metric is total 1-second profit over all
selected samples. Average profit, profit distribution, and number of selected
samples are additional diagnostics.
