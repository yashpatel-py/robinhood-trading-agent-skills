# Workflows A–G in full

*Unofficial — not affiliated with Robinhood.* Read with `SKILL.md`. Tool sequences use bare tool names (your client may prefix them). Every list is read to its last page: `cursor` from the `next` URL for positions, lots and orders; `next_cursor` (empty = done) for the trade history.

## Shared setup (every workflow)

1. `get_accounts {}`, fresh. Build the account table: last 4 of `account_number`, label (nickname, or Agentic for the `agentic_allowed` account, Individual, IRA, Roth), `type` taxable or retirement (sweep step 0), and keep `rhs_account_number` aside for the P&L tools. Never show full numbers.
2. Read scope per R3 and `references/wash-sweep.md` step 0: with no config (or `read_scope` unset) the tax request is the consent; `all` reads every account without asking; `ask` asks once per session before reading beyond the Agentic account, and a scheduled `ask` run reads the Agentic account plus the accounts its prompt names; `agentic_only` reads the Agentic account plus one the user names. An unread account means nothing can be fully clear. Every report names the accounts read and says the data went to the AI provider.
3. `kitconfig.py get` with `{"section": "tax"}`, `{"section": "accounts"}` and `{"section": "policy"}`, each with `"cwd": "<the user's project directory>"`. `CONFIG_NOT_FOUND` is fine: use the defaults and say which ones. `PROJECT_DIR_UNKNOWN` means `cwd` was missing: pass it and run again.
4. The clock: a date the user states, else `python3 scripts/rh_time.py session` (US Eastern).

Why a fresh `get_accounts`: an account opened since the last call (a new Agentic account, an IRA rollover) is exactly the one that washes a loss unseen.

## A. Wash check for one trade

Run `references/wash-sweep.md` steps 0–9. Script input for a **sale**:

```json
{"mode": "sale", "symbol": "TSLA", "as_of": "2026-11-16",
 "sale": {"date": "2026-11-16", "account_last4": "M7Q5", "price_per_share": "262.00",
          "lots_sold": [{"lot_id": "L7", "acquired": "2026-06-02", "shares": "20", "cost_per_share": "340.00"}]},
 "accounts": [{"last4": "X4F1", "type": "taxable", "read_status": "complete", "label": "Agentic"},
              {"last4": "M7Q5", "type": "taxable", "read_status": "complete", "label": "Individual"},
              {"last4": "P0Z9", "type": "retirement", "read_status": "complete", "label": "Roth IRA"}],
 "per_account": [{"account_last4": "P0Z9", "orders": ["<every get_equity_orders row, all pages>"],
                  "tax_lots": ["<every get_equity_tax_lots row>"]},
                 {"account_last4": "M7Q5", "orders": [], "tax_lots": []},
                 {"account_last4": "X4F1", "orders": [], "tax_lots": []}],
 "option_buys": [], "drip": [], "related_buys": [], "declared_related": []}
```

- `price_per_share` is the user's limit, or the quote for an estimate (say which).
- `lots_sold` lists the lots being sold: the user's choice from workflow C, or Robinhood's FIFO default (oldest first) when the user named none. The order they are listed in does not change the dollars: lots sold together count as sold earliest acquired first, and a partial wash lands on them in that order (Treas. Reg. §1.1091-1(b)–(c)). Only when a lot's acquisition date is missing is the order unknown: then `disallowed_total_usd` is the largest-loss-first figure and `disallowed_low_usd` the smallest-first one; show both (`matching_note`).
- For a sale that already happened (no open lot left): `"sale": {"date", "account_last4", "shares", "realized_usd"}` from the confirmed trade-history row, plus `"acquired"` (one date) or `"acquired_lots": [{"acquired", "shares"}]` from the user or the FIFO order history. Without it, a same-account buy up to the sale date may be the sold shares' own purchase, so it comes back possible, not as a conflict.
- Run the `window` op with `mode`. For a sale, step 6 always runs: it reads loss sales from `loss_sales_from` (D − 60), because a buy that already washed an earlier loss cannot wash this one (Treas. Reg. §1.1091-1(e)).

