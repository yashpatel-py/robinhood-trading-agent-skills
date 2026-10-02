# Earnings inside a position's life: the implied move

*Unofficial — not affiliated with Robinhood.*

Read this when an equity underlying of a held option reports earnings before that option expires,
or when the user asks what the options market prices in for a report. The script is
`scripts/earnings_move.py`; the formulas are in `formulas.md`.

## Why

An earnings report inside a held option's remaining life is the largest single source of overnight
gap risk, in both directions. The at-the-money straddle is the options market's price for that
move, and the stock's own reactions to its last reports show how often moves that size actually
happened. Neither is a forecast. The output always carries n and the label "evidence, not a
forecast", and never a direction.

## When to run it

1. For each **equity** underlying you hold options on: `get_earnings_results {symbol}` (one symbol
   per call; never the calendar, which has no symbol filter and hides names under $1B).
2. Read the next report's date, timing (before the open / after the close) and whether the company
   verified it, plus the trailing reports (up to 8). The field names aren't in the live capture;
   follow the response guide.
3. Run the rest only when the next report falls inside the position's life: on or before the latest
   held expiration on that underlying. An **unverified** date counts as a possible hit.
4. Index underlyings have no earnings; skip them.

## Tool sequence

1. `get_option_chains {underlying_symbol: "<S>"}`. Several chains can come back (weeklies, adjusted
   chains). Across **all** of them, find the first expiration after the report: strictly after the
   report date for an after-the-close or unknown-timing report, on or after it for a before-the-open
   report. If it appears in several chains, prefer the standard (non-adjusted, 100-multiplier) one.
2. List the calls on that date: `get_option_instruments {chain_id, expiration_dates: "<YYYY-MM-DD>",
   type: "call", cursor}`, every page. Pick the listed strike nearest the spot price (the lower one
   on a tie).
3. The put at that strike: `get_option_instruments {chain_id, expiration_dates: "<YYYY-MM-DD>",
   strike_price: "<the strike exactly as the call result printed it>", type: "put"}`. `type` takes
   **one** value, so the straddle always needs two instrument calls.
4. `get_option_quotes {instrument_ids: [<call id>, <put id>]}` → `bid_price`, `ask_price`.
5. Past reactions: `get_equity_historicals {symbols: ["<S>"], start_time: "<today − 2 years,
   RFC3339 UTC>", interval: "day"}`. The default split adjustment is what you want. Pass **every**
   bar as `{date, close, interpolated}` (bar field names follow the response guide), interpolated
   ones included: don't filter them out yourself. Interpolated bars are synthesized and carry no
   information, and the script skips any quarter that needs one; if you drop rows first, the gap is
   still caught (the script checks that the two bars are adjacent trading days), but the reason it
   gives is less precise.
6. `python3 scripts/earnings_move.py run` with `symbol`, `spot`, `report`, `straddle`, `past` (each
   trailing report's date and timing), `bars`, and `held_expirations` (each held contract's label and
   expiration, so the output says which positions the report falls inside).

## Reading and presenting

- Lead with the implied move in dollars and percent and the date it runs through, then the median
  absolute past move, how many past moves exceeded today's implied move ("k of n") and n.
- The straddle also prices ordinary movement between the report and its expiration, so it slightly
  overstates the earnings move alone; the script says so when the gap is more than a day.
- Wide spreads, a missing bid, or a strike far from spot make the mid-sum rough; the script's notes
  say which applies.
- Say "evidence, not a forecast". Never "the stock will", "expect", "likely up/down", or any sizing,
  hedging or exit suggestion. If the user asks what to do, lay out what the numbers show on both sides
  and leave the decision with them.
