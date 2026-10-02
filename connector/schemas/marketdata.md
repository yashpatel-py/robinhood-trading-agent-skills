# Robinhood Trading MCP — Market data tool schemas (verbatim)

Family: Market data (quotes, book, historicals, technicals, indexes, options chains/quotes, crypto quotes, search).

Source: schemas loaded via ToolSearch from MCP server `mcp__00000000-0000-4000-8000-000000000000` (full tool name = `mcp__00000000-0000-4000-8000-000000000000__<tool_name>`). No tool was invoked. Descriptions and input schemas below are copied verbatim from the loaded definitions. The loaded definitions carried no tool annotations (no readOnlyHint / destructiveHint / idempotentHint / openWorldHint fields were present), and no property carried an `enum` or `default` keyword — enumerations and defaults appear only inside description text.

Tools (15): `get_equity_quotes`, `get_equity_price_book`, `get_equity_historicals`, `get_equity_technical_indicators`, `get_equity_tradability`, `get_indexes`, `get_index_quotes`, `get_index_historicals`, `get_option_chains`, `get_option_instruments`, `get_option_quotes`, `get_option_historicals`, `get_crypto_quotes`, `get_currency_pairs`, `search`

## get_equity_quotes

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_equity_quotes`

### Description

Get real-time stock quotes and the official last-completed-session close for one or more symbols.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "symbols": {
      "description": "One or more stock symbols. Above 20 symbols, quotes still return but closes is omitted with closes_error set.",
      "items": {
        "type": "string"
      },
      "type": [
        "null",
        "array"
      ]
    }
  },
  "required": [
    "symbols"
  ],
  "type": "object"
}
```

## get_equity_price_book

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_equity_price_book`

### Description

Get a real-time bid/ask order book (Level 2) snapshot for one or more equity symbols (max 4), showing the ladder of price levels and resting share size on each side. Use to read supply/demand depth before entering or exiting a position.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "symbols": {
      "description": "One or more stock symbols, max 4 per call.",
      "items": {
        "type": "string"
      },
      "type": [
        "null",
        "array"
      ]
    }
  },
  "required": [
    "symbols"
  ],
  "type": "object"
}
```

## get_equity_historicals

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_equity_historicals`

### Description

Get OHLCV bars for one or more equity symbols across an explicit time range. Use this for charting, "recent activity" questions, and backtesting. The server auto-selects an interval when one is not provided. If the bar's interpolated field is true, bar was synthesized to fill a gap and carry no new information. Parameter rules: - interval is optional; when omitted, the server auto-selects an interval that targets ~2,500 bars across the requested range. Provide an explicit interval only when you need a specific granularity. - interval values are fixed; the server does NOT aggregate intermediate bars. For a custom interval (e.g. 3-minute), request the next-finer fixed interval and aggregate client-side. - bounds defaults to 'regular' (RTH only). Use 'extended' or '24_5' only when the user explicitly asks about extended-hours activity. - adjustment_type defaults to 'split' (split-adjusted, the right default for backtesting). Use 'none' for raw prices, 'all' for split + dividend adjustment. - If the range would produce more bars than the upstream allows at the explicitly requested interval, narrow the range or coarsen the interval — the call is rejected before reaching upstream. The cap does not apply when interval is auto-selected.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "adjustment_type": {
      "description": "Corporate-action adjustment: 'none' (raw prices), 'split' (default; right for backtesting), or 'all' (split + dividend; intraday only).",
      "type": "string"
    },
    "bounds": {
      "description": "Session bounds. One of 'regular' (RTH, default), 'extended', 'trading', '24_5', '24_7', 'hyper_trading'.",
      "type": "string"
    },
    "end_time": {
      "description": "End of the range (RFC3339 UTC). Optional — when omitted, defaults to the current time.",
      "type": "string"
    },
    "interval": {
      "description": "Bar interval. Optional — when omitted, the server picks an interval that targets ~2,500 bars across the requested range. Intraday: 15second, 30second, minute, 5minute, 10minute, 30minute, hour, 4hour. Interday: day, week, month, 3month, 6month, year, 5year, 10year, 20year, 50year. Note: the 1-minute bar is named 'minute' (not '1minute').",
      "type": "string"
    },
    "start_time": {
      "description": "Start of the range (RFC3339 UTC, e.g. '2026-01-01T00:00:00Z'). Required.",
      "type": "string"
    },
    "symbols": {
      "description": "One or more stock symbols (uppercase). Up to 10 per call.",
      "items": {
        "type": "string"
      },
      "type": [
        "null",
        "array"
      ]
    }
  },
  "required": [
    "symbols",
    "start_time"
  ],
  "type": "object"
}
```

