<!-- synced from shared/wash-sweep.md; do not edit -->
# Wash-sale sweep across every Robinhood account

*Unofficial — not affiliated with Robinhood.* Used by `robinhood-trading` (checked tickets) and `robinhood-tax-loss-harvesting`. Rules as of 2026-09-22; tool fields from the 2026-09-22 capture.

**Why this exists.** Robinhood reports wash sales per account, on separate 1099s, and warns at trade time about none of them. A loss sold in the Agentic account is still washed by a buy of the same stock in the Individual account, an IRA or Roth, a recurring buy or a dividend reinvestment, from 30 days before to 30 days after the sale. A buy in an IRA or Roth makes the loss disappear permanently (Rev. Rul. 2008-5). The agent can read every account, so it can see what no 1099 sees. The expensive mistake is a false "clear", so every step below exists to stop one: an unread page, a GTC order created before the window, a UTC timestamp read as a date, an IRA left out, a trade-history row that was really an option.

**When to run it.**
- Before preparing any sell at a loss, and for "can I harvest X?", "when can I buy X back?" and every harvest-scan candidate.
- Before any buy of a stock that was sold at a loss in the last 30 days (in any account), that the user holds below cost in a taxable account, or that the user plans to sell at a loss within the next 30 days. From December 1, any buy can wash a year-end harvest: ask whether one is planned.
- Before any option ticket with an opening leg that buys a call or sells a put (`position_effect: open`, including the opening leg of a spread or a roll) on `S`. A call bought or a put sold is an option to acquire `S` (IRC 1091(a); Rev. Rul. 85-87 for a short put), so it can wash a loss sale of `S`. Run it as a `planned_buy` with `instrument: "option"`.

**Inputs.** Symbol `S` (a stock or ETF). Trade kind: `sale` (a loss sale) or `planned_buy` (shares, or an option to acquire). Trade date `D`: the date the user states, else today in US Eastern time (`python3 scripts/rh_time.py session`). The account being traded.

**Which steps.**
- A `sale` runs steps 0–9. Step 6 reads loss sales of `S` from 60 days before `D`: a buy that already washed an earlier loss cannot wash this one (Treas. Reg. 1.1091-1(e)).
- A `planned_buy` runs steps 0, 1, 2, 3 (taxable accounts only, plus `get_equity_quotes {symbols: [S]}` for the price), 6, 8 and 9. The buy itself is the replacement, so it needs the loss sales and the orders that confirm them, the open lots a later loss sale would come from, and any harvest the user has declared; not other buys or options.
- A harvest scan runs one sweep per symbol, not one per account: every candidate lot of `S` in every taxable account goes into one `lots_sold` (each lot with its `account_last4`), so the candidates share one pool of replacement shares instead of each claiming it. It stops at step 8: the script output goes to `harvest_plan.py`.

**Crypto stops here.** The wash-sale rule covers "stock or securities"; crypto held directly is property, so under current law (as of 2026-09-22) it does not apply. `wash_sale.py` refuses crypto with `CRYPTO_NOT_SUBJECT`. Say so with the as-of date, mention that a bill introduced 2026-06-08 would change it but is not law, and do not run the sweep. Crypto ETFs are listed securities: sweep them like any stock.

---

## Step 0 — Scope and consent

1. Call `get_accounts {}` fresh (never from an earlier turn: an account opened since then would be missed).
2. List every account, masked (`••••X4F1`), with a readable label (nickname, or Agentic / Individual / IRA).
3. Read scope: follow R3 in `references/connector-rules.md` (load `[policy]` with `kitconfig.py get {"section": "policy", "cwd": "<the user's project directory>"}`). For this sweep that means:
   - No config, or `all`: the tax or trade request is the consent (a wash check means nothing for one account alone). Read every account, and say in the report which accounts were read and that the data went to the AI provider (R18); offer to narrow next time.
   - `ask`: before reading beyond the Agentic account, ask once: *"This reads orders and lots in ••••X4F1 (Agentic), ••••M7Q5 (Individual) and ••••P0Z9 (Roth IRA); that data goes to your AI provider. Agentic only, or all accounts?"* A scheduled run cannot ask: it reads the Agentic account and any account its prompt names.
   - `agentic_only`, an account the user leaves out, or one a scheduled `ask` run could not ask about: that account is `not_in_scope`, and the best possible result is "clear in the accounts read". Say which accounts that leaves unchecked.
4. Classify each account as `taxable` or `retirement`:
   - `brokerage_account_type` containing `ira` or `roth` → retirement; `individual` or `joint` → taxable. (IRA values were not observed in the capture; treat any other value as unknown.)
   - else the account's last 4 listed in `[accounts] retirement` → retirement;
   - else ask once: "Is ••••P0Z9 an IRA or Roth? A buy there permanently erases a washed loss."
   - The Agentic account is taxable. Managed (Robinhood Strategies) accounts are read too: their buys count.
