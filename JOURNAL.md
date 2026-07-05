# Journal

A bottom-up working journal: the problem as I understand it, formalized, and the
reasoning behind.

---

A bit of honesty: the start was a steep learning curve for me. I came in as a
"mathematician" with no background in market making, so order books, mark-outs, adverse
selection, maker vs aggressor were all new, and it took me some reading before I understood
the problem and the repository code. I also had a few other things on my plate in parallel,
so I got going a little slower than I'd hoped — but (!) I've genuinely enjoyed it.

---

# 1. Context

First a bit of context. Trading, abstractly, is a stream of **orders** (temporally ordered)
arriving at an **exchange** and being **filled** over time. Heuristically: an order is a
single instruction to buy or sell — a size, a price, and which way; the exchange is the one
venue every such instruction is sent to; and an order *fills* when it meets one going the
other way, so a trade happens. The exchange, for our purposes, *is* one machine: the
**matching engine** — a single deterministic program, the one computer every order must
physically reach, and the sole authority on the state of the market. There is exactly one of
it, and the same orders arriving in the same order always produce the same outcome. All we
ever have are orders and the times at which they arrived. Everything is finite, obviously: it
all has to fit in a computer.

## 1.1. The asset, its quantity and price

Let's fix an asset `Asset`, i.e. something we trade: an apple, a bitcoin, an ETF share, an
ounce of gold. It comes in a smallest tradable unit $\delta>0$, its **lot** (1 apple; for BTC
an exchange-set step like `0.00001`; one ETF share; for gold a fixed weight or contract).
Every quantity is a positive integer number of lots, so size lives on a grid: $\delta\mathbb{N}$.

That's really all the engine needs to know about the asset. In particular it has no notion
of world supply: it never checks how much gold exists, only the sizes actually submitted to
it. So nothing stops me, in principle, from sending an order for more gold than exists. What
actually caps order size lives *outside* matching: (i) an account check, where my broker
verifies I can really deliver (I hold the asset, or the cash/margin); and (ii) exchange risk
limits (a max-order-size cap, price bands). Neither is about world supply.

Different people value one unit $\delta$ of `Asset` differently: value is subjective. Nothing
is traded yet, nor even sent to the engine. Still, any price must be a multiple of the
exchange's tick size $\rho>0$, a minimum price increment in the **quote currency** (CHF, USD,
…), since you cannot have arbitrary precision on money. So like quantity, the price lives on
a grid: $\rho\mathbb{N}$.

## 1.2. Making an order

An order is the atom: a single instruction I send to the exchange. I first choose three things:
- a **side** — a **bid** is a request to buy, an **ask** (offer) a request to sell;
- a **size** $q\in\delta\mathbb{N}$ — a positive integer number of **lots**;
- a **price** $p\in\rho\mathbb{N}$ — what I'm willing to pay (bid) or accept (ask) per unit.

When I send the order to the engine, I also attach
- my **tag** $c$ — my **client order id**: a label I pick and stick on the order. The engine
  treats it as opaque (it never reads or interprets it), but it echoes $c$ back unchanged on
  every reply about that order: acknowledgement, fill, cancel. That's how I match the
  exchange's replies to what I sent: I compare tags by equality, nothing more.

A submitted order is exactly this tuple:
$$ o:=\langle\text{side},\, q,\, p,\, c\rangle \;\in\; \{\text{bid}, \text{ask}\} \times \delta\mathbb{N}_{>0} \times \rho\,\mathbb{N}_{>0} \times C,$$
with $C$ the opaque set of client labels (strings). Write $\text{side}$, $q$, $p$, $c$ for the
four coordinate projections: $q(o)$ the size, $p(o)$ the price, and so on.

## 1.3. Identity and time

Orders arrive physically at the exchange's engine and are processed **one by one, in order
of arrival**, never two at once: the engine takes whichever reaches it first (whoever hits
the microchip gateway first; this is literally why people pay to sit physically next to the
engine) and handles them one at a time.

The engine doesn't have to accept what is sent. It runs a validity check and rejects the
order if it fails. For example:

- **off-grid size or price**: $q$ not a positive multiple of the lot $\delta$, or $p$ not a
  multiple of the tick $\rho$;
- **out of bounds**: $p$ outside the exchange's price bands, or $q$ above the max-order-size cap;
- **account check fails**: not enough cash/margin to back a bid, or I don't hold (and can't
  short) the asset for an ask;
- **bad context**: unknown symbol, market closed / outside the session, or a malformed message.