## get_equity_technical_indicators

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_equity_technical_indicators`

### Description

Compute a technical indicator (RSI, MACD, Bollinger Bands, moving averages, ATR, VWAP, and more) over one equity symbol's OHLCV bars across a time range. For the raw OHLCV bars themselves, use get_equity_historicals. Parameter rules: - The parameters an indicator accepts depend on type: - period only: ema/sma (default 9); rsi/cci/atr/mfi (default 14); williams_r/adx (default 10); momentum (default 12); roc (default 14); donchian_channels (default 20). - bollinger_bands: period (default 20) + num_std (default 2). - macd: fast_period (12), slow_period (26), signal_period (9). - keltner_channels: period (default 20) + multiplier (default 2). - supertrend: period (default 10) + multiplier (default 3). - pivot_points: method (only 'classic'). - vwap, obv: no parameters. Omit a parameter to use its default. Passing a parameter the chosen type does not accept is rejected. - interval is REQUIRED — indicator periods are counted in bars, so there is no auto-selection. - adjustment_type defaults to 'split'; 'all' (split + dividend) requires a day-or-coarser interval. - If the requested range plus the indicator's warm-up exceeds the per-request bar cap, narrow the range or coarsen the interval.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "adjustment_type": {
      "description": "Corporate-action adjustment: 'none' (raw prices), 'split' (default), or 'all' (split + dividend; requires a day-or-coarser interval).",
      "type": "string"
    },
    "bounds": {
      "description": "Session bounds. One of 'regular' (RTH, default) or 'extended'.",
      "type": "string"
    },
    "end_time": {
      "description": "End of the range (RFC3339 UTC). Optional — defaults to the current time when omitted.",
      "type": "string"
    },
    "fast_period": {
      "description": "Fast EMA period. macd only (default 12).",
      "type": [
        "null",
        "integer"
      ]
    },
    "interval": {
      "description": "Required bar interval the indicator is computed on. Intraday: 15second, 30second, minute, 5minute, 10minute, 30minute, hour, 4hour. Interday: day, week, month, 3month, 6month, year, 5year, 10year, 20year, 50year. The 1-minute bar is named 'minute' (not '1minute').",
      "type": "string"
    },
    "method": {
      "description": "Calculation method. pivot_points only; currently only 'classic'.",
      "type": "string"
    },
    "multiplier": {
      "description": "Band/offset multiplier. keltner_channels (default 2) and supertrend (default 3) only.",
      "type": [
        "null",
        "number"
      ]
    },
    "num_std": {
      "description": "Number of standard deviations for the bands. bollinger_bands only (default 2).",
      "type": [
        "null",
        "number"
      ]
    },
    "output": {
      "description": "How much of the series to return: 'series' (default, full range), 'latest' (most recent bar only), or 'last:N' (most recent N bars). The indicator is always computed over the full range first; this only trims the response.",
      "type": "string"
    },
    "period": {
      "description": "Lookback period in bars. Applies to ema, sma, rsi, momentum, roc, cci, williams_r, atr, mfi, adx, donchian_channels, bollinger_bands, keltner_channels, supertrend. Omit to use the indicator's default.",
      "type": [
        "null",
        "integer"
      ]
    },
    "signal_period": {
      "description": "Signal EMA period. macd only (default 9).",
      "type": [
        "null",
        "integer"
      ]
    },
    "slow_period": {
      "description": "Slow EMA period. macd only (default 26).",
      "type": [
        "null",
        "integer"
      ]
    },
    "start_time": {
      "description": "Start of the range (RFC3339 UTC, e.g. '2026-01-01T00:00:00Z'). Required.",
      "type": "string"
    },
    "symbol": {
      "description": "Stock symbol (uppercase). Exactly one symbol per call.",
      "type": "string"
    },
    "type": {
      "description": "Indicator to compute. One of: ema, sma, rsi, momentum, roc, cci, williams_r, atr, mfi, adx, donchian_channels, bollinger_bands, macd, keltner_channels, supertrend, vwap, obv, pivot_points.",
      "type": "string"
    }
  },
  "required": [
    "symbol",
    "type",
    "interval",
    "start_time"
  ],
  "type": "object"
}
```