For a **planned buy** (a rebuy, or any buy of a stock sold at a loss in the last 30 days):

```json
{"mode": "planned_buy", "symbol": "AMD", "as_of": "2026-11-16",
 "planned_buy": {"date": "2026-11-16", "account_last4": "X4F1", "shares": "3"},
 "accounts": ["<as above>"],
 "per_account": [{"account_last4": "M7Q5", "orders": ["<get_equity_orders rows>"],
                  "pnl_rows": ["<get_pnl_trade_history trades, all pages>"]}]}
```

The script classifies the trade-history rows itself (a loss row counts as a share sale only when a filled equity sell matches it) and returns the washed shares, the disallowed dollars, `earliest_clean_buy_date` and `rewash_until` (a loss sale through that date would be washed by this buy).

Report: sweep step 9 layout (template 1). `status_line` first.

## B. Harvest scan

1. Shared setup. Count the calls first: per taxable account 1 positions call (+ pages), 1 quotes call per 20 symbols, 1 lots call per candidate, the sweep calls per candidate (account and symbol), 2 P&L calls per account. Say the number when it is over 40 and let the user narrow it (one account, a symbol list, `min_loss_usd`).
2. `get_equity_positions {account_number, cursor}` for each taxable account. Keep `symbol`, `quantity`, `shares_available_for_sells`, `average_buy_price` (it may be missing while Robinhood reconciles; then treat the position as a candidate).
3. `get_equity_quotes {symbols: [...]}` (at most 20 per call so the official close comes back). Price = the quote's current price; say "as of <time ET>" and whether the regular session is open.
4. Lots: `get_equity_tax_lots {account_number, symbol, cursor}` for every position priced below its average cost; with 20 or fewer positions in the account, for all of them (a position with an overall gain can still hold a loss lot).
5. One wash sweep per symbol, `mode: "sale"`: `lots_sold` = every candidate loss lot of that symbol from every taxable account, each with its `account_last4` and its `open_lot_id` as `lot_id` (`sale.account_last4` can be any of those accounts: each lot's own `account_last4` decides where it is sold from). TSLA held at a loss in two accounts is one sweep, so both candidates share one pool of replacement shares; two separate sweeps would each claim the same buy, and `harvest_plan.py` then refuses to add their figures (`shared_replacement_buys`, totals left blank). One `get_equity_orders` per account per symbol serves it.
6. YTD: `get_pnl_trade_history {account_number: <rhs VALUE>, span: "ytd", cursor}` and `get_realized_pnl {account_number: <rhs VALUE>, start_date: <Jan 1 of this year>, end_date: <today>}` for every account.
7. `harvest_plan.py run`:

```json
{"as_of": "2026-11-16",
 "accounts": [{"last4": "X4F1", "type": "taxable", "agentic": true, "label": "Agentic", "read_status": "complete"},
              {"last4": "M7Q5", "type": "taxable", "agentic": false, "label": "Individual", "read_status": "complete"},
              {"last4": "P0Z9", "type": "retirement", "agentic": false, "label": "Roth IRA", "read_status": "complete"}],
 "lots": [{"account_last4": "M7Q5", "symbol": "TSLA", "open_lot_id": "L7", "open_date": "2026-06-02",
           "quantity": "20", "cost_per_share": "340.00", "term": "short", "is_selectable": true}],
 "prices": {"TSLA": "262.00"},
 "wash": {"TSLA": "<the wash_sale.py run output for selling every candidate TSLA loss lot, all accounts>"},
 "loss_sales": [{"account_last4": "X4F1", "symbol": "TSLA", "date": "2026-10-20", "realized_usd": "-212.40"}],
 "ytd_realized": {"X4F1": "462.23", "M7Q5": "7950.10", "P0Z9": "1204.00"},
 "min_loss_usd": "UNSET", "sort_by": "harvestable_usd"}
```

- `read_status` is `complete`, `partial`, `failed` or `not_in_scope` (left out by `[policy] read_scope`). With any account `not_in_scope`, the status is at best `clear_in_scope`, printed "CLEAR … (wash checked only in the accounts read; not read, by your choice: …)". The "Not read" line of template 2 lists `accounts_not_in_scope` and `accounts_not_fully_read`.
- `wash` keys are `"SYMBOL"` (the combined sweep) or `"ACCT:SYMBOL"` (a per-account check). A sweep prices only the lots its `per_lot` covers: any other lot comes back `not_checked`. Disallowed dollars are capped at the candidate's loss, with a note when a cap was needed.
- Pass each lot's `term` from `get_equity_tax_lots`. Robinhood's term wins over the date arithmetic; where they differ, the candidate carries `term_split_disputed`, `term_disputed_lots`, `date_term_split` and a `TERM_DISAGREES` note. Show both splits; Robinhood's tax documents govern.
- `loss_sales` is optional: share sales at a loss this year, either the `equity_sale_losses` list from `wash_sale.py classify_pnl` (the YTD trade history with each account's equity orders; see E.3), passed as is, or sales the user states. A lot bought within 30 days of such a sale in another account is listed in `possible_replacement_lots`, whether it shows a loss or a gain: if the loss was washed into it, its basis and holding period are not what Robinhood's lot shows. Without `loss_sales`, `not_evaluated` says the check was not run.

8. `realized_summary.py run` with the trade rows (see E). Report with template 2: the status line, one row per candidate, the retirement list, the YTD arithmetic and the rules note. No "best", no "you should".
9. Tickets only when the user asks, per "Tickets" in `SKILL.md`.

Scheduled runs (weekly Nov 2 – Dec 31): one pass, status line first, `CONNECTOR UNAVAILABLE: no Robinhood tools in this session` when the tools are missing, and never a ticket.

## C. Which lots to sell

1. `get_equity_tax_lots {account_number, symbol, cursor}` (all pages) and `get_equity_quotes {symbols: [S]}`.
2. `lot_select.py compare`:

```json
{"symbol": "TSLA", "sell_quantity": "20", "price": "262.00", "as_of": "2026-11-16",
 "lots": [{"open_lot_id": "L7", "open_date": "2026-06-02", "quantity_available": "20", "cost_per_share": "340.00",
           "term": "short_term", "is_selectable": true},
          {"open_lot_id": "L1", "open_date": "2025-03-10", "quantity_available": "20", "cost_per_share": "280.00",
           "term": "long_term"}]}
```

   Show each strategy's lots, realized $ and short/long split side by side, in the order returned. Say which sort each uses (the `order` text). FIFO carries the label "Robinhood's default".
3. The quantity is the user's; never suggest one. If they ask which is better, give what each choice does to this year's short- and long-term totals and say the answer depends on their other gains and bracket.
4. `lot_select.py validate` with `{"sell_quantity", "tax_lots", "lots", "order": {"side": "sell", "type", "market_hours"}}` before any review.
5. Loss lots in the choice → workflow A before the ticket.

## D. Long-term countdown

1. Positions (the account the user named, or all taxable accounts after consent), lots for the symbols in question, quotes.
2. `holding_period.py run`:

```json
{"as_of": "2026-11-16", "horizon_days": 45, "prices": {"NVDA": "228.10"},
 "lots": [{"lot_id": "N2", "symbol": "NVDA", "account_last4": "M7Q5", "acquired": "2025-12-03", "shares": "40",
           "cost_per_share": "181.50", "term": "short"}]}
```

3. Report: status line, then each lot's term, `long_term_on`, first trading day, days left and unrealized $. A lot at a gain that turns long-term soon is the useful fact; what to do with it is the user's call.

## E. Realized gains this year

1. `get_pnl_trade_history {account_number: <rhs VALUE>, span: "ytd", cursor}` for each account (retirement too, to show them separately), every page.
2. `get_realized_pnl {account_number: <rhs VALUE>, start_date: <Jan 1>, end_date: <today>}` for each account.
3. Optional, when the user wants stocks separated from options and crypto: `get_equity_orders {account_number, created_at_gte: <Jan 1 00:00 ET in UTC>}` (all pages) and `wash_sale.py classify_pnl` with `{"rows", "equity_orders"}`; pass each row's `classification`, `alt_date` and `date_note` on (an overnight or closed-day fill can carry the next trading day's date).
4. `realized_summary.py run`:

```json
{"as_of": "2026-11-16", "window": "ytd",
 "accounts": [{"last4": "X4F1", "type": "taxable", "label": "Agentic"},
              {"last4": "M7Q5", "type": "taxable", "label": "Individual"},
              {"last4": "P0Z9", "type": "retirement", "label": "Roth IRA"}],
 "trades": ["<each trade row with account_last4 added>"],
 "aggregates": {"M7Q5": {"data_points": ["<get_realized_pnl data_points>"]}}}
```

5. Report with template 4. The taxable total excludes IRA and Roth; the short/long split is "not provided by the connector; see Robinhood's tax center"; any reconciliation gap is explained (prediction markets, an unread page, a window mismatch), not hidden.

## F. Crypto

- Answer the rule question from `references/tax-rules-2026.md` §7 with its as-of date: not subject under current law as of 2026-09-22; a bill introduced 2026-06-08 is not law; re-verified every November; the economic-substance caution is unsettled. That is information, not a green light: the user decides.
- Crypto ETFs (listed shares): run workflow A.
- Listing crypto gains and losses: `get_crypto_positions {rhs_account_number, cursor}`, `get_crypto_quotes {symbols: [...]}` (pass `rhs_account_number` when quoting for an order). Report them separately from stocks, in coins, with no wash check.
- A crypto sell or rebuy ticket belongs to the core skill (`preview_crypto_order`); specific crypto lots cannot be chosen through the connector.

## G. Tax-season pack

1. **Rebuy calendar** after harvests: ask where to save it, then

```json
{"items": [{"symbol": "TSLA", "sale_date": "2026-11-16", "do_not_buy_until": "2026-12-17",
            "first_trading_day_after": "2026-12-17"}],
 "calendar_name": "Wash-sale rebuy dates"}
```

   `python3 scripts/rebuy_calendar.py run --out <that path> < input.json`. It refuses to overwrite a file unless `--force`. Put no account numbers in the item text.
2. **Dec 1 look-back:** from 2026-12-01 any buy can wash a 2026-12-31 loss sale. Before any buy of a stock the user plans to harvest this year, run workflow A in `planned_buy` mode with the declared harvest as `planned_sales` (`[{account_last4, date, price_per_share, lots}]`), the taxable accounts' `tax_lots`, and `planned_buy.price_per_share`, and show both dates. Never lead with CLEAR when the harvest date is on or before `rewash_until`: the script returns conflict or possible.
3. **Dec 31 checklist:** last trading day Thu 2026-12-31; trade date, not settlement, sets the year (the Dec 31 sale settles Mon 2027-01-04 and still counts in 2026); a Dec 31 loss can be bought back from Mon 2027-02-01; recurring buys and dividend reinvestment in January still wash it.

## Failure handling

| Situation | What you do |
|---|---|
| A page or account call fails | mark that account `partial` or `failed`; the result is unknown for it; name it |
| `the tool you requested cannot be found or does not exist` | the tool is not enabled for this account (R26); treat as not read, never as empty |
| A lot has no cost | basis pending; exclude it from dollars and say so |
| A trade-history loss row is unmatched | unclassified; that symbol is unknown, never clear |
| The user will not wait for the full sweep | say which steps were skipped; unknown, never clear |
| A retirement account's type is unclear | ask once; do not guess |