If the engine doesn't accept the order, it bounces back with a reason, carrying my tag $c$ so
I know which one it was.

If the engine accepts the order, it attaches three fields:

- an **id** — a unique handle for *this* order, assigned by the engine and fixed for its
  whole life; an opaque key (in practice a 64-bit integer) that I only ever test for equality.

- a **sequence number** `seq`$\in\mathbb{N}$ — a single counter the engine advances by $+1$ for
  each order it processes, running across the whole **session** (one continuous run of the
  feed, typically a trading day). It does *not* restart when I disconnect and reconnect; I
  pick the numbering back up where I left off, which is exactly what lets me recover what I
  missed while away. It starts over only when a new session opens (the next trading day's
  feed). Its real purpose is gap detection: the exchange doesn't send updates one-to-one; it
  broadcasts them to everyone at once, like a radio station, and never checks that each was
  heard, so some get lost silently along the way. That's why every message carries `seq`: I
  expect the next to be `last + 1`, so a jump to `last + 2` tells me one was dropped, and I
  reconnect and ask for that message by its number. Without it, a lost update would vanish in
  silence and I'd never know my picture of the market was wrong. In short, `seq` is the
  order of arrival. Notice that two distinct orders can have the same timestamp but not the
  same sequence number.

- a **timestamp** $\tau\in\mathbb{N}$ — the engine's own wall-clock reading at the instant it
  processes the order, counted in whole µs from a fixed origin (the Unix epoch,
  1970-01-01 00:00 UTC). It is an absolute clock: it only moves forward and never restarts;
  in particular it does *not* reset at session boundaries the way `seq` does, because it is
  real calendar time. What I actually compute with are differences $\tau' - \tau$: durations
  in µs, which live in $\mathbb{Z}$, and on which every time-based computation runs: latency,
  mark-out horizons, anything clock-driven. In short, `τ` is the time of arrival.

An accepted order is the submission with the stamp appended:
$$\hat o = \langle \text{id},\ \tau,\ \text{seq}\rangle\frown o\;\in\; I \times \mathbb{N} \times \mathbb{N}\times\{\text{bid}, \text{ask}\} \times \delta\mathbb{N}_{>0} \times \rho\,\mathbb{N}_{>0} \times C,$$
with $I$ the (opaque, 64-bit) set of engine ids.

Acceptance is the (partial) map
$$\text{accept}\colon\ o \;\longmapsto\; \hat o = \langle \text{id},\ \tau,\ \text{seq}\rangle\frown o,$$
defined on the submissions that pass the check, filling in the three stamped fields: a fresh
`id` never reused, the timestamp $\tau$, and `seq` $=$ last $+\,1$. Because every accepted order
gets its own `id` and its own `seq`, the stamp makes $\hat o$ globally unique: no two accepted
orders agree on it.


## 1.4. The math behind "fill", and the book behind "rest"

Once my order $o$ is accepted and stamped to $\hat o$, the engine does two things, in this
order: it **fills** $\hat o$ against the orders already **resting**, then it **rests**
whatever is left. Both can happen to a single order: part fills immediately, the remainder
rests.

**The book.** The engine maintains, publicly at each step of `seq`, one object: the **order
book** $B$. It is a finite list,
$$B \in \mathcal{O}_{\mathrm{pub}}^{*}, \qquad
\mathcal{O}_{\mathrm{pub}} := I \times \mathbb{N} \times \mathbb{N} \times \{\mathrm{bid},\mathrm{ask}\} \times \delta\mathbb{N}_{>0} \times \rho\,\mathbb{N}_{>0}.$$

The four coordinate projections extend to public entries, so I write
$\mathrm{side}(r),\,q(r),\,p(r),\,\mathrm{seq}(r)$ for any $r\in\mathcal{O}_{\mathrm{pub}}$.

**The invariants.** **(I)** The entries of the list are ordered by strictly increasing `seq`.
**(II)** At every `seq` the book is uncrossed: writing
$$\text{best bid} := \sup\{p(B(i)) : i \in \mathrm{dom}(B),\ \mathrm{side}(B(i)) = \mathrm{bid}\} \in \{-\infty\} \cup \rho\,\mathbb{N}_{>0},$$
$$\text{best ask} := \inf\{p(B(i)) : i \in \mathrm{dom}(B),\ \mathrm{side}(B(i)) = \mathrm{ask}\} \in \{+\infty\} \cup \rho\,\mathbb{N}_{>0},$$
(with the standard conventions $\sup\varnothing = -\infty$ and $\inf\varnothing = +\infty$),
$$\text{best bid} \;<\; \text{best ask},$$
their positive gap being the **spread** = ($\text{best ask} - \text{best bid}$). The recursive
construction of the book below preserves both invariants.