## get_equity_tradability

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_equity_tradability`

### Description

Check tradability for up to 10 equity symbols on a given account: per-session eligibility and fractional. Call before placing an order to surface restrictions. Exact-ticker match — no name or partial-ticker resolution.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account number. Must come from the user or be clearly implied — never default from get_accounts.",
      "type": "string"
    },
    "symbols": {
      "description": "Stock symbols, max 10 per call. With more than 10, split across multiple calls of 10 or fewer. Exact-ticker match only.",
      "items": {
        "type": "string"
      },
      "type": [
        "null",
        "array"
      ]
    }
  },
  "required": [
    "account_number",
    "symbols"
  ],
  "type": "object"
}
```

## get_indexes

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_indexes`

### Description

Get index data for market indexes by symbol. Optionally pass a comma-separated list of symbols (e.g. 'SPX,NDX,DJI') to filter results. Omit symbols to return all available indexes.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "symbols": {
      "description": "Comma-separated list of index symbols to look up (e.g. 'SPX,NDX'). Omit to return all available indexes.",
      "type": "string"
    }
  },
  "type": "object"
}
```

## get_index_quotes

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_index_quotes`

### Description

Get real-time values for one or more market indexes by instrument ID. Returns current index level, state, and timestamps.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "instrument_ids": {
      "description": "One or more index instrument IDs (UUIDs) to fetch current values for. Obtain IDs from the get_indexes tool.",
      "items": {
        "type": "string"
      },
      "type": [
        "null",
        "array"
      ]
    }
  },
  "required": [
    "instrument_ids"
  ],
  "type": "object"
}
```

## get_index_historicals

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_index_historicals`

### Description

Get OHLC value bars for one or more market indexes (by instrument UUID) across an explicit time range. Use this for charting an index's history and "recent movement" questions. If the bar's interpolated field is true, bar was synthesized to fill a gap and carry no new information. Parameter rules: - instrument_ids are index instrument UUIDs from get_indexes. Resolve symbols there first; this tool does not accept ticker symbols. - interval is required; pick the coarsest interval that answers the question. If the requested interval would produce too many bars for the range, the call is rejected — narrow the range or coarsen the interval.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "end_time": {
      "description": "End of the range (RFC3339 UTC). Optional — when omitted, defaults to the current time.",
      "type": "string"
    },
    "instrument_ids": {
      "description": "Index instrument UUIDs (from get_indexes). Up to 10 per call.",
      "items": {
        "type": "string"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "interval": {
      "description": "Bar interval. Required — there is no server auto-select for indexes. Intraday: 5second, 15second, 30second, minute, 5minute, 10minute, 30minute, hour, 4hour. Interday: day, week, month, 3month, 6month, year, 5year, 10year, 20year, 50year. Note: the 1-minute bar is named 'minute' (not '1minute').",
      "type": "string"
    },
    "start_time": {
      "description": "Start of the range (RFC3339 UTC, e.g. '2026-01-01T00:00:00Z'). Required.",
      "type": "string"
    }
  },
  "required": [
    "instrument_ids",
    "start_time",
    "interval"
  ],
  "type": "object"
}
```

## get_option_chains

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_option_chains`

### Description

List option chains for one or more underlyings. A chain describes the full set of expiration dates and contracts for a given underlying. One of underlying_symbol or ids is required.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "ids": {
      "description": "Comma-separated chain UUIDs.",
      "type": "string"
    },
    "underlying_symbol": {
      "description": "Ticker filter; covers equity and index underlyings (e.g. 'AAPL', 'SPX').",
      "type": "string"
    }
  },
  "type": "object"
}
```