5. Start every account at `read_status: "complete"`. Set `partial` when any page or call for it fails or is skipped, `failed` when nothing could be read. A failed call is never "no rows".

Why: C2 in the audit — positions and lots are never read from an account the agent picked on its own, and an account left out silently is the classic false clear.

## Step 1 — Window and query bounds

Run `python3 scripts/wash_sale.py window` with `{"date": D, "as_of": <today>, "mode": <sale|planned_buy>, "gtc_lookback_days": <[tax] gtc_lookback_days, default 90>}`. It returns:

- `window`: `[D − 30, D + 30]` in calendar days, both ends included (61 days). Weekends and holidays never stretch it; the trade date sets it, not the settlement date.
- `created_at_gte`: midnight US Eastern on `window.start − gtc_lookback_days`, in UTC. The orders filter bounds when an order was **created**, not when it filled, so a GTC buy created before the window can fill inside it. A naive date is read as UTC by the connector, so always send the UTC value.
- `loss_sales_from` and `pnl_span` (`3month`, `ytd` or `all`) for step 6: `D − 60` for a sale (an earlier loss has first claim on a buy both windows share), `D − 30` for a planned buy.
- `do_not_buy_until`, `first_trading_day_after`, and the not-yet-happened part of the window.

Why: M53 in the audit (fill time, not creation time) and R8.

## Step 2 — Buys, every account in scope

For each account in scope: `get_equity_orders {account_number, symbol: S, created_at_gte}` with **no `state` filter**, reading every page (`cursor` = the `cursor` query parameter of the `next` URL) until `next` is null. Keep the raw rows; `wash_sale.py` maps them:

- A buy counts when `side` is buy and `cumulative_quantity` > 0, **in any state**: `partially_filled`, and `cancelled` with a partial fill, count too.
- Its trade date is the US Eastern date of `last_transaction_at`. A fill at 20:00 ET or later (the overnight session) may carry the next trading day's trade date; the script checks both dates.
- Filled after the window but created before its end: counted as possible (some shares may have filled inside).
- `placed_agent` (`user`, `agentic`, `recurring`, `drip`) is kept for the report.

