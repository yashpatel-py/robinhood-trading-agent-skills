# Response fields observed on the live connector

Captured 2026-09-22 (pre-market, ~05:35 ET) from `https://agent.robinhood.com/mcp/trading` with read-only
calls plus one `review_equity_order` simulation. **Field names and types only — no values from the
capture account are recorded here.** "From guide" means the field is named in the response's `guide`
text but was not present in the payload (the list was empty).

## Envelope (every tool)

Every response is `{"data": {...}, "guide": "<string>"}`. `guide` is Robinhood-authored presentation
and workflow rules for that response (masking, which price field to use, verbatim-disclosure
requirements, pagination). Skills must read and follow it, subordinate to the kit's safety rules.
Tools that are listed by the client but not enabled for the account fail with the error text
`the tool you requested cannot be found or does not exist`.

## get_accounts
`data.accounts[]`: `account_number` (str), `rhs_account_number` (str), `rhc_account_number` (str — the
linked crypto account; guide labels it "Crypto Account"), `type` (`cash` | `margin` | `limited_margin`),
`unsettled_funds` (decimal str), `brokerage_account_type` (e.g. `individual`; IRA values not observed),
`nickname` (optional str), `is_default` (bool), `agentic_allowed` (bool), `option_level`
(e.g. `option_level_2`, `option_level_3`; may be empty), `management_type` (`self_directed` | `managed`),
`affiliate` (str), `state` (str), `deactivated` (bool), `permanently_deactivated` (bool).
Observed: `account_number` == `rhs_account_number` on both capture accounts (so the capture cannot
disambiguate the two; keep the per-tool rule from the schemas).

## get_portfolio
`total_value`, `equity_value`, `options_value`, `futures_value`, `event_contracts_value`,
`crypto_value`, `cash`, `pending_deposits`, `mutual_funds_value`, `fixed_income_value`, `currency`,
`buying_power{buying_power, unleveraged_buying_power, display_currency}`,
`crypto_buying_power{buying_power}` (omitted when unavailable). All decimal strings.

## get_equity_quotes
`data.results[]`: `quote{symbol, last_trade_price, venue_last_trade_time, last_non_reg_trade_price,
venue_last_non_reg_trade_time, adjusted_previous_close, previous_close, previous_close_date,
bid_price, venue_bid_time, ask_price, venue_ask_time, has_traded (bool), state}`,
`close{symbol, date, price, interpolated (bool), source}`. Timestamps are RFC3339 UTC with ns.
(`closes_error` replaces `close` above 20 symbols, per schema.)

## get_indexes / get_index_quotes
`get_indexes`: `indexes[]{id, symbol, name, current_value, trade_halted, updated_at}` (snapshot
fields were empty strings in the capture); `restricted` per guide.
`get_index_quotes`: `quotes[]{instrument_id, symbol, value, state, venue_timestamp, updated_at}`.
**`state` was an empty string** — do not use it as a market-session signal.

## get_equity_fundamentals
`results[]{symbol, open, high, low, volume, overnight_volume, bounds, market_date,
average_volume_2_weeks, average_volume, average_volume_30_days, high_52_weeks, high_52_weeks_date,
low_52_weeks, low_52_weeks_date, float, market_cap, pb_ratio, pe_ratio, shares_outstanding,
dividend_yield (percent units, e.g. "0.31" = 0.31%), dividend_per_share, distribution_frequency,
payable_date, ex_dividend_date, record_date, thirty_day_sec_yield (nullable), description, ceo,
headquarters_city, headquarters_state, sector, industry, num_employees (int), year_founded (int),
financial_status_indicator, financial_status_description}`; unresolved symbols in `not_found`.

## get_equity_analyst_ratings
`results[]` positional to the request: `{symbol, ratings{num_buy_ratings, num_hold_ratings,
num_sell_ratings, high_price_target, low_price_target, mean_price_target, updated_at}}`;
`ratings` null = no coverage; targets can be absent independently of counts.

## get_politician_trades
`trades[]{politician_name, party, position, asset_type, symbol, transaction_type (BUY|SELL),
amount_range{min, max}, transaction_date, disclosure_date, source}`. Guide: always attribute
"Tip Ranks", never present ranges as exact amounts, treat as historical (lag up to 45 days).

## get_sec_filing_index
`{symbol, filings[]{filing_id, form_type, description, date_filed}, next}`.

## get_option_chains
`chains[]{id, symbol, can_open_position, cash_component, expiration_dates[], trade_value_multiplier,
underlying_instruments[]{instrument, symbol}, min_ticks{above_tick, below_tick, cutoff_price},
late_close_state, extended_hours_state, settle_on_open (bool), sellout_time_to_expiration (int s)}`.