**Recursion — base when $B = [\,]$.** The book is empty. When an order $o$ arrives at the engine
and is accepted and stamped, the book is updated to hold its public projection
$$\pi_{\mathrm{pub}}(\hat o) := \hat o|_6 = \langle \mathrm{id},\ \tau,\ \mathrm{seq},\ \mathrm{side},\ q,\ p\rangle,$$
the stamped order with its client tag $c$ projected away: the world sees the order and its
priority but not who sent it, and only the sender's private acknowledgements tie that `id`
back through $c$. With a single entry, (I) holds trivially, and only one side is populated, so
(II) holds through the $\pm\infty$ conventions.

**Recursion — step when $B$ is not empty.** Suppose $B = [\,r_0,\dots,r_k\,]$, $k\in\mathbb{N}$,
satisfies (I) and (II). Let
$o_{k+1}$ be the next order the engine accepts, stamped to $\hat o_{k+1}$. Since `seq` is a
single counter advanced by $+1$ on each acceptance, the new entry follows $r_k$ directly,
$$\mathrm{seq}(\hat o_{k+1}) = \mathrm{seq}(r_k) + 1.$$

*Crossing.* For each $i \le k$, the incoming order $\hat o_{k+1}$ and a resting one $r_i$ **cross**, written
$\hat o_{k+1} \bowtie r_i,$ when they sit on opposite sides at meeting prices:
$\big(\mathrm{side}(o_{k+1})=\mathrm{bid}\wedge\mathrm{side}(r_i)=\mathrm{ask}\wedge p(o_{k+1})\ge p(r_i)\big)\ \vee\ \big(\mathrm{side}(o_{k+1})=\mathrm{ask}\wedge\mathrm{side}(r_i)=\mathrm{bid}\wedge p(r_i)\ge p(o_{k+1})\big).$
Crossing only says that a trade between the two is possible, that they lie on opposite sides at
meeting prices. It fixes neither the quantity traded nor the price.

*The match.* Collect the crossing entries $C := \{\, r_i : \hat o_{k+1}\bowtie r_i \,\}$. They all sit on the side opposite $o_{k+1}$, we define (depending on which side $o_{k+1}$ is) the **price–time priority**: a single
total order on $C$, namely the lexicographic order on price, then on `seq`:
- if the side is bid (buys, against asks: so cheapest first):
$$r \preceq r' \iff p(r) < p(r') \ \vee\ \big(p(r)=p(r') \wedge \mathrm{seq}(r) < \mathrm{seq}(r')\big);$$
- if the side is ask (sells, against bids: so dearest first):
$$r \preceq r' \iff p(r) > p(r') \ \vee\ \big(p(r)=p(r') \wedge \mathrm{seq}(r) < \mathrm{seq}(r')\big).$$
Since $\preceq$ is a total order on the finite set $C$, it admits a unique strictly increasing
enumeration, the bijection $c \colon |C| \to C$ with
$c_0 \prec c_1 \prec \dots \prec c_{|C|-1}$.

The match walks down this list once, carrying for each $j\in|C|+1$ a state $\langle s_j, B_j, \Delta_j\rangle$ that records the
incoming size still unfilled, the book as it stands, and the saving so far. It
starts with
$$s_0 := q(o_{k+1}), \qquad B_0 := B, \qquad \Delta_0 := 0,$$
then for any $j \in |C|$ we define $m_j := \min\{\,s_j,\ q(c_j)\,\}$ (that is the lots we are taking from $c_j$). Each of those lots trades at $c_j$'s price $p(c_j)$ and never at $o_{k+1}$'s own price $p(o_{k+1})$, which the cross guarantees is at least as favorable to $o_{k+1}$. So the difference on every lot is money the sender of $o_{k+1}$ keeps. We define $$s_{j+1} := s_j - m_j, \qquad \Delta_{j+1} := \Delta_j + \big|\,p(o_{k+1}) - p(c_j)\,\big|\,m_j,$$
and it updates the book by reducing $c_j$ and dropping the entry only if it is empty,
$$B_{j+1} := \begin{cases} B_j \text{ with } c_j \text{ deleted that is when } m_j = q(c_j),\\[2pt] B_j \text{ with } c_j \text{ resized to } q(c_j) - m_j, \text{that is when }m_j < q(c_j).\end{cases}$$
Obviously, the recursion stays defined for every $j$ (for example once the unfilled size reaches $0$ the order $o_{k+1}$ is
full, and each later step clears $m_j = 0$ lots, so it leaves the size, the book, and the saving
untouched and runs harmlessly on to $j = |C|-1$).

