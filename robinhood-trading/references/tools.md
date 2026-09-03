# Tool map

Every tool the Robinhood Trading MCP connector exposes, by family. Where a tool has a trap
worth knowing before you call it, it's noted inline.

Some tool descriptions reference companion tools that aren't exposed here (`preview_scan`,
`get_scanner_datapoints`, `replace_option_order`). Treat those as unavailable and work
within what's listed below.

## Accounts

| Tool | Notes |
|---|---|
| `get_accounts` | Call once per session and cache. Exactly one account is tradable by you. **Not a reliable buying-power source.** |
| `get_portfolio` | Market value by asset type and buying power. The correct answer to "how much can I spend." |

**Which account-number field?** Two fields exist and they are not interchangeable in
general: `account_number` (alphanumeric) and `rhs_account_number` (numeric). They happen to
be identical on some accounts, so a call that works isn't proof you used the right one.
Read each tool's parameter description rather than pattern-matching — crypto flows
(`get_crypto_positions`, `preview_crypto_order`, `place_crypto_order`) and `get_realized_pnl`
name `rhs_account_number`; equity, option, and portfolio tools take `account_number`.

Mask to the last four digits in anything the user reads; pass the full value to tools.

## Market data

| Tool | Notes |
|---|---|
| `get_equity_quotes` | Batch symbols. Above 20, quotes still return but the official closes are omitted with `closes_error` set. |
| `get_equity_price_book` | Order-book depth. |
| `get_equity_historicals` | Raw OHLCV bars. |
| `get_equity_technical_indicators` | `interval` and `start_time` required. See `research.md`. |
| `get_equity_tradability` | Halts and restrictions — check before analyzing anything unusual. |
| `get_indexes`, `get_index_quotes`, `get_index_historicals` | Benchmark context. |
| `get_option_chains` | Expirations and contract sets per underlying. `extended_hours_state` here gates CURB option sessions. |
| `get_option_instruments` | Source of `option_id` for order legs. |
| `get_option_quotes`, `get_option_historicals` | Contract-level pricing. |
| `get_crypto_quotes`, `get_currency_pairs` | Crypto pricing and what's tradable. |
| `search` | Name → symbol / instrument ID. Use it instead of guessing tickers. |

## Research

| Tool | Notes |
|---|---|
| `get_equity_fundamentals` | Standing company profile. |
| `get_financials` | Income statement, balance sheet, cash flow. |
| `get_equity_news` | Headlines. |
| `get_earnings_calendar`, `get_earnings_results` | Upcoming and reported. |
| `get_sec_filing_index` | Find `filing_id`s. Start here for filings. |
| `get_sec_filing_facts` | Facts by GAAP concept. Guess concept names first. |
| `get_sec_filing_facts_catalog` | Fallback when a guessed concept returns nothing. |
| `get_sec_filing` | The document itself. |

## Screening

| Tool | Notes |
|---|---|
| `get_scanner_filter_specs` | **Call first, always.** No parameters. Filter names can't be guessed. |
| `create_scan` | Also updates, with `scan_id` — REPLACE semantics, so read current state via `get_scans` first. |
| `get_scans`, `run_scan` | Results are live at request time. |
| `update_scan_filters` | Enum filters only; rejects expressions. |
| `update_scan_config` | Scan configuration. |

## Portfolio and performance

| Tool | Notes |
|---|---|
| `get_equity_positions`, `get_option_positions`, `get_crypto_positions` | Crypto takes `rhs_account_number`. |
| `get_realized_pnl` | Bucketed aggregates, `rhs_account_number`, default span 3 months. |
| `get_pnl_trade_history` | Trade-level detail. |
| `get_equity_tax_lots` | Per symbol, one symbol per call, paginated by `cursor`. |

## Orders

| Simulate (safe) | Place (do not call) | Inspect | Cancel (confirm first) |
|---|---|---|---|
| `review_equity_order` | `place_equity_order` | `get_equity_orders` | `cancel_equity_order` |
| `review_option_order` | `place_option_order`, `exercise_option` | `get_option_orders` | `cancel_option_order`, `cancel_option_exercise` |
| `preview_crypto_order` | `place_crypto_order` | `get_crypto_orders` | `cancel_crypto_order` |

Order construction rules live in `order-mechanics.md`. The short version: check the
session, check the type, surface every `order_checks` alert verbatim, and hand the placing
to the user.

## Watchlists

`get_watchlists`, `get_watchlist_items`, `create_watchlist`, `update_watchlist`,
`add_to_watchlist`, `remove_from_watchlist`, `add_option_to_watchlist`,
`remove_option_from_watchlist`, `get_option_watchlist`, `get_popular_watchlists`,
`follow_watchlist`, `unfollow_watchlist`.

## Enrollment and upgrades

| Tool | When |
|---|---|
| `get_option_level_upgrade_info` | Options wanted, account has empty or level-0 options. |
| `get_limited_margin_upgrade_info` | A `cash` account has unsettled funds > 0 — those proceeds could trade immediately with limited margin. Only mention this when the tool is available; there's no fallback. |
| `get_crypto_account_onboarding_info` | Crypto wanted, no crypto account. |