## get_option_instruments

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_option_instruments`

### Description

List option contracts. One of chain_symbol, chain_id, or ids is required; narrow further with expiration_dates, strike_price, type, state. When looking up contracts for a specific expiration, call this in parallel for every chain whose expiration_dates (from get_option_chains) includes the date. For AM/PM/morning/evening preferences, first check settle_on_open on each chain via get_option_chains and only query matching chains.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "chain_id": {
      "description": "Chain UUID.",
      "type": "string"
    },
    "chain_symbol": {
      "description": "Underlying ticker (e.g. 'AAPL').",
      "type": "string"
    },
    "cursor": {
      "description": "Pagination cursor. Omit for the first page; for the next page, pass the cursor query param from the prior response's next URL.",
      "type": "string"
    },
    "expiration_dates": {
      "description": "Comma-separated YYYY-MM-DD expirations.",
      "type": "string"
    },
    "ids": {
      "description": "Comma-separated instrument UUIDs.",
      "type": "string"
    },
    "state": {
      "description": "'active' (default), 'expired', or 'inactive'. Use 'expired' to find option contracts whose expiration date has passed; 'inactive' is for delisted/withdrawn contracts that never expired.",
      "type": "string"
    },
    "strike_price": {
      "description": "Exact strike (e.g. '150.0000').",
      "type": "string"
    },
    "tradability": {
      "description": "'tradable' or 'untradable' (untradable is rejected at the tool layer).",
      "type": "string"
    },
    "type": {
      "description": "'call' or 'put'.",
      "type": "string"
    }
  },
  "type": "object"
}
```

## get_option_quotes

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_option_quotes`

### Description

Get real-time quotes for one or more option contracts by instrument UUID, plus the official prior-session close for each.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "instrument_ids": {
      "description": "Option instrument UUIDs. Above 20, quotes still return but closes is omitted with closes_error set.",
      "items": {
        "type": "string"
      },
      "type": [
        "null",
        "array"
      ]
    }
  },
  "required": [
    "instrument_ids"
  ],
  "type": "object"
}
```

## get_option_historicals

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_option_historicals`

### Description

Get OHLC price bars for one or more option contracts (by instrument UUID) across an explicit time range. Use this for charting an option's price history and "recent activity" questions. The server auto-selects an interval when one is not provided. If the bar's interpolated field is true, bar was synthesized to fill a gap and carry no new information. Parameter rules: - instrument_ids are option contract UUIDs from get_option_instruments. Resolve underlying -> get_option_chains -> get_option_instruments first; this tool does not accept ticker symbols. - interval is optional; when omitted the server auto-selects an interval that targets a bounded bar count across the range. Provide an explicit interval only when you need a specific granularity. - bounds defaults to 'regular' (regular hours). Use '24_5'/'24_7' only when the user explicitly asks about extended-hours or overnight option activity. - If an explicitly requested interval would produce more bars than the upstream allows for the range, the call is rejected — narrow the range or coarsen the interval. The cap does not apply when interval is auto-selected.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "bounds": {
      "description": "Session bounds. One of 'regular' (regular hours, default), '24_5', or '24_7'. Use '24_5'/'24_7' only when the user explicitly asks about extended-hours or overnight option activity (index options, and certain equity options).",
      "type": "string"
    },
    "end_time": {
      "description": "End of the range (RFC3339 UTC). Optional — when omitted, defaults to the current time.",
      "type": "string"
    },
    "instrument_ids": {
      "description": "Option contract instrument UUIDs (from get_option_instruments). Up to 10 per call.",
      "items": {
        "type": "string"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "interval": {
      "description": "Bar interval. Optional — when omitted, the server auto-selects an interval that targets a bounded bar count across the range. Intraday: 15second, 30second, minute, 5minute, 10minute, 30minute, hour, 4hour. Interday: day, week, month, 3month, 6month, year, 5year, 10year, 20year, 50year. Note: the 1-minute bar is named 'minute' (not '1minute').",
      "type": "string"
    },
    "start_time": {
      "description": "Start of the range (RFC3339 UTC, e.g. '2026-01-01T00:00:00Z'). Required.",
      "type": "string"
    }
  },
  "required": [
    "instrument_ids",
    "start_time"
  ],
  "type": "object"
}
```