After the final step the order has filled
$$f := q(o_{k+1}) - s_{|C|} = \sum_{j=0}^{|C|-1} m_j = \min\!\Big(q(o_{k+1}),\ \textstyle\sum_{r\in C} q(r)\Big)\ \text{lots},$$
the smaller of its own size and the total size of everything it crossed, and the engine returns the saving
$\Delta := \Delta_{|C|} \ge 0$ to the sender of $o_{k+1}$.

*Resting the remainder.* The unfilled residual is
$$q_{\mathrm{rest}} := q(o_{k+1}) - f = s_{|C|} \ \ge\ 0,$$
which is the whole order when nothing crossed ($f = 0$) and none of it on a full fill
($f = q(o_{k+1})$). When $q_{\mathrm{rest}} > 0$ the engine takes the public projection of the
incoming order, replaces its size by $q_{\mathrm{rest}}$,
$$r_{k+1} := \pi_{\mathrm{pub}}(\hat o_{k+1}) \ \text{resized to}\ q_{\mathrm{rest}},$$
and appends it, so $B':=B_{|C|} \frown \langle \,r_{k+1}\,\rangle$. When $q_{\mathrm{rest}} = 0$ it appends
nothing and $B':=B_{|C|}$.


*The invariants survive on $B'$.* Two facts hold no matter how the match plays out. First, each
step $j$ changes only $c_j$: it deletes $c_j$ when $m_j = q(c_j)$ and otherwise shrinks its size to
$q(c_j)-m_j>0$, in both cases leaving $c_j$'s `seq`, side and price untouched, and it never touches
any entry outside $C$. So $B_{|C|}$ is exactly $B$ with each member of $C$ deleted or shrunk and
everything else left in place: the same entries, in the same `seq` order, with the same sides and
prices, only some sizes smaller.

This implies by hpyothesis on B that both invariant hold for $B_{|C|}$: (I) holds because its entries are
entries of $B$ in their original order carrying their original `seq`s, so they are still strictly
increasing. (II) holds because a crossing pair in $B_{|C|}$ would be two entries of $B$ with
unchanged sides and prices, hence a crossing pair in $B$, which has none. (Crossing reads only
sides and prices, never sizes, so deleting or shrinking an entry can never create one.)

It remains to pass from $B_{|C|}$ to $B'$, and that splits on the residual.

**Case $q_{\mathrm{rest}} = 0$ (the order fills completely).** This case is trivial by definition of $B'$.

**Case $q_{\mathrm{rest}} > 0$ (some size is left to rest).** Here
$B' = B_{|C|} \frown \langle r_{k+1}\rangle$. A positive residual forces *every* step to have taken a
full bite: if some step had $m_{j^\ast} < q(c_{j^\ast})$, then
$m_{j^\ast} = \min\{s_{j^\ast}, q(c_{j^\ast})\} = s_{j^\ast}$, so $s_{j^\ast+1} = 0$ and, because (clearly) the sequence of $s_i$ is decreasing and non negative we get $s_{|C|} = 0$, contradicting $q_{\mathrm{rest}} > 0$. So every $c_j$ was consumed in
full and deleted, leaving $B_{|C|} = B \setminus C$, the entries of $B$ that do not cross
$o_{k+1}$. Now the two invariants on $B'$:

(I): every entry of $B_{|C|}$ keeps its `seq` $\le \mathrm{seq}(r_k)$, while $r_{k+1}$ is appended
with `seq` $= \mathrm{seq}(r_k)+1$, strictly above all of them, so the list stays strictly
increasing.

(II): $B_{|C|}$ is uncrossed (simply because $B$ is uncrossed), so any crossing pair in $B'$ must involve the new entry $r_{k+1}$ paired with some $r \in B_{|C|}$ so $r\notin C$. Since $r_{k+1}$ carries $o_{k+1}$'s side and price, $r$
crossing $r_{k+1}$ means $r$ crosses $o_{k+1}$, i.e. $r \in C$ a contradiction.

This concludes.

## 1.5. Some vocabulary

Now that the mechanics are fixed, the standard names attach cleanly: the incoming order $o_{k+1}$ that crosses and consumes resting size is the **aggressor** (or **taker**), the one who **emits** it the **emitter**; each resting order it trades against — the entries of $C$ — is a **maker**, said to have been **providing liquidity** at its price; the total size it could cross, $\sum_{r\in C} q(r)$, is the **depth** it met; and the saving $\Delta$ is its **price improvement**.


