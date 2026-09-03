# Research, screening, and monitoring

## Contents
- [Screening with scanners](#screening-with-scanners)
- [The research ladder](#the-research-ladder)
- [SEC filings](#sec-filings)
- [Technical indicators](#technical-indicators)
- [Portfolio and performance](#portfolio-and-performance)
- [Watchlists](#watchlists)

## Screening with scanners

Call `get_scanner_filter_specs` first, every time. It takes no parameters and returns
every valid `filter_type`, the predicates each one supports, and its allowed
`interval` / `length` / `plot` values. Filter names are wire-format enums like
`FILTER_TYPE_RSI` with predicates like `PREDICATE_GREATER_THAN` — not guessable, and a
scan built on invented names either errors or silently matches nothing, which is worse.

Then `create_scan` → `run_scan`. Notes that save real time:

- **Prefer enum filters.** They're pre-validated and render as editable filters in the
  Robinhood UI. Expression filters (raw market-data expressions) are a fallback for when
  no enum covers the request.
- **Columns display, filters screen.** To show a datapoint without restricting results,
  add it to `columns` — never fake it with a no-op filter like `>= 0`, which pollutes the
  scan's filter list and confuses the user looking at it in the app. A couple of
  contextual columns supporting the filters make results much easier to read.
- **Updating a scan replaces, it doesn't merge.** Passing `scan_id` to `create_scan`
  appends a new configuration version holding exactly what you pass. Read the current
  filters and columns with `get_scans` first and pass the complete intended set, or you'll
  silently drop the parts you didn't repeat. Earlier versions are preserved, never edited.
- `update_scan_filters` rejects expression filters — to change a scan that has one, go
  through `create_scan` with its `scan_id`.
- Presets: `INITIAL` (requires filters), `DAILY_GAINERS`, `DAILY_LOSERS`,
  `HIGH_OPTIONS_VOLUME_IV`, `UPCOMING_EARNINGS`. A non-`INITIAL` preset can't be combined
  with expression filters, columns, or `scan_id`.
- Results are computed against live market data at request time. Present them as a table
  and say they're live.

If `create_scan` mentions companion tools that aren't exposed in this session
(`preview_scan`, `get_scanner_datapoints`), that's a signal to stay on enum filters —
there's no way to validate an expression before saving it, and a bad expression means
nothing persists.

## The research ladder

Go only as deep as the question needs. Each rung costs more context than the one above it.

1. **Price and liquidity** — `get_equity_quotes`, `get_equity_price_book` (order book
   depth), `get_equity_tradability` (halts, restrictions). Always the first rung: a halted
   or thinly traded name changes the answer to every other question.
2. **Company shape** — `get_equity_fundamentals` for the standing profile (market cap,
   ratios, sector).
3. **Financial statements** — `get_financials` for the reported income statement, balance
   sheet, and cash flow series.
4. **Catalysts** — `get_equity_news`, `get_earnings_calendar` (what's coming),
   `get_earnings_results` (what landed, and against what expectation).
5. **Primary documents** — SEC filings, below. This is where you go when the question is
   about something the summary numbers can't answer: segment detail, debt schedules,
   related-party transactions, subsequent events, the actual risk-factor language.

`search` resolves names to symbols and instrument IDs. Use it when the user names a
company rather than a ticker rather than guessing the symbol — "Meta" and "MTA" are not
the same thing.

Index-level context comes from `get_indexes`, `get_index_quotes`, and
`get_index_historicals`. A stock down 3% on a day the index is down 3% is a different
story than one down 3% on a flat tape, and that framing is usually worth including.

## SEC filings

Four tools, used in this order:

1. `get_sec_filing_index` — find `filing_id`s for a company.
2. `get_sec_filing_facts` — pull facts by GAAP concept name (1–10 concepts, 1–3 filings).
   **Guess common concept names directly** (`NetIncomeLoss`, `Assets`, `Revenues`); they
   usually hit. Concepts cover disclosure text blocks too, not just numbers — e.g.
   `ScheduleOfDebtTableTextBlock`.
3. `get_sec_filing_facts_catalog` — only when a guessed concept comes back empty. It lists
   what's actually tagged in the filing.
4. `get_sec_filing` — the filing document itself, when you need the language rather than
   the tagged figures.

Default to a **single** `filing_id`. Filings already carry history internally — a 10-K
holds roughly three fiscal years, a 10-Q holds the current period plus the prior-year
comparable. Pass multiple filings only when the question structurally needs it: a trend
longer than three years, or checking whether a disclosed subsequent event actually landed
in the next filing.

## Technical indicators

`get_equity_technical_indicators` computes RSI, MACD, Bollinger Bands, moving averages,
ATR, VWAP, OBV, ADX, Supertrend, Keltner and Donchian channels, CCI, MFI, Williams %R,
momentum, ROC, and classic pivot points. For raw OHLCV bars, use `get_equity_historicals`
instead.

- `interval` is **required** — periods are counted in bars, so there's no sensible
  default. Intraday: `15second`, `30second`, `minute` (not `1minute`), `5minute`,
  `10minute`, `30minute`, `hour`, `4hour`. Interday: `day`, `week`, `month`, and coarser.
- `start_time` is required, RFC3339 UTC. `end_time` defaults to now.
- Parameters are per-indicator and passing an unsupported one is rejected: `period` alone
  for the moving averages and oscillators; `bollinger_bands` takes `period` + `num_std`;
  `macd` takes `fast_period`/`slow_period`/`signal_period`; `keltner_channels` and
  `supertrend` take `period` + `multiplier`; `vwap` and `obv` take none.
- `output` trims the response — `latest` or `last:N` instead of the full series when you
  only need the current reading. The indicator is computed over the full range either way.
- `adjustment_type` defaults to `split`; `all` (split + dividend) needs a day-or-coarser
  interval.
- If range plus warm-up exceeds the bar cap, narrow the range or coarsen the interval.

Present indicators as evidence, not signals. "RSI(14) is 78, its highest since March" is a
fact the user can weigh; "RSI says overbought, time to sell" is a recommendation you
shouldn't be making.

## Portfolio and performance

- `get_portfolio` — market value by asset type **and buying power**. This is the only
  reliable source for "how much can I spend"; `get_accounts` is not.
- `get_equity_positions`, `get_option_positions`, `get_crypto_positions` — current
  holdings. Crypto takes `rhs_account_number`.
- `get_realized_pnl` — bucketed realized gain in dollars and percent plus closing-trade
  counts over a window. Takes `rhs_account_number`. `span` presets: `day`, `week`, `month`,
  `3month` (default), `year`, `all`; or a custom `start_date`/`end_date`. Filter by
  `asset_classes`. Aggregate only — no individual trades.
- `get_pnl_trade_history` — the trade-level detail `get_realized_pnl` doesn't give.
- `get_equity_tax_lots` — per-symbol open lots with cost basis, acquisition date, and
  long/short-term status.

When reporting performance, separate realized from unrealized and say which is which. And
give the denominator: a $4,000 gain means something different against $10,000 deployed
than against $200,000.

## Watchlists

`get_watchlists`, `get_watchlist_items`, `create_watchlist`, `update_watchlist`,
`add_to_watchlist` / `remove_from_watchlist`, the option equivalents
(`add_option_to_watchlist`, `remove_option_from_watchlist`, `get_option_watchlist`), plus
`get_popular_watchlists`, `follow_watchlist`, and `unfollow_watchlist`.

These move no money, so use them freely — building a watchlist out of scan results is a
natural way to finish a screening session. Confirm before removing things the user
curated by hand.
