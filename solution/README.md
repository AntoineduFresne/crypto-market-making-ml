# solution/

The four trained models, saved by the last cell of `notebooks/starter_samples.ipynb`.
A sample is selected when the model output is positive.

| file | problem | model | role |
|---|---|---|---|
| `p1_gbt.joblib` | 1 (all fills) | HistGradientBoostingRegressor | **the hand-in** |
| `p1_nn.joblib` | 1 | sklearn MLPRegressor + scaler | sanity check |
| `p2_gbt.joblib` | 2 (deep fills) | HistGradientBoostingRegressor | comparison |
| `p2_nn.joblib` | 2 | sklearn MLPRegressor + scaler | **the hand-in** |

Each file is a dict. Trees: `{"model", "feats"}`. Nets: `{"net", "scaler", "feats", "risk"}`.

Loading and predicting with the trees:

```python
import joblib
b = joblib.load("solution/p1_gbt.joblib")
pred = b["model"].predict(X[b["feats"]])   # X = features built like the notebook's _feat_day
take = pred > 0
```

For the nets: apply `log1p` to the `risk` columns, then `scaler.transform`, then NaN -> 0, exactly
as in the notebook's `_nn_X`, and select on `net.predict(...) > 0`.

The 17 problem-1 features are built by `_feat_day` in the notebook (used by both problems).
Problem 2 adds two hidden-liquidity columns, `repl_opp` and `exc_my`, built by `_hb_day` in the
iceberg cell of section 2.1 (`exc_my` is stored as log1p there; `repl_opp` stays raw). So for p2:
build the 17 with `_feat_day`, the 2 with `_hb_day`, and give each model the columns named in its
`feats` list. Every feature reads only events at or before each fill's cutoff (fill time minus
the 10 ms latency).