##
# 2. Our Situation

Section 1 built the engine in the abstract. Now we point it at the actual project. The data
lives in `data/market_data/{SYMBOL}/{YYYYMMDD}_{orderbook_10,trades}.parquet`, gets loaded by
`utils/starter_samples.py`, and is explored in `notebooks/starter_samples.ipynb`. The three
**instruments** (`BTCUSDT`, `ETHUSDT`, `SOLUSDT`) all trade on one centralized crypto-derivatives
**exchange** (the single matching engine of §1), and the `symbol` field gives away that they are
**perpetual futures** (`SOLUSDT-FUTURE`, …). The data never names the venue, but its conventions
and a `tokyo` tag on the source files point to **Binance** USDT-margined futures, whose engine
runs colocated in a Tokyo data centre. The ticker says how to read them:
`SOLUSDT` is SOL priced in **USDT** (Tether, a US-dollar stablecoin), which is the **quote
currency** of §1 here, the unit every price, and so every profit, is measured in. So the `Asset`
is one such futures contract, and "holding the asset" means carrying a futures position.
The data covers 89 days, `2026-02-01` to `2026-04-30`, one pair of files per coin per day.

We trade **`SOLUSDT` only**; `BTCUSDT` and `ETHUSDT` are there as possible side information. From
SOL's trades we build a finite list of candidate **fills**, i.e. trades we could have been resting on
the other side of (§2.6), and tag each with the **profit** it would have made one second later
(§2.7) (this notion of profit is explained later). The task is to pick which fills to take, and **maximize their total profit** (§2.8). Obviously the choice has to be **causal**: at each fill we may only look at data from a fixed
**latency** *before* it. The finished model is then scored on a hidden month of (apparently) the same shape.

## 2.1. What we see, and what we don't

The engine of §1 stamps every order with an `id`, a timestamp $\tau$, and a `seq`. It keeps our
private tag $c$, and rebuilds the book one order at a time. **Here, in the data we get almost
none of that.** Per coin there are just two feeds, both **anonymous** and **aggregated**:

- **`orderbook_10`**: snapshots of the resting book, but only its top 10 **price levels** per
  side. Each level is one price with one *total* size: level 1 is the best quote (the highest
  bid, the lowest ask), level 2 the next price out, on down to level 10, and `bid_size_i` is
  every order resting at price `bid_price_i` added into a single number (same for the asks). We
  never see the separate orders behind that sum, or their place in the queue.
- **`trades`**: a stream of anonymous **prints** (price, size, and which side was the
  aggressor). No `id`, no `seq`, no tag here either.

Two things follow that we keep running into. We can never see a cancel directly: the only sign a
resting order is gone is that the size at its level shrank from one snapshot to the next, or that
a trade print ate into it. And what *does* survive is just the timestamp $\tau$, the prices and
sizes *grouped by level*, and the aggressor side of each trade. The next sections say, plainly,
which column of the parquet files each of those is.

## 2.2. The asset and its grid

The `Asset` is one perpetual-futures contract. It comes with the same two grids as in §1: the
**lot** $\delta$, the smallest size step, and the **tick** $\rho$, the smallest price step. So
sizes live on $\delta\mathbb{N}$, prices on $\rho\mathbb{N}$, both quoted in **USDT**. Read straight off the `price` and `volume` columns, the two steps are:

| coin | tick $\rho$ (USDT) | lot $\delta$ |
|---|---:|---:|
| SOLUSDT | 0.01 | 0.01 |
| ETHUSDT | 0.01 | 0.001 |
| BTCUSDT | 0.1 | 0.001 |

The one worth double-checking is SOL: its tick is `0.01` (a cent), not `0.001`. SOL trades near
`100`, so a price is just `dd.dd` and the smallest move is one cent. However there seem to be a
mistake: of the 3,515,395 prints on 1 Feb, about 40 carry a stray third decimal, and those few
were enough to make the smallest gap between prices look ten times finer than it really is. We ignore them and every price lands on a whole cent.

## 2.3. The clock

The timestamp $\tau$ of §1 is the column **`server_time`**: a whole number of microseconds since
the Unix epoch, like `1769904006706000`. The microseconds are mostly for show, though. The clock
really only moves once a **millisecond**: every `server_time` ends in `000`, so the times sit on a
1 ms grid (`gcd(server_time) = 1000`, across both feeds and all three coins).
That is what this clock is: a publication time the exchange stamps as the data goes out, rounded
to the millisecond, not a precise capture clock. So two things less than a millisecond apart, we
simply cannot tell apart in time.

