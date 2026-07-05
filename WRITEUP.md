# Writeup

Location: The whole solution is `notebooks/starter_samples.ipynb`; its first cell is a
map with the final numbers. `JOURNAL.md` is the formal background I wrote while learning the domain
(the order book, the matching engine, what this dataset really contains). `solution/` holds the four
trained models. This file is an explanation (in short) of what I did in the notebooks.

Introduction: I came in new to market making, so I first wrote down, bottom-up, what an order book
and a matching engine are, and what the data gives us (that is JOURNAL.md). Then I worked in the
notebook from the ground up: fixed the notation and a small shared toolbox, checked the data (the
book updates on a ~53 ms strobe, the trades are event-driven, a few sentinel rows need one filter),
built the baselines (accept-all loses -589 over the 89 days, the oracle makes +1162), and looked at
where the losses live (no single fat outlier; the worst 5% of fills carry about half the loss).
After that I hunted features in blocks, each with the same shape: a motivation, a code cell that
prints the evidence, an interpretation.

For the features construction, everything is causal: a feature reads only events at or before the
cutoff (the fill time minus the 10 ms latency). I used two screens. For direction features (signed 
by my side): the partial correlation with profit given what I already have, which separates new 
information from an echo of an existing feature. For risk features (unsigned): the partial correlation
of |profit| given the risk features I already have. The rule is stated in the notebook and applied
everywhere: a feature that adds something genuine goes into the model and the trees weight it; only
echoes and zeros stay out. The final set has 17 features: 7 direction (the level-1 book imbalance, 
its 1 s momentum, its deviation from the 1 s average, and the BTC/ETH versions of the level and the
deviation) and 10 risk (fill size, |move|, trade rate, spread, depth, quote flips, the fill's print
past the stale quote, BTC/ETH |move|, and the support resting behind the fill price). Two data
patterns mattered a lot: cross-asset returns do not lead SOL (they co-move at lag 0), but cross-asset
book state and cross-asset |move| do add; and risk features pay as a group, not one by one, so I
tested baskets, not only singles. The dead ends (trade flow, deeper book imbalance, cross-asset return
and momentum, and a dozen more) are kept in the notebook with the numbers explaining why I didn't
kept them, because the search is part of the answer.

One gradient boosted trees regressor: predict the 1 s profit, take a fill iff the
prediction is positive (the cut at zero is the objective). Validation is month-out:
train two months, test the third, in both directions, because I bet the hidden month is Jan or May.
Held-out April: +63.52 of summed return, about 34% of the oracle. Held-out February: +110.65, about 18%.
A small neural net trained on the same features (with a temporal validation slice for early stopping)
lands just under the trees (+62.51 / +108.33). This is great since the value is in the features and not
in the fitter. I hand-in for problem 1 the gradient boosted trees.

Warning: the feature screens use all three months, on purpose, because the hidden month is outside of
these 3 month in the real test and I want the feature set chosen on all the data I have; the cost is
that the two totals above are then probably optimistic as estimates of a new month. 

For the problem 2 (deep fills): These fills only exist when an aggressor sweeps through the best level,
so the informed order already acted. On this set the direction features die (even deeper book
imbalance does not help), the risk features get stronger, and the expected profit is negative almost
everywhere: taking everything loses -132.76 over the period and even the oracle only makes +110.07.
I tried a model score carried over from problem 1, reversal signals, the hour of the day, and
hyperparameter grids (the rescue cell in the notebook runs all of them); nothing makes the held-out
months positive. I also looked for hidden liquidity (iceberg), the footprint exists on about 1% of
these fills, and two of its columns carry a little size information, so they join the deep model
(nineteen features there). The models converge to skipping almost everything (i.e. the no trade strategy). 
The net ends at 0.00 / -0.00 on the held-out months and is the hand-in for problem 2. (Is a
positive total actually achievable on that set, or is near-total abstention the expected answer?)

FYI on the tooling: I worked with an LLM (Claude) throughout, as the README allows. I set the questions,
the screens, the notation and the final calls; it ran the searches, wrote the
plumbing, and double-checked the numbers. It also suggested many of the metrics I test with, and it
gave me intuition about what they mean. Still, more than once I overruled it and the data sided with
me (for example some features it wanted to drop are in the final model). Every number in the notebook is
backed by a cell output (the journal's data-audit numbers I checked directly against the raw files),
and every fit is seeded, so a re-run gives back the same numbers. A full run of the notebook takes about
two hours on my laptop once the data is downloaded (most of that is the feature builds and the model fits).
