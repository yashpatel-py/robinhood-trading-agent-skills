# Formulas: the prose twin of every script

*Unofficial — not affiliated with Robinhood.* Use this only when Python cannot run on this surface, and label every figure "computed by hand". Money is rounded half-up to cents at the end, not along the way. Dates are US Eastern trade dates. Worked numbers: `references/examples.md`.

## Calendar facts the formulas need (from `rh_time.py`; embedded 2026-09-22, re-verify each year)

- NYSE closed: 2026 Jan 1, Jan 19, Feb 16, Apr 3, May 25, Jun 19, Jul 3, Sep 7, Nov 26, Dec 25; 2027 Jan 1, Jan 18, Feb 15, Mar 26, May 31, Jun 18, Jul 5, Sep 6, Nov 25, Dec 24. Early close (13:00 ET): 2026 Nov 27, Dec 24; 2027 Nov 26.
- "First trading day on or after X": if X is a Saturday, Sunday or a closed day, step forward a day until it is not.
- US Eastern = UTC − 4 during daylight time (2026-03-08 to 2026-11-01 02:00; 2027-03-14 to 2027-11-07), UTC − 5 otherwise. A timestamp's trade date is its US Eastern calendar date. At 20:00 ET or later (the overnight session), or on a closed day, the broker may use the next trading day as the trade date: keep both dates in mind.

## `wash_sale.py`

**`window`** — for trade date D:
- window = D − 30 through D + 30 (61 calendar days, both ends included).
- lookback start = window start − `gtc_lookback_days` (default 90); `created_at_gte` = that day at 00:00 US Eastern, written in UTC (…T04:00:00Z in daylight time, …T05:00:00Z otherwise).
- loss sales are read from `loss_sales_from`: D − 60 for a sale (a buy that already washed an earlier loss cannot wash this one, Treas. Reg. §1.1091-1(e)), D − 30 for a planned buy.
- trade-history span: `3month` when `loss_sales_from` is within the last 85 days; else `ytd` when it is in this calendar year; else `all`.
- do-not-buy-until = D + 31; its first trading day as above.

**`classify_pnl`** — a trade-history row is a **share sale** only if a filled `get_equity_orders` sell in the same account has: the same symbol; `created_at` − 2 minutes ≤ row time ≤ `last_transaction_at` + 2 minutes; enough filled quantity left (`cumulative_quantity` minus rows already matched); and |row price − order `average_price`| ≤ 2% of `average_price`. Otherwise:
- no symbol → unattributed (cannot belong to any stock);
- anything else → unclassified. An unclassified **loss** row makes that symbol's check unknown.

A matched loss row gives: loss per share = −realized ÷ quantity; trade date = the row's US Eastern date.