## get_crypto_quotes

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_crypto_quotes`

### Description

Get real-time bid/ask/mark prices plus the previous close for one or more crypto pair symbols (e.g. BTC-USD).

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "rhs_account_number": {
      "description": "Optional numeric brokerage account number (the 'rhs_account_number' from get_accounts). When supplied, bid/ask/mark and the previous close are priced on this account's execution routing — the venues its orders actually fill at — and the applied routing is echoed in routing. When omitted, prices use the caller's own routing (it is set at the user level, so any of their crypto accounts yields the same routing). A caller with no crypto account, or an account that cannot be resolved, receives market-maker-routed pricing.",
      "type": "string"
    },
    "symbols": {
      "description": "One or more crypto pair symbols (e.g. 'BTC-USD', 'ETH-USD'). Hyphenated and unhyphenated forms are both accepted on input; the response symbol field comes back unhyphenated.",
      "items": {
        "type": "string"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "timezone": {
      "description": "Optional IANA timezone name (e.g. 'America/New_York', 'America/Los_Angeles') that anchors the previous-close (open_price) day boundary to the user's local midnight and sets the timezone the response timestamps (bid_time, ask_time, updated_at) are rendered in. Pass ONLY when the user's timezone is known from context — do NOT guess it, infer it from language, or default it. When omitted, the previous close is anchored to midnight US Eastern and timestamps are rendered in US Eastern. Must be a valid IANA name — not an abbreviation ('PST'), a UTC offset ('-08:00'), or 'Local'.",
      "type": "string"
    }
  },
  "required": [
    "symbols"
  ],
  "type": "object"
}
```

## get_currency_pairs

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_currency_pairs`

### Description

List Robinhood-supported crypto currency pairs (e.g. BTC-USD, ETH-USD), including any with an active trading halt. Catalog-only coins that Robinhood does not offer are excluded. Use this to discover symbols and per-pair order constraints before calling get_crypto_quotes or referencing positions.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "cursor": {
      "description": "Pagination cursor. Omit for the first page; for the next page, pass the cursor query param from the prior response's next URL.",
      "type": "string"
    },
    "limit": {
      "description": "Maximum number of pairs to return on this page (clamped to [1, 700]). Defaults to 25 to keep the response sized for an agent's context window; raise it when the user wants the full catalog and is OK with a larger response.",
      "type": "integer"
    }
  },
  "type": "object"
}
```

## search

Full name: `mcp__00000000-0000-4000-8000-000000000000__search`

### Description

Resolve a natural-language query to Robinhood instruments (stocks/ETFs), crypto pairs, or market indexes. Use when the user names an asset by name (or partial name) instead of a ticker/pair/index symbol, or when you need an instrument_id / currency-pair UUID / market-index id for a downstream tool. Defaults to instrument search; pass asset_type="currency_pair" for crypto or asset_type="market_index" for indexes (SPX, NDX, DJI, etc.). Instrument results carry symbol + instrument_id (use with get_equity_quotes / get_equity_tradability / place_equity_order or any instrument_id-based tool). Crypto results carry hyphenated symbol (e.g. BTC-USD) + id — the symbol routes to crypto quote/order tools, the id routes to watchlist tools as currency_pair_ids. Market-index results carry symbol + id — pass id to index quote tools for current values, or in the index_ids array of watchlist tools.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "asset_type": {
      "description": "Asset category to search. Supported: \"instrument\" (US-listed stocks/ETFs), \"currency_pair\" (crypto pairs like BTC-USD), and \"market_index\" (e.g. SPX, NDX, DJI). Defaults to \"instrument\" when omitted. More categories (events, futures) will be added as their corresponding tools land.",
      "type": "string"
    },
    "limit": {
      "description": "Max results to return. Defaults to 10; clamped to 20.",
      "type": "integer"
    },
    "query": {
      "description": "Natural-language search query: company name, partial name, or ticker (e.g. \"apple\", \"tesla motors\", \"AAPL\"). Required.",
      "type": "string"
    }
  },
  "required": [
    "query"
  ],
  "type": "object"
}
```