## get_option_instruments
`instruments[]{id, chain_id, chain_symbol, underlying_type, expiration_date, sellout_datetime,
strike_price, type, state, tradability, trade_value_multiplier, min_ticks{...}}`, `next`.
`sellout_datetime` = when Robinhood force-closes the position (authoritative per guide).

## get_option_quotes  (screener gate: OPEN)
`results[]`: `quote{instrument_id, ask_price, ask_size (int), bid_price, bid_size (int),
break_even_price, adjusted_mark_price, mark_price, high_fill_rate_buy_price,
low_fill_rate_buy_price, high_fill_rate_sell_price, low_fill_rate_sell_price, previous_close_price,
previous_close_date, implied_volatility (fraction, e.g. "0.24"), delta, gamma, rho, theta, vega,
open_interest (int), volume (int), chance_of_profit_long, chance_of_profit_short, updated_at}`,
`close{instrument_id, symbol, date, price, interpolated, source}`.
Guide: current price = `mark_price` (use `adjusted_mark_price` vs historical cost basis); 1-day P&L =
(mark − close.price) × multiplier × quantity. IV rank is **not** returned.

## get_equity_positions (payload empty in capture; names from guide)
`positions[]{symbol, quantity, intraday_quantity, shares_available_for_sells, average_buy_price
(may be omitted while reconciling), type, …hold breakdowns}`, `next`.
Guide: sellable = `shares_available_for_sells`, not `quantity`.

## get_option_positions (payload empty; names from guide)
`positions[]{chain_symbol, type, quantity, average_price, expiration_date, option_id,
trade_value_multiplier, pending_* quantities}`, `next`.

## get_equity_orders (payload empty; names from guide)
`orders[]{id, symbol, side, type, trigger, state, quantity, dollar_based_amount, cumulative_quantity,
price, stop_price, average_price, placed_agent, created_at, last_transaction_at}`, `next`.

## get_equity_tax_lots (payload empty; names from guide)
`{symbol, tax_lots[]{open_lot_id, quantity, quantity_available, is_selectable, open_date, term,
cost_per_share, tax_cost_basis}}`, next cursor. Newest-acquired first. Absent cost fields = basis
pending (never treat as zero). `is_selectable=false` = still syncing (e.g. acquired today).

## get_pnl_trade_history
`{account_number, span, trades[]{timestamp, symbol, side, quantity, price, realized_gain}, next_cursor}`
(empty `next_cursor` = last page). **There is no asset-class field and no term (short/long) field.**
Option closes appear under the *underlying* ticker with the option premium as `price`; crypto
appears as the base asset (e.g. `BTC`); some rows (e.g. prediction markets or adjustments) have
empty `symbol` and `side`. Do not treat a row as a share sale without cross-checking
`get_equity_orders` (filled sell at the same time/quantity) — see wash-sweep rules.

## get_realized_pnl
`{account_number, window, display_currency, data_points[]{start_time, end_time, realized_gain
(nullable), rate_of_realized_gain (nullable), number_of_trades}, total_returns, total_rate_of_return}`.
Null bucket values = "n/a" (transfer-only buckets), not $0.

## get_alerts
`alerts[]{alert_id, asset_class, symbol, display_name, enabled, condition_type, condition{target_price
| indicator params}, created_at, updated_at}`, `next_cursor`. Crypto alerts carry the base code (BTC).

## get_alert_log
`{events[]{alert_log_id, alert_id, asset_class, symbol, condition_type, trigger{target_price,
triggered_price, direction}, triggered_at, read}, next_cursor, total_unread_count}`.
`mark_alerts_read` takes `alert_log_id`s, never `alert_id`s.

## review_equity_order
`{symbol, side, type, quantity, limit_price, order_checks, quote_data{…same as quote…},
market_data_disclosure}`. **`order_checks` is an object** (`{}` when there are no alerts), e.g.
`{alertType: "EQUITY_NOT_ENOUGH_BP", equityNotEnoughBpAlertDetails: {...}}` — not an array.
**`market_data_disclosure` must be displayed verbatim** ("for compliance reasons", per guide).
Guide: empty `order_checks` does not mean confirmation can be skipped.

## Advanced orders (OCO)
`get_advanced_orders` and `review_advanced_order` returned `the tool you requested cannot be found
or does not exist` on both capture accounts, although the client lists them. Treat the OCO family as
**possibly not enabled per account**: detect this error and fall back (stop order via
`review_equity_order`, and/or native alerts), and never report "no OCOs exist" from a failed call.