**`run`, sale mode:**
1. Loss lots: each sold lot with cost > sale price; loss per share = cost − price. Gain lots are not washed. A lot with no cost is basis pending → unknown.
2. Buys (any account, IRA and Roth included): from orders, side buy with `cumulative_quantity` > 0 in any state, trade date from `last_transaction_at`; plus open lots acquired in the window that no order explains. Keep those inside the window. A buy created before the window's end but last filled after it is possible, not ignored.
3. Drop the purchase of the shares being sold (same lot id, or same account and same trade date as a sold lot, up to that lot's shares). Leftover shares of that same purchase are possible (same block), not clear. For a sale that already happened, the sold shares' acquisition date (`sale.acquired`, or `acquired_lots` [{acquired, shares}] from the user or the FIFO order history) identifies that purchase; without it, a same-account buy up to the sale date is possible, not a conflict.
4. First remove buys an earlier loss sale already used: loss sales of the stock from D − 60, in the order sold, each take the earliest buys in their own window (Treas. Reg. §1.1091-1(e)). Then match: the remaining buys earliest first, attached to the loss lots sold earliest acquired first (Treas. Reg. §1.1091-1(b)–(c)), whatever order they are listed in; washed = min(buy shares left, loss shares left); disallowed = Σ washed × loss per share. Only a missing acquisition date leaves the order unknown: then use the largest loss per share first (the most that can be disallowed) and repeat smallest-first for the low figure. Retirement buy → that part is permanent.
5. Clean shares = loss shares − washed; allowed loss = total loss − disallowed.
6. Earliest clean sale date (only if something washed and D is today or later) = latest in-window buy date + 31 — while a recurring buy continues, no date is clean.
7. Possible items: unknown fills, same-block leftovers, projected recurring buys (monthly cadence → the same day each month, moved to the next trading day), projected dividend reinvestment (the payable date, or the last one plus the payment frequency), option orders on the same stock, declared related tickers. "Up to" dollars for possible items = the same matching on the loss shares left.
8. Status: conflict if anything washed; else unknown if any account is partial or failed, a lot is basis pending, or a loss row is unclassified; else possible if any possible item; else clear-in-scope if an account was left out on purpose; else clear.

**`run`, planned-buy mode** (buy on date B):
- Loss sales: confirmed share-sale losses in taxable accounts dated B − 30 through B (from classify, or a sale the user states).
- Match the planned shares against those losses, earliest sale first → washed, disallowed (permanent if the buy is in an IRA or Roth).
- Earliest clean buy date = latest such loss-sale date + 31. The buy also opens its own window: a loss sale through B + 30 would be washed by it.
- Other known buys near those loss sales may already have washed part of the loss; the kit does not net them, so its figure is the maximum.

## `lot_select.py`

- **Term on the sale date:** long-term if the sale date ≥ the long-term date from `holding_period.py` below (acquired + 1 year + 1 day; a lot acquired on the last day of a month → the 1st of the 13th month after it), else short-term.
- **Order of lots per strategy** (ties: older first, then lot id):
  - `fifo` (Robinhood's default): oldest acquisition first.
  - `highest_cost`: highest cost per share first. `lowest_cost`: lowest first. Lots with no cost last.
  - `long_term_first`: long-term lots (oldest first), then short-term lots (oldest first).
  - `losses_first`: short-term losses, long-term losses (largest loss per share first), long-term gains, short-term gains (smallest gain per share first), then lots with no cost.
- **Fill the quantity** in that order, each lot up to `quantity_available` (lots with `is_selectable` false are skipped).
- **Realized** per lot = (price − cost) × quantity taken; split short and long by term. Any chosen lot with no cost → realized unknown.
- **Valid** when the quantity is fully covered and at most 30 lots are used. Never name a "best" strategy.
- **validate:** side is sell; ≤ 30 lots; each quantity a positive decimal string ≤ that lot's `quantity_available`; lot ids exist, appear once, and are selectable; quantities sum **exactly** to the order quantity (0.1 + 0.2 = 0.3 in decimal, not floating point); not with `dollar_amount`, a stop order, `all_day_hours`, or a limit order for a fractional quantity.

## `holding_period.py`

- long-term on = acquired + 1 year + 1 day, except that a lot acquired on the **last day of a month** is long-term from the **first day of the 13th month after it** (Rev. Rul. 66-7). The two agree except at the end of February: bought 2027-02-28 → long-term from 2028-03-01 (a sale on 2028-02-29 is still short-term); bought 2024-02-29 → long-term from 2025-03-01. First trading day on or after it as above.
- term = long if as-of ≥ long-term on, else short; days until long-term = max(0, long-term on − as-of).
- crosses within horizon = short and days until ≤ `lookahead_days` (default 45).
- unrealized = (price − cost) × shares, when a price and a cost exist.

## `harvest_plan.py`

- Per (account, symbol) in taxable accounts: loss lots are lots with (price − cost) × shares < 0. Harvestable loss = −Σ of those; short and long split by Robinhood's own `term` for each lot, else the date arithmetic on the as-of date. Where the two differ, flag the split as disputed (`TERM_DISAGREES`) and show the date arithmetic's split next to it; Robinhood's tax documents govern.
- Include the pair only if harvestable loss ≥ `min_loss_usd` (all when UNSET).
- From one combined wash result per symbol (every candidate lot of it, all taxable accounts, sharing one pool of replacement buys), each candidate takes its own lots' rows: disallowed if sold now, permanent part, earliest clean sale date, do-not-buy-until. No result covering its lots → wash status "not checked" (unknown). A loss lot the result does not cover → unknown for that lot, never $0 disallowed. Two separate results that matched the same buy are never added: each is an "if sold alone" figure, and the totals are left blank until one combined result exists.
- Disallowed is at most the harvestable loss, and permanent at most disallowed.
- Net after wash = harvestable − disallowed. Reviewable only in the Agentic account; every other account gets a manual ticket.
- Scan status, worst first: conflict (any candidate) > unknown (an account partial or failed, basis pending, a symbol unpriced, or any candidate unknown or not checked) > possible > clear in scope (any account left out of the read scope, or a wash result that was clear only in scope; printed "CLEAR … (wash checked only in the accounts read; not read: …)") > clear > no action (no candidates).
- A lot bought within 30 days of a loss sale of the same stock in another account may be a wash replacement: its real basis includes the deferred loss and its holding period includes the sold shares'. Robinhood's lot shows neither, so say so next to the figures.
- Retirement accounts: unrealized listed separately, never counted.
- YTD arithmetic: taxable realized so far − Σ net after wash. Sorted by harvestable loss, largest first (a display order).

## `realized_summary.py`

- Keep trade rows whose US Eastern date is in this calendar year. Rows with no realized amount are n/a.
- Per account: sum, gains, losses, count. Taxable total = Σ taxable accounts; IRA and Roth listed separately.
- Per symbol (taxable only): sum, gains, losses. Rows with no symbol are counted apart.
- Reconcile: account sum − `get_realized_pnl` total (null buckets are n/a, not 0). A gap of a cent or more is shown with its likely cause.
- Term split: only if rows carry a term; otherwise "not provided by the connector; see Robinhood's tax center".

## `rebuy_calendar.py`

- Per item: an all-day "Don't buy <S>" event from the sale date up to (not including) do-not-buy-until, and an all-day "OK to buy <S> again" event on do-not-buy-until with a 09:00 reminder. By hand: tell the user the two dates to put in their calendar.
