# Formulas: the prose twin of every script this skill runs

Read this only when you cannot run Python on this surface (Claude Desktop, claude.ai, ChatGPT without code
execution). Compute by hand exactly as below and label every figure **"computed by hand"**. Money is
rounded half-up to cents at the end, percentages to one decimal, and dates are YYYY-MM-DD. When a
required input is missing, the result is "unknown", never a guess.

## Contents
- [rh_time: sessions and trading days](#rh_time-sessions-and-trading-days)
- [order_lint: rules and the cost estimate](#order_lint-rules-and-the-cost-estimate)
- [canon: the ticket id](#canon-the-ticket-id)
- [policy_check and kitconfig](#policy_check-and-kitconfig)
- [exposure: household concentration](#exposure-household-concentration)
- [options_math: payoff and required move](#options_math-payoff-and-required-move)
- [wash_sale: the 61-day window](#wash_sale-the-61-day-window)
- [lot_select: comparing lots](#lot_select-comparing-lots)
- [alert_spec: alert parameters](#alert_spec-alert-parameters)
- [doctor: capability and inventory](#doctor-capability-and-inventory)

## rh_time: sessions and trading days

- **Now**: a time the user states, else the newest quote timestamp, else the system clock; convert to
  US Eastern (EDT = UTC−4 from the second Sunday in March, 02:00, to the first Sunday in November, 02:00;
  EST = UTC−5 otherwise).
- **Regular session**: 09:30–16:00 ET on trading days; 13:00 close on early-close days. A trading day is
  a weekday that is not an NYSE holiday.
- **NYSE holidays** 2026: Jan 1, Jan 19, Feb 16, Apr 3, May 25, Jun 19, Jul 3, Sep 7, Nov 26, Dec 25.
  2027: Jan 1, Jan 18, Feb 15, Mar 26, May 31, Jun 18, Jul 5, Sep 6, Nov 25, Dec 24. Early closes
  (13:00): 2026-11-27, 2026-12-24, 2027-11-26. After 2027-12-31 the table has expired: say the session
  can't be determined.
- **Outside the regular session** the kit asserts nothing about Robinhood's extended or overnight
  windows (unverified): ask "extended_hours (pre-/post-market) or all_day_hours (24 Hour Market,
  overnight)?"
- **Next trading day on or after D**: step forward past weekends and holidays (2027-01-01 → 2027-01-04).
- **To UTC**: a bare date means local midnight ET (2026-10-17 → 2026-10-17T04:00:00Z in EDT).
- **Settlement (T+1)**: the next day after the trade date that is an NYSE trading day and not a bank
  holiday; Columbus Day and Veterans Day (NYSE open, banks closed) are skipped. A trade on a non-trading
  day carries the next trading day's trade date.

## order_lint: rules and the cost estimate

Check before any review; stop on an error, report warnings.
- **Provenance**: quantity, dollar amount, contract count and every price came from the user or their
  saved config. A marketable limit is the only exception, and only when the user asked for an immediate
  fill: a buy limit ≥ ask, a sell limit ≤ bid.
- **Equity**: exactly one of quantity and dollar amount; dollar amount → market; fractional → market and
  regular hours, ≤6 decimals; market, stop and dollar/fractional orders → regular hours only (after the
  close tagged `regular_hours` they queue for the next open); limit price for limit and stop limit; stop
  price for the stop types; a sell ≤ `shares_available_for_sells`; tax lots: sell only, ≤30, sum exactly
  equal to the quantity, each ≤ `quantity_available`, not with dollar amount, stops, `all_day_hours` or a
  fractional limit.
- **OCO**: whole shares; sell → take-profit > stop; each price |p − m| / m ≥ 0.0025 (0.25%) from the
  current price m; |take-profit − stop| ≥ $0.10; regular hours; time in force gfd or gtc; quantity ≤
  sellable. At exactly 0.25% it passes; at 0.249% it fails.
- **Option**: 1–4 legs; ratios in lowest terms, 1 on a single leg; 2+ legs → limit only, direction
  required; single leg → no direction; price required for limit and stop limit and omitted for market
  and stop market; market and stop market → gfd and regular hours; stop market → sell to close with the
  stop below the ask; equity session values are invalid; CURB sessions only on index chains with
  extended hours enabled; single leg needs level 2+, multi-leg level 3 on margin or limited margin, never
  cash or retirement; quantity a positive whole number.
- **Crypto**: one of quantity and dollar amount; market and limit → gtc only; stops → gtc, gfd, gfw or gfm
  (omitted = gfd, today only); never ioc; no tax lots; quantity a multiple of the pair's
  `min_order_quantity_increment`; not a halted pair.

| Order | Estimate (label it "agent estimate — broker did not compute") |
|---|---|
| equity limit, stop limit | quantity × limit |
| equity market buy / sell | quantity × ask / quantity × bid |
| equity stop market | quantity × stop (trigger basis; a gap can fill far from the stop) |
| equity dollar amount | the dollar amount (the server sizes shares from the last trade price) |
| option, one leg | price × 100 × quantity |
| option, multi-leg | net price × 100 × quantity |
| option market | ask (buy) or bid (sell) × 100 × quantity |
| OCO | both outcomes: take-profit × quantity and stop × quantity (gap risk on the stop) |
| crypto market buy / sell | the amount; worst case +1% / −5% |
| crypto stop order | sized at the stop; then the collar |
| crypto limit, stop limit | sized at the limit |

A broker-returned estimate (the crypto preview, or an option review with fees) always wins; say which one
the ticket shows.

## canon: the ticket id

The ticket id identifies "this exact order". Drop `ref_id`, `chain_symbol`, `underlying_type` and empty
keys; fill defaults (equity gfd + regular_hours; option limit + gfd + regular_hours, ratio 1; OCO
regular_hours; crypto gtc for market and limit, gfd for stops); lowercase enums; uppercase stock symbols
(crypto symbols stay as given); strip trailing zeros from decimals ("31.240" → "31.24"); sort legs by
`option_id` and lots by `open_lot_id`; sort keys; serialize compactly; SHA-256 of `family + "\n" + json`;
the ticket id is the first 6 hex characters. By hand, skip the hash and describe the ticket in full: any
change to a field means a new review.

## policy_check and kitconfig

- **Config**: find it in this order: a path the user names, `$ROBINHOOD_SKILLS_CONFIG`,
  `./.robinhood/config.toml`, `~/.config/robinhood-skills/config.toml`, a pasted block starting
  `# robinhood-skills:config`. A value of "UNSET" (or a missing key) in `[policy]` means "not
  configured": skip that check and list it. Never fill one in.
- **Checks** (soft limits; Robinhood does not enforce them): estimate ≤ `max_order_usd`; symbol value
  after the order ÷ household total ≤ `max_symbol_pct_household`; symbol in the allowlist and not in the
  denylist; days to the next earnings date > `earnings_blackout_days` (an unverified date counts as a
  hit); orders today < `max_orders_per_day`; options only if `allow_options`, crypto only if
  `allow_crypto`; session in `allowed_sessions`; contracts (quantity × each leg's ratio, summed) ≤
  `max_option_contracts`. A limit that is set but can't be evaluated (no estimate, no earnings date)
  **fails**; unknown is never a pass.

## exposure: household concentration

Value every position in scope: stocks and coins at quantity × price; options at the bid (long) or ask
(short) × 100 × contracts, grouped under the underlying, negative for shorts. Sum by symbol across
accounts; the household total is the sum of all values (plus cash only if cash was given; say which).
Percent = symbol value ÷ total × 100. After a proposed buy of N at price P, the symbol value rises by
N × P (and the total too, unless cash is included, since cash would fund it); a sell lowers both. No ETF
look-through. A position you couldn't price makes the result incomplete: list it.

## options_math: payoff and required move

Per share, at expiration price S: each leg is worth max(0, S − K) for a call or max(0, K − S) for a put,
times its ratio, positive when bought and negative when sold; P&L = that sum − net debit (or + net
credit). Multiply by 100 × quantity for dollars. The **natural price** (buys at the ask, sells at the bid)
is the worst-case entry, for analysis only, never the user's limit.

| Structure | Cost or credit | Max loss | Max gain | Breakeven |
|---|---|---|---|---|
| long call | ask × 100 | the premium | unlimited | strike + premium |
| long put | ask × 100 | the premium | (strike − premium) × 100 | strike − premium |
| debit call spread | (long ask − short bid) × 100 | the net debit | (width − debit) × 100 | long strike + debit |
| debit put spread | (long ask − short bid) × 100 | the net debit | (width − debit) × 100 | long strike − debit |
| credit spread | credit (short bid − long ask) × 100 | (width − credit) × 100 | the credit | short strike ± credit |
| iron condor | total credit | (wider wing − credit) × 100 | the credit | short put − credit; short call + credit |
| calendar or diagonal | the debit | two-leg debit calendar or diagonal, long leg expiring later: about the debit when the long leg is at or further in the money than the short (both closed together); further out of the money, an upper bound of debit + width; a short call ever left uncovered: unlimited; anything else: not computed (`max_loss_basis`) | not computed | not computed |

- **Required move** = (breakeven − underlying) ÷ underlying × 100, signed. Read it through
  `profits_when` (above, below, between, outside) and `required_move_meaning`: `needed` is the move the
  position still needs; `cushion` means it already profits at today's price and the figure is how far the
  underlying can move against it before it starts losing (an in-the-money debit spread past its
  breakeven shows a cushion, not a required fall). On a debit ticket that still needs a move, lead with
  it: on cheap out-of-the-money contracts it is routinely a double-digit percentage within weeks.
  Example: a 165 call bought at 6.30 with the stock at 161.40 breaks even at 171.30, a required move of
  +6.1%. On short, credit and cash-secured-put tickets, lead with the cushion wording instead.
- **Spread %** per leg = (ask − bid) ÷ ((ask + bid) ÷ 2) × 100.
- **Risk/reward** on spreads: state it. A spread costing $0.80 on a $1.00 width risks $80 to make $20.
- **Contracts per leg** = quantity × ratio: quantity 2 on an iron condor is 8 contracts.
- A contract whose multiplier isn't 100 is adjusted; these formulas don't apply; say so.
- **Position P&L** (conservative): long = (bid − open price) × 100 × contracts; short = (open price − ask)
  × 100 × contracts.

## wash_sale: the 61-day window

The full procedure (which accounts, which orders, recurring and dividend buys) is in
`references/wash-sweep.md`; the arithmetic is:
- **Window**: sale date D − 30 through D + 30 calendar days, both ends included.
- **Replacement buys**: fills of the same ticker inside the window, in **any** Robinhood account (IRA
  included), excluding the shares being sold.
- **Loss per share** per lot = cost per share − sale price (only lots sold at a loss count).
- **Matching** (the same rule as `wash_sale.py` and `references/wash-sweep.md` §By hand step 5): take
  replacement buys earliest first and attach them to the loss lots sold, **earliest acquired first**
  (Treas. Reg. 1.1091-1(b)–(c)), whatever order the lots are listed in. Washed shares = min(buy shares
  left, loss shares left); disallowed = Σ washed × that lot's loss per share. Only a missing acquisition
  date leaves the order unknown: then report the largest-loss-first figure (the most that can be
  disallowed) with the smallest-first figure next to it. Allowed loss = total loss − disallowed. Fewer
  washed than loss shares is a partial wash. Example: 20 shares sold at $262 from lot A (10 at $267,
  $5 loss each, acquired first) and lot B (10 at $282, $20 loss each, acquired later), with 10
  replacement shares: lot A is matched, so $50.00 is disallowed and $200.00 allowed. With the
  acquisition dates missing, the range is $200.00 (largest first) down to $50.00 (smallest first).
- **Permanent**: a replacement buy in a retirement account; the disallowed loss is not added to IRA basis
  (Rev. Rul. 2008-5). In a taxable account it moves into the new shares' basis.
- **Dates**: do-not-buy-until = D + 31; the first clean trading day = the next trading day on or after
  that. Earliest clean sale date = latest replacement buy in the 30 days before + 31.
- **Buy check** (a planned buy of a symbol sold at a loss): any loss sale within the 30 days before the
  buy date washes min(planned shares, loss shares) × loss per share; the clean buy date is the loss
  sale's date + 31. Example: AMD sold 2026-11-03 at a $41.20/share loss; buying 3 shares on 2026-11-16
  washes 3 × $41.20 = $123.60; clean buy date 2026-12-04.
- **Status**: conflict > unknown (an account unread or a field missing) > possible (inferred recurring or
  dividend-reinvestment buys, option buys, related tickers) > clear_in_scope (the user excluded an
  account) > clear. Never "clear" unless every account was read completely. Crypto is not covered by the
  wash-sale rule as of 2026-09-22: say so with that date.

## lot_select: comparing lots

For a sale of Q shares at price P, fill Q from the lots in each strategy's order: FIFO (oldest first;
Robinhood's default), highest cost first, lowest cost first, long-term first, losses first. Per lot,
realized = (P − cost per share) × shares; a lot is long-term when held more than one year (acquired
2025-12-03 → long-term from 2026-12-04). Report short-term and long-term totals per strategy; there is no
"best", the user chooses. A valid pick has ≤30 lots, quantities summing exactly to Q, each ≤
`quantity_available`, and no lot with `is_selectable` false.

## alert_spec: alert parameters

Map the intent to a shape (`references/alerts-watchlists.md` §Choosing the condition): price vs a number
takes a threshold and no indicator; an indicator's own value takes both; price vs a line takes an
indicator and no threshold. Bars: 5m 300, 10m 600, 1h 3600, 1d 86400, 1w 604800, 30d 2592000 seconds;
VWAP only 300. Crypto: price conditions only. Always include `asset_class`. Periods come from the user.
Not expressible: MA-versus-MA crosses, percent change, volume, option contracts, indexes. The dedupe key
is symbol | asset class | condition | period | interval.

## doctor: capability and inventory

| Strategy | Level | Account types | Retirement |
|---|---|---|---|
| long call, long put, covered call, cash-secured put | option_level_2 | any | allowed |
| vertical spread, calendar, iron condor, straddle, strangle, one-order roll | option_level_3 | margin or limited margin | not available |
| crypto | none | any, with a crypto account | n/a |

Null, empty, `option_level_0` or `option_level_1` means no options review. Routes: level too low on a
qualifying account → options upgrade, the user completes it, re-fetch accounts. Level 3 on a cash account
→ limited-margin upgrade, the user completes it, re-fetch accounts, then the options upgrade only if the
level is still short. Never route to an upgrade the account doesn't need. A roll on level 2, cash or an
IRA = two single-leg orders with legging risk. Unknown account type or retirement status → settle it
first.

Inventory: a tool is money-like if its name starts with place, exercise, replace, submit, execute,
transfer, withdraw, deposit, stake, unstake, convert, send, buy, sell, trade, liquidate, lend or borrow;
it is covered by the always-on guard layer only if it starts with place, exercise or replace. The guard
self-test has no by-hand version: without Python and `sh`, say the guard is unverified on this surface.