There is **no `seq`** either, and that is no surprise: §1's counter numbered individual orders,
but we never receive individual orders. The book reaches us as aggregated snapshots (a total size
per price) and the trades as anonymous prints, so there is nothing to number. We just order
everything by its `server_time`, breaking the occasional same-millisecond tie by the order the
rows sit in the file.

The book and the trades arrive on completely different rhythms:
- **the book comes as snapshots, not live.** The exchange does not send the book every time it
  changes. It publishes a fresh photo of the top of the book on a fixed clock, about every **52 ms**
  (roughly 19 a second). It is a **strobe**: 19 still frames a second of a book that is actually
  moving the whole time, and that rate is simply how often the venue broadcasts. We never see the
  in-between states, so **52 ms** is the finest any book-based feature can resolve.
- **the trades arrive as they happen.** Each row is one trade. They are irregular: about **40 a
  second** on SOL on average (3.5 M on 1 Feb), but they clump. In a busy moment several land in the
  very same millisecond; in a quiet stretch none come for a while.

## 2.4. The book

The book $B$ of §1 is the `orderbook_10` feed, one row per snapshot. A row has **43 columns**:
three for identity (`server_time`, an `int64`; `symbol`, the instrument name like
`SOLUSDT-FUTURE`; `symbol_id`, an `int32` code, `0` here), then for each of the **top 10** levels
$i \in L := \{1,\dots,10\}$ on a side four `double`s, `bid_price_i`, `bid_size_i`, `ask_price_i`,
`ask_size_i` (10 levels × 4 = 40). One real SOL row, first three levels shown. Each cell reads
`price × size`: the price on the left (USDT), the total resting size on the right (lots).
```
server_time = 1769904006926000    symbol = SOLUSDT-FUTURE    symbol_id = 0
         bid (price × size)       ask (price × size)
 L1    105.45 × 857.99          105.46 × 282.93
 L2    105.44 × 551.32          105.47 × 332.01
 L3    105.43 × 494.60          105.48 × 1640.50
                  ... down to L10
```

This is just the aggregated snapshot of §2.1 made concrete, no surprise: each level is one price,
level 1 the best quote and the rest stepping away from it, and `bid_size_i` is the total size
resting there, the §1 makers at that price already added up. So the best bid and ask of §1 are
simply $\beta(b) = $ `bid_price_1` and $\alpha(b) = $ `ask_price_1`.

`read_day` (`utils/starter_samples.py:82-107`) keeps the snapshots that pass a basic sanity check,
$$\mathcal{B} := \{\, b : \texttt{server\_time}>0 \ \wedge\ \beta(b)>0 \ \wedge\ \alpha(b)>\beta(b)\,\},$$
sorted by `server_time`. The only rows it drops are placeholders the feed emits at the very start
of a session, before any real order rests: `server_time = 0`, with the sentinel prices
`bid_price_1 = -9999999999` and `ask_price_1 = 9999999999` standing in for "no bid or ask yet".
They are rare and all sit at the open (4 on 1 Feb, about 600 across the 89 days). The
$\alpha(b) > \beta(b)$ clause is just a guard: §1 showed a resting book is never crossed, and
indeed not one snapshot in our SOL data is crossed, across all 89 days.

When we need the best quote (that is, the best bid and best ask currently on the book) at some
time $\tau$, we look it up from $\mathcal{B}$ in one of two directions, depending on what it is
for:
$$\beta^{\leftarrow}(\tau),\ \alpha^{\leftarrow}(\tau) \ := \ \text{best bid/ask of the last snapshot we have at or before } \tau \quad(\text{looking back, for features}),$$
$$\beta^{\to}(\tau),\ \alpha^{\to}(\tau) \ := \ \text{best bid/ask of the first snapshot we have at or after } \tau \quad(\text{looking forward, for the label}),$$
which is just `pd.merge_asof(..., direction="backward")` or `"forward"`. For the book, `read_day`
loads only the best-of-book columns `server_time`, `bid_price_1` and `ask_price_1`. The deeper
levels and every size just sit in the parquet, and will be read directly for the later extension of the problem which needs them;
and ten levels is the most we could ever read, since ten is all the feed gives.

## 2.5. The trades, and which side is ours

