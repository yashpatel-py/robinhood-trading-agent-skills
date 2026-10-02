# Formulas (prose twin of the scripts)

*Unofficial — not affiliated with Robinhood.*

Use this file only when Python can't run on this surface. Compute exactly as written, show your
work, and label every figure **"computed by hand"**. Money is rounded half-up to cents at the end,
percentages to one decimal. Keep full precision until the final rounding: rounding early is how a
+50.6% becomes a +50.7% that fires a +50.7% target.

## Inputs you will need

| Value | Where it comes from |
|---|---|
| strike, call/put, expiration, underlying type, force-close time, tick sizes | `get_option_instruments`: `strike_price`, `type`, `expiration_date`, `underlying_type`, `sellout_datetime`, `min_ticks` |
| long or short, contracts, average cost, multiplier, pending quantities | `get_option_positions`: `type` (long/short), `quantity`, `average_price`, `trade_value_multiplier`, `pending_*` |
| bid, ask, mark, prior close, quote time | `get_option_quotes`: `bid_price`, `ask_price`, `mark_price`, `adjusted_mark_price`, `close.price`, `updated_at` |
| underlying price | `get_equity_quotes` (the newer of `last_trade_price` and `last_non_reg_trade_price`), or `get_index_quotes` `value` for an index |

`M` below is the multiplier (normally 100), `q` the number of contracts held.

## Opening price per share (average-price unit check)