Why: the page-2 buy is the classic miss (the fixture's Roth buy sits on page 2 behind three unfilled orders), and a filter such as `state: "filled"` drops partial fills.

## Step 3 — Lots cross-check

For each account in scope, `get_equity_positions {account_number, cursor}` (every page) to see whether it holds `S`. For each that does: `get_equity_tax_lots {account_number, symbol: S, cursor}` (every page). Pass the rows. An open lot whose `open_date` falls inside the window and that no order buy explains (a dividend reinvestment, a transfer in, a corporate action) becomes a buy with `source: "tax_lot"`. A lot with no cost is **basis pending**, never zero.

- **A sale being prepared:** the lots being sold supply `open_lot_id`, `open_date` and `cost_per_share`. The acquisition dates matter: lots sold together count as sold earliest acquired first (Treas. Reg. 1.1091-1(b)), so a partial wash lands on the oldest loss lot, not the biggest loss.
- **A sale that already happened:** its lot is closed, so no lot row exists. Pass `sale.acquired`, the trade date of the buy that supplied the sold shares (or `acquired_lots: [{acquired, shares}]` for several buys), from the user or from the order history (Robinhood sells first-in, first-out unless a lot was chosen, so this is a likely date, not a certain one). Without it, a same-account buy up to the sale date may be that purchase, and the script lists it as possible, never as a conflict.
- **A planned buy:** only taxable accounts, and pass the price (the buy's limit, or `last_trade_price` from `get_equity_quotes`) as `planned_buy.price_per_share` (`underlying_price` for an option). An open lot below cost is a harvest that this buy would wash if it is sold at a loss before `rewash_until`.

## Step 4 — Recurring buys and dividend reinvestment (look-ahead)

- **Recurring:** the step-2 rows already reach back more than 100 days (window start minus the GTC lookback). `wash_sale.py` takes filled buys with `placed_agent: "recurring"`, infers the cadence and projects the next buys inside the window. If you lowered `gtc_lookback_days` below 70, also call `get_equity_orders {account_number, symbol: S, placed_agent: "recurring", created_at_gte: <recurring_created_at_gte>}`.
- **Dividend reinvestment:** call `get_equity_orders {account_number, symbol: S, placed_agent: "drip", created_at_gte: <drip_created_at_gte>}` (about 400 days). If any exist, call `get_equity_fundamentals {symbols: [S]}` and pass `ex_dividend_date`, `payable_date` and `distribution_frequency` as a `drip` entry for that account.
- Both are labeled "inferred from history; the connector cannot see your schedule" and count as **possible**, never as clear.

## Step 5 — Options on the same stock

1. `get_option_chains {underlying_symbol: S}` — keep every chain `id` (adjusted chains exist after corporate actions).
2. For each account in scope whose `option_level` is not empty: `get_option_orders {account_number, chain_ids: "<id1>,<id2>", created_at_gte}` with no state filter, every page.
3. Option-order response fields were **not** in the 2026-09-22 capture. List every option order on these chains with a fill inside the window as `option_buys` (`date`, `contracts`, a short description). If a row shows it opened a long call or a short put, say so; if you cannot tell, list it anyway.
4. These are **possible** ("may be substantially identical; not evaluated"). The kit never decides that question.

## Step 6 — Loss sales

For each **taxable** account in scope: `get_pnl_trade_history {account_number: <that account's rhs_account_number VALUE>, span: <pnl_span>, symbol: S, cursor: <next_cursor>}` until `next_cursor` is empty. Pass the rows as `pnl_rows` with the step-2 orders, even when there are none (an empty list tells the script the check was done).

- For a sale, these are the earlier loss sales: losses are taken in the order they were sold, and a buy that washed an earlier loss cannot wash this sale (Treas. Reg. 1.1091-1(e)). Without step 6 the script labels its figures the maximum.
- For a planned buy, these are the losses the buy would wash. Also ask about, and pass as `planned_sales`, any loss sale of `S` the user plans (a year-end harvest): `[{account_last4, date, price_per_share, lots: [{lot_id, shares, cost_per_share, acquired}]}]`. A declared harvest inside the buy's window is a **conflict**, permanently so for an IRA or Roth buy.

The trade history has no asset-class and no short/long-term field. Option closes appear under the **underlying** ticker with the premium as `price`; crypto appears under its base code; some rows have no symbol. So `wash_sale.py` counts a row as a share sale only when a filled `get_equity_orders` sell matches it (same account and symbol, inside that order's life, enough filled quantity, price within 2% of its average). An unmatched loss row is `unclassified`: it makes `S` **unknown**, never clear. A loss sold inside an IRA or Roth is not deductible, so it cannot be washed.

## Step 7 — Tickers the user declared related

If `[tax] related_tickers` puts `S` in a group (for example `["VOO", "IVV"]`), repeat step 2 for each other ticker and pass matches as `related_buys` with `declared_related`. They are **possible** ("you told us these may be substantially identical"). Never add a ticker the user did not declare.

## Step 8 — Run the script

`python3 scripts/wash_sale.py run < input.json` with `mode`, `symbol`, `as_of`, `accounts` (every account from step 0 with `type` and `read_status`), the `sale` or `planned_buy`, and `per_account: [{account_last4, orders, tax_lots, pnl_rows}]` holding the raw rows. Add `option_buys`, `drip`, `related_buys`, `declared_related` and `planned_sales` when present. For an option ticket, `planned_buy` is `{date, account_last4, instrument: "option", contracts, structure, description, underlying_price}`: every loss it could wash comes back as possible ("an option to acquire may wash this loss; not evaluated"), never as clear. If Python cannot run here, use "By hand" at the end of this file and label every figure "computed by hand".

Status precedence: `conflict` > `unknown` > `possible` > `clear_in_scope` > `clear`. It is `clear` only when every account from `get_accounts` was read completely.

## Step 9 — Report

Print the script's `status_line` first, then:

```
WASH-SALE CHECK: <sell|buy> <qty> <S> (<lot>) in <Label> ••••<last4>, <trade date> · quotes as of <time ET>
Accounts read (this data went to your AI provider): ••••X4F1 Agentic (taxable) · ••••M7Q5 Individual (taxable) · ••••P0Z9 Roth IRA (retirement)   Not read: <none | list>
Window: <start> → <end>
CONFLICT: <account> bought <n> <S> on <date> → <washed> of <loss shares> shares washed → $<amount> disallowed<, PERMANENTLY (IRA purchase)>
Clean: <clean shares> shares ($<allowed loss>) · earliest clean sale date <date> (if no new buys) · don't buy back in any account until <do_not_buy_until>
Possible: <items, each with its reason> · Unknown: <items> · Not evaluated: other brokers, spouse accounts, future recurring/DRIP settings beyond history, different tickers you consider equivalent, basis and holding-period adjustments on replacement lots from earlier washes (cross-account ones are not in Robinhood's lots)
```

Only when the calling workflow is preparing a sale the user asked for, with the user's own quantity and limit (R2): the lot choice (core: `lot_select.py compare`; no "best") and the ticket. A sale in the Agentic account gets `review_equity_order` with `tax_lots` (after `lot_select.py validate`); a sale in any other account gets a manual ticket for the app's tax-lot selector with handoff R21(b). A status question ("can I sell TSLA at a loss?"), a harvest-scan candidate and a scheduled run end at the report: never invent a quantity or limit to reach a ticket. Close with: *What this is: rule arithmetic on your Robinhood data. What it isn't: tax advice or a "substantially identical" judgment.*

## Call budget and failures

- Roughly: accounts × (orders + positions + lots) + 1 DRIP check per account + 1 chain lookup + option orders per options-enabled account + trade history per taxable account, plus extra pages. When the plan exceeds 40 calls, say how many before starting (R23).
- A tool that answers `the tool you requested cannot be found or does not exist` is not enabled (R26): mark that account `partial` and name what could not be read.
- Follow each response's `guide` for masking and pagination (R24); the kit's rules win on any conflict.
- If the user will not wait for the full sweep, say which steps were skipped; the status is then `unknown` for those accounts, never clear.

## By hand (when scripts cannot run)

Label every figure "computed by hand" and show the arithmetic.

1. **Dates.** Window = D − 30 through D + 30 calendar days. `do_not_buy_until` = D + 31. Its first trading day skips weekends and NYSE holidays (2026: Nov 26, Dec 25; 2027: Jan 1, Jan 18, Feb 15, Mar 26, May 31, Jun 18, Jul 5, Sep 6, Nov 25, Dec 24). Examples: sold 2026-12-31 → 2027-01-31 (Sunday) → first trading day 2027-02-01; sold 2026-12-01 → 2027-01-01 (holiday) → 2027-01-04.
2. **Trade dates from timestamps.** Convert to US Eastern first (EDT = UTC − 4 until 2026-11-01 02:00, then EST = UTC − 5). `2026-12-17T00:30:00Z` is 2026-12-16 19:30 ET, so its trade date is 12-16. A fill at 20:00 ET or later may carry the next trading day's date: if the two candidates straddle a window edge, a buy is possible and a loss sale is unknown; if both are inside, use the later date for the clean dates.
3. **Loss shares.** Only lots sold below cost: loss per share = cost per share − sale price. Lots sold at a gain are not washed.
4. **Replacement shares.** Buys of S in any account (IRA and Roth included) with a trade date inside the window, except the purchase of the shares being sold (if the sold shares' purchase date is unknown, a same-account buy up to the sale date is possible, not a conflict). Take them earliest first. First remove any buy an earlier loss sale already used: take the loss sales of S from D − 60 in the order they were sold; each uses the earliest buys inside its own window, and a buy that washed one loss cannot wash another (Treas. Reg. 1.1091-1(b), (e)).
5. **Matching.** Walk the remaining buys earliest first. When one sale includes several loss lots, they count as sold in the order they were acquired, earliest first, and the buys match them in that order (Treas. Reg. 1.1091-1(b)–(c)): washed = min(buy shares left, loss shares left). Only if a lot's acquisition date is missing is the order unknown: then show the largest-loss-first figure (the most that can be disallowed) with the smallest-first figure next to it. Disallowed $ = Σ washed × that lot's loss per share. Washed < loss shares means a partial wash.
6. **Permanent.** Washed shares matched to an IRA or Roth buy: that part of the loss is gone for good. Otherwise it is deferred into the replacement shares' basis.
7. **Earliest clean sale date** (only when something washed and the sale is still ahead): the latest in-window buy date + 31, assuming no new buys. For a planned buy: the latest loss-sale date in its window (past or planned) + 31; for a planned sale after the buy, also the last clean buy date (sale date − 31) and the first clean sale date (buy date + 31, which may move the loss into next year).
8. **Status.** Conflict if anything washed (including a harvest the user declared inside a planned buy's window); otherwise unknown if any account, page or loss row could not be confirmed; otherwise possible if any inferred or option item exists, if the planned purchase is an option to acquire and a loss sale is in its window, or if a planned buy meets an open taxable lot below cost; otherwise clear (only when every account was read).

Example: 20 TSLA sold 2026-11-16 at $262.00 from a $340.00 lot (loss $78.00/share, $1,560.00). The Roth IRA bought 5 TSLA on 2026-11-06, inside 2026-10-17 → 2026-12-16. Washed 5 × $78.00 = $390.00, permanently; clean 15 shares ($1,170.00). Earliest clean sale 2026-11-06 + 31 = 2026-12-07. Don't buy back in any account until 2026-12-17.