The `trades` feed is the aggressor stream of §1 with the names stripped off: `server_time`
(`int64`), `symbol`, `symbol_id` (`int32`), **`aggressor_dir`** (`int32`, the taker's side:
`0 = Bid`, a buyer lifting the asks; `1 = Ask`, a seller hitting the bids), `price` and `volume`
(both `double`). The valid stream is
$$\mathcal{T} := \{\, \langle\,\texttt{server\_time},\ \texttt{aggressor\_dir},\ \texttt{price},\ \texttt{volume}\,\rangle : \texttt{server\_time}>0 \ \wedge\ \texttt{price}>0 \ \wedge\ \texttt{volume}>0 \,\},$$
sorted by `server_time` (in practice nothing ever fails the check). Each row is **one print**, a
single executed trade as it lands on the public tape. When an aggressor sweeps through several
resting levels it shows up as several prints at the same `server_time`, so `volume` is the size of
*that one print*, not of the whole incoming order. And we
never see the price improvement $\Delta$ of §1: that goes to the aggressor, and a print only
tells us the maker price it traded at, not how far the aggressor was willing to go.

Here is the move the whole task turns on: **we play the maker.** Every public trade was someone
aggressing into the resting book, so the order on the other end (the one that got filled) sat
on the **opposite** side. We treat that order as ours. So we flip the aggressor's side to get our
**maker side** (`utils/starter_samples.py:133`):
$$\texttt{dir} = \begin{cases} \texttt{ask} & \texttt{aggressor\_dir}=\text{Bid} \\ \texttt{bid} & \texttt{aggressor\_dir}=\text{Ask} \end{cases}$$
Read `dir` as *our* side everywhere: `dir = "bid"` means our resting buy got hit (a seller came in
and took it), `dir = "ask"` means our resting sell got hit. From here on `dir` always means this flipped maker
side, kept as a `string`; the raw aggressor direction is dropped.

## 2.6. The fills we could have had

A fill in §1 was real: the engine "ate" into a resting order. Here we have no orders resting in the
historical data, so our fills are counterfactual. The trick is that every public trade already had
a maker on one side (whoever was resting and got hit) and an aggressor on the other; we just
imagine the maker was us. Read that way, each trade in $\mathcal{T}$ is a fill we could have had:
had we been resting at the maker price, on the side opposite the aggressor, that trade would have
filled our order.

In a busy moment many trades land in the same short window on the same side, at almost the same
time and price. As candidate fills they would be nearly identical, carrying essentially the same
information, so keeping all of them just over-counts those moments. We therefore **thin**
$\mathcal{T}$ down to at most one fill per side in each window of $w = 100\text{ ms}$
(`select_window_us`), keeping the **first** trade in each `(dir, ⌊server_time / w⌋)` bucket
(`drop_duplicates(..., keep="first")`, `:156-161`). This leaves
a subset of trades $X \subset \mathcal{T}$, and from each fill $x \in X$ we read off its time,
side, price and size:
$$T(x)=\texttt{server\_time},\quad S(x)=\texttt{dir}\in\{\text{bid},\text{ask}\},\quad P(x)=\texttt{price},\quad V(x)=\texttt{volume},$$
where $S(x)$ is our maker side, the opposite of the trade's `aggressor_dir` (§2.5).
Across the 89 days we get **30,337,634** fills (15.4 M on the ask side, 14.9 M on the bid). This is
the **samples table**, written to `data/samples/starter_samples.parquet`; each row also carries a
`date` (`string`, `YYYYMMDD`) and a `sample_id` (`string`, `"{coin}:{date}:{index}"`).