Robinhood's `average_price` unit is not recorded in the live capture: it may be the premium per
share (`2.05`) or per contract (premium × M, `205.00`), and a short may show a negative value. Read
the response `guide`; if it states the unit, that is the stated unit. The **opening fill** settles
the unit; the current quote never does on its own, because a position that moved 20× looks exactly
like a 100× unit error against it (a short sold at $0.10 and now $2.00 reads as "opened at $10.00,
+79.5%" if you pick the reading that sits near the quote).

1. Take the absolute value of `average_price`.
2. Candidate A (per share) = that value. Candidate B (per contract) = that value ÷ M.
3. Opening fill F = the per-share price of the opening order from `get_option_orders`
   (quantity-weighted when there were several; within 2× is enough). If F is known, the candidate
   with candidate ÷ F between 0.5 and 2 is the unit. It is the same trade, so this holds however far
   the market has moved since. If that contradicts the stated unit, **stop for that position**.
4. No F (or F matches neither candidate):
   - Unit unknown: **don't pick one.** Leave P&L blank and don't evaluate the profit target or the
     stop loss for that position; say why and name both readings. The time stop and maximum holding
     period don't need the open price and are still evaluated.
   - Unit stated: compare with the reference (`adjusted_mark_price`, else `mark_price`, else the
     bid/ask midpoint). If the stated candidate ÷ reference is between 0.1 and 10, use it. If only
     the *other* candidate fits that band, hold the position back as above until F settles it. If
     neither fits, use the stated unit and say the position moved a lot.

## Exit rules (`option_exits.py`)

Exit price: a **long** is marked at the **bid**, a **short** at the **ask**: the price you could
close at right now. Robinhood's app shows P&L at the mark; show that too, labeled, but the rules use
bid/ask.

For one position:
- open value = open price × M × q
- exit value = exit price × M × q
- P&L $ = (exit − open) × M × q for a long; (open − exit) × M × q for a short
- P&L % = P&L $ ÷ open value × 100

For a spread the user confirmed as one position (legs L, signed +1 long, −1 short):
- open value = Σ sign × open price × M × q_leg
- exit value = Σ sign × (bid for longs, ask for shorts) × M × q_leg
- P&L $ = exit value − open value; P&L % = P&L $ ÷ |open value| × 100
  (a credit spread has a negative open value; the percent is of the credit received)

Rules, each skipped and named when UNSET (never invented):
- PROFIT_TARGET fires when P&L % ≥ profit_target_pct.
- STOP_LOSS fires when P&L % ≤ −stop_loss_pct. A short can lose more than 100%.
- TIME_STOP fires when days to expiration (calendar days from today, ET, to the earliest leg's
  expiration) ≤ time_stop_dte.
- MAX_HOLD fires when days held (calendar days since the opening fill) ≥ max_hold_days. If no
  filled opening order is found, days held is **unknown** and MAX_HOLD is skipped, never estimated.

Day change (per Robinhood's guide) = (mark − `close.price`) × M × q, sign flipped for shorts.

**Golden checks.** AMD 165C long ×1, open 2.05, bid 3.30: (3.30 − 2.05) × 100 × 1 = **$125.00**,
1.25 ÷ 2.05 = **+61.0%**. SPY 450C long ×2, open 4.05, bid 6.10, ask 6.25: (6.10 − 4.05) × 100 × 2 =
**$410.00**, **+50.6%** (not $424.00 / +52.3%, which comes from marking at the 6.17 mark).
KO 70C short ×1, open 1.10, ask 2.30: (1.10 − 2.30) × 100 = **−$120.00**, **−109.1%**.
AMD 165C short ×1, `average_price` −10.0000, opening fill 0.10 (so per contract: 10 ÷ 100 = 0.10),
ask 2.05: (0.10 − 2.05) × 100 = **−$195.00**, **−1950.0%** (the stop fires; reading it per share
would have shown a false +79.5% profit).

### Closing spec
- Each leg: `side` = sell for a long, buy for a short; `position_effect` = close;
  `ratio_quantity` = leg contracts ÷ g, where g = the greatest common divisor of the legs' contracts.
- Quantity held = g. The default "all g held" is **shown, not applied**: the user picks the quantity.
- Spread direction: net mid = Σ sign × ratio × (bid + ask) ÷ 2. Positive → closing is a **credit**;
  negative → a **debit**. If the natural net (longs at bid, shorts at ask) has the opposite sign, say
  the direction is unclear and let the user decide.
- The limit price stays **blank**. Reference prices: bid, ask, mid; for a spread the natural net
  (longs at bid, shorts at ask) and the mid net.
- Only when `exit_price_rule` is set, show what it produces (still not applied until asked):
  - `bid`: the natural side: the bid when selling to close, the ask when buying to close; for a
    spread, |natural net|.
  - `mid`: the midpoint (or |mid net|) rounded to the tick **toward the natural side** (down when
    selling or closing for a credit, up when buying or closing for a debit). Tick = `below_tick`
    under `cutoff_price`, else `above_tick`; with no tick data, round to cents and say so.

## Expiration radar (`expiry_risk.py`)

- In the radar: 0 ≤ days to expiration ≤ radar_days (default 7).
- Round the underlying price S to cents (half up) first, as the official close is, and use that one
  figure everywhere: moneyness, distances, pin risk and settlement. A quote of 100.005 against a 100
  call is $100.01, one cent in the money, never "out of the money by −$0.01".
- Intrinsic: call = max(0, S − K); put = max(0, K − S). In the money means intrinsic ≥ $0.01.
- Shares per position = M × q.

| Code | When | Dollars |
|---|---|---|
| AUTO_EXERCISE_CASH_NEED | long call ITM, in the radar, equity | cash = K × M × q; shortfall = max(0, cash − buying power). Sum all such calls per account before comparing |
| AUTO_EXERCISE_SHARE_DELIVERY | long put ITM, in the radar, shares held ≥ M × q | credit = K × M × q |
| LONG_PUT_EXERCISE_SHORT_STOCK | long put ITM, in the radar, shares held < M × q | short shares = M × q − held |
| ASSIGNMENT_SHORT_CALL_DELIVERY | short call ITM, in the radar | deliver M × q shares; covered if held ≥ M × q |
| ASSIGNMENT_SHORT_PUT_CASH | short put ITM, in the radar | cash = K × M × q |
| EARLY_ASSIGNMENT_BEFORE_EX_DIV | short call ITM, today ≤ ex-date ≤ min(expiration, today + radar_days), and extrinsic at the bid < dividend | extrinsic at bid = max(0, bid − intrinsic); extrinsic at mid = max(0, (bid + ask) ÷ 2 − intrinsic). Label: estimate |
| INDEX_NO_MANUAL_EXERCISE | index option in the radar | settles in cash: about intrinsic × M × q at the current level |
| PIN_RISK | in the radar, trading days to expiration ≤ 2, and \|S − K\| ≤ max($0.50, 0.5% × K) | — |
| EXPIRING_WORTHLESS | long, out of the money, in the radar | what is left at the bid = bid × M × q |

Shares are shared: in one account, short calls and long puts on the same stock draw on the same
shares in expiration order. Use the total `quantity` held, not `shares_available_for_sells` (shares
pledged against a covered call are exactly the ones that get delivered).

Trading days to expiration = trading days after today up to and including the expiration date,
using the NYSE holiday table (`rh_time.py`); without it, count weekdays and say holidays were not
excluded.

**Dividend per payment.** If the amount is known per payment, use it. If you only have
`get_equity_fundamentals` `dividend_per_share` (its unit is not recorded in the capture), check it
against the yield: annual ≈ price × `dividend_yield` ÷ 100 (the yield is in percent units), one payment
≈ annual ÷ payments per year (quarterly 4, monthly 12, semi-annual 2, annual 1). Within 20% of one
payment → use it as is; within 20% of the annual figure → divide it by the payments per year;
otherwise use it as one payment and label it **unverified**.

**Golden checks.** SPY 650C long ×2 with SPY at 671.20: cash = 650 × 100 × 2 = **$130,000.00** against
**$2,480.00** buying power, shortfall **$127,520.00**. KO 70C short ×1, KO 72.05, bid 2.20, ask 2.30:
intrinsic 2.05, extrinsic at bid **$0.15**, at mid **$0.20**, below the **$0.53** dividend going ex on
2026-11-18 → early-assignment risk (covered by 100 KO held).

## Earnings implied move (`earnings_move.py`)

- Straddle: the strike nearest spot on the **first expiration after the report** across all chains
  (after-the-close or unknown timing: strictly after the report date; before-the-open: on or after it).
- Implied move $ = call mid + put mid, where mid = (bid + ask) ÷ 2. Implied move % = implied $ ÷ spot × 100.
  Implied range = spot ± implied $ through the straddle's expiration.
- A past reaction for an after-the-close report = report-date close → next trading day's close; for a
  before-the-open report = prior trading day's close → report-date close. Move % = (after − before) ÷
  before × 100. Never use an interpolated bar, and never bridge a missing one: if the neighbouring
  bar is not the adjacent trading day (more than one weekday between them, the one allowed for a
  single exchange holiday), skip that quarter and say why.
- Median of the absolute past moves (the average of the two middle values when n is even).
- Exceeded = how many |past move| are strictly greater than the implied move %, written "k of n".
- Always print n, and the label **"evidence, not a forecast"**. Never a direction.

**Golden check.** NVDA 228.10, 230 strike: call 12.00/12.20 → 12.10; put 13.60/13.80 → 13.70;
implied **$25.80**, **11.3%**.