The task comes in two versions, set by the `best_price_trades` flag. With `True` (the default) we
keep every trade. With `False` (the README's extension) we keep only the trades that executed
*deeper* than the best quote just before: a smaller, harder set, more exposed to **adverse
selection**, the maker's hazard of being filled exactly when the price is about to move against us
(a resting buy fills as the price drops, a resting sell as it rises). These deeper fills are worse
on that count, since a trade only reaches past the best quote when an aggressor sweeps through it,
and those sweeps tend to be the larger, orders coming from more informed entities that keep pushing the price the same
way right after.

## 2.7. What a fill earns

It helps to remember what the whole game is for. Trading is a round trip: you start in some
position, you trade, and the aim is to come back to that same position holding more than when you
began. Here the position we start and end at is **flat** (holding only cash), and the "more" we are
after is money, namely **USDT** (§2.1). A fill (§2.6) puts us into a position: a filled bid leaves
us **long** (we now hold the contract), a filled ask leaves us **short** (we owe it). Nothing is
realized yet. To collect anything we have to return to flat, i.e. unwind the position: sell the
contract if we are long, buy it back if we are short. The fill was favourable if getting back to
flat leaves us with more USDT than we started, unfavourable if we have to pay to get there. That
surplus from the round trip is the whole profit.

Concretely, we enter at the fill price $P(x)$ and **mark the position out** one **horizon**
$H = 1\text{ s}$ later (`profit_horizon_us`): we value it at the market a second on. With a maker
**rebate** $r = $ `rebate_bps`$/10^4$ added (`rebate_bps` is in basis points, so the $/10^4$ turns
it into a fraction; it is what the exchange pays a maker when a resting order fills, here **0**),
the profit (by side) is:
$$\pi(x) \;=\; r \;+\; \begin{cases} \dfrac{\beta^{\to}\!\big(T(x)+H\big) - P(x)}{P(x)} & S(x)=\text{bid (long)} \\[10pt] \dfrac{P(x) - \alpha^{\to}\!\big(T(x)+H\big)}{P(x)} & S(x)=\text{ask (short)} \end{cases}$$
Writing $\sigma(x)=+1$ for a long and $-1$ for a short, and $q^{\to}(x)$ for that same-side future
quote, this is just
$$\pi(x) \;=\; \sigma(x)\,\frac{q^{\to}(x)-P(x)}{P(x)} \;+\; r,$$
a plain **return**, and a unitless one: the USDT cancel in $(q^{\to}-P)/P$, so $\pi$ is a pure
fraction, the gain per unit of notional ($10^{-4} = 1$ basis point), not an amount of USDT. The
actual USDT made on a fill would be $\pi$ times the notional put on it, but that figure is never
stored: the column `profit_1s_with_rebate` (`double`) holds $\pi$ itself, the fraction.

Two things make this a tough way to make money, and together they are the heart of the problem: how
we score a fill, and which fills we get.

The first is the mark itself. We fill at a price, then a second later value the position at the
same-side quote: a long at the future bid, a short at the future ask. If the book has not moved
that future quote is exactly the price we filled at, so the round trip nets **0**, a flat second
pays nothing. And flat seconds are the norm: **57% of all fills score exactly 0** (a count over the
`profit_1s_with_rebate` column, printed by the notebook's profit-distribution cell, code cell 3). The mark is also deliberately stingy: it values us where we could
trade out right now, the bid for a long and the ask for a short, never at the mid, so it never
credits the half-spread a maker is notionally up on entry. We only make or lose anything when the
price actually moves in that second.

The second is that, when the price does move, it leans *against* us, which is the adverse selection
of §2.6: a resting order tends to get filled right as the price is about to push through it. So
across all fills **26%** lose and only **17%** win, and the losers are the bigger ones, which is why
blindly taking every fill bleeds money: **−0.19 bps per fill, −589 summed over the 89 days** (a
count and sum over the `profit_1s_with_rebate` column, tabulated in the notebook's accept-all
summary and loss-anatomy cells, code cells 5 and 6). The whole task lives in that thin moving
slice: pick the fills whose next second is likely to go our way, and skip the rest.


## 2.8. The objective, and what we're allowed to look at

Out of the candidate fills we pick a subset $A \subseteq X$ to make the total profit as large as
possible,
$$\text{maximize}_{A\subseteq X}\ \ \sum_{x\in A}\pi(x),$$
where a fill is taken exactly when the rule comes out positive. With no strings attached this is
trivial: just take $A^\star = \{x : \pi(x) > 0\}$. But $\pi$ looks into the **future** (it reads
the book at $T(x)+H$), which we don't know when we have to decide. At each fill we may only use what
was known up to the **cutoff** time
$$c(x) \;:=\; T(x) - \lambda, \qquad \lambda = \texttt{latency\_us} = 10\text{ ms},$$
the `events_before_server_time` column. A feature is allowed only if it reads events with
$\texttt{server\_time} \le c(x)$: features look strictly *backward* from the cutoff, and $\pi$ the
label is the *only* thing allowed to look forward. The latency stands for the round trip from
seeing the market to actually having an order resting in it (data coming in, computing, and
sending the order back out). A shop colocated next to the engine runs that round trip in
microseconds. Its real job is to keep the backtest honest: letting a feature peek
into $(c(x),\,T(x)]$ would be **look-ahead**: acting on information we couldn't have had in time. Worth noting,
$\lambda = 10\text{ ms}$ is shorter than one book snapshot (~52 ms), so the cutoff almost always
falls in the same gap between snapshots as the trade itself; the limit that really bites is the
52 ms between book updates, not the 10 ms latency.
