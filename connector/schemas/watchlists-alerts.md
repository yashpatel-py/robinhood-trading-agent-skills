# Robinhood Trading MCP — Watchlists and alerts (verbatim schemas)

Source: ToolSearch schema load of the Robinhood Trading MCP connector (server id 00000000-0000-4000-8000-000000000000), 2026-09-21. Descriptions and input schemas are copied verbatim. No tool annotations (readOnlyHint/destructiveHint/etc.) were present in the loaded schemas.

## get_watchlists

List the user's watchlists, including both user-created custom lists and Robinhood-curated lists the user follows. Use to look up list_id values for other watchlist tools.

```json
{
  "additionalProperties": false,
  "type": "object"
}
```

## get_watchlist_items

List the items in a watchlist. Items may be stocks/ETFs, crypto pairs, futures, indexes — distinguished by object_type. For the options watchlist, use get_option_watchlist instead — this tool returns a generic shape that drops the strategy-specific fields and the upstream rejects it with 400 anyway. Does not return live prices; call get_quotes with the symbol(s) for that.

```json
{
  "additionalProperties": false,
  "properties": {
    "list_id": {
      "description": "UUID of the watchlist whose items to fetch. Obtain from get_watchlists or get_popular_watchlists.",
      "type": "string"
    }
  },
  "required": [
    "list_id"
  ],
  "type": "object"
}
```

## create_watchlist

Create a new custom watchlist for the user — a real write. When the user has already specified the name, call this directly; ask for a name first only when they have not. Do not use this to follow a Robinhood-curated list (use follow_watchlist).

```json
{
  "additionalProperties": false,
  "properties": {
    "display_description": {
      "description": "Short description shown under the name.",
      "type": "string"
    },
    "display_name": {
      "description": "Name for the new watchlist (e.g. 'Tech Stocks'). Must be unique among the user's watchlists.",
      "type": "string"
    },
    "icon_emoji": {
      "description": "Emoji shown next to the name (one character).",
      "type": "string"
    }
  },
  "required": [
    "display_name"
  ],
  "type": "object"
}
```

## update_watchlist

Rename a custom watchlist or change its icon/description. Robinhood-curated lists cannot be renamed; the call will fail with 404. Provide at least one of display_name, icon_emoji, display_description.

```json
{
  "additionalProperties": false,
  "properties": {
    "display_description": {
      "description": "New description.",
      "type": "string"
    },
    "display_name": {
      "description": "New name for the watchlist.",
      "type": "string"
    },
    "icon_emoji": {
      "description": "New emoji.",
      "type": "string"
    },
    "list_id": {
      "description": "UUID of the watchlist to update. Obtain from get_watchlists.",
      "type": "string"
    }
  },
  "required": [
    "list_id"
  ],
  "type": "object"
}
```

## add_to_watchlist

Add items to a watchlist. Exactly one of symbols (stocks/ETFs), currency_pair_ids (crypto), or index_ids (market indexes like SPX, NDX) is required — mutually exclusive. For options use add_option_to_watchlist (separate dedicated watchlist). Futures still require the Robinhood app. Already-present items are no-ops. Confirm with the user before calling.

```json
{
  "additionalProperties": false,
  "properties": {
    "currency_pair_ids": {
      "description": "Currency-pair UUIDs to add (e.g. the object_id from get_watchlist_items where object_type=currency_pair, or the id from get_currency_pairs). Mutually exclusive with symbols and index_ids.",
      "items": {
        "type": "string"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "index_ids": {
      "description": "Market-index UUIDs to add (the id field from get_indexes; SPX, NDX, DJI, etc.). Mutually exclusive with symbols and currency_pair_ids.",
      "items": {
        "type": "string"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "list_id": {
      "description": "UUID of the watchlist to add items to.",
      "type": "string"
    },
    "symbols": {
      "description": "Stock symbols to add (e.g. ['AAPL', 'NVDA']). US stocks and ETFs only. Mutually exclusive with currency_pair_ids and index_ids.",
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
    "list_id"
  ],
  "type": "object"
}
```

## remove_from_watchlist

Remove items from a watchlist. Exactly one of symbols (stocks/ETFs), currency_pair_ids (crypto), or index_ids (market indexes) is required — mutually exclusive. For options use remove_option_from_watchlist. Items not on the list are no-ops (not errors). Confirm with the user before calling.

```json
{
  "additionalProperties": false,
  "properties": {
    "currency_pair_ids": {
      "description": "Currency-pair UUIDs to remove. Mutually exclusive with symbols and index_ids.",
      "items": {
        "type": "string"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "index_ids": {
      "description": "Index UUIDs to remove. Mutually exclusive with symbols and currency_pair_ids.",
      "items": {
        "type": "string"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "list_id": {
      "description": "UUID of the watchlist to remove items from.",
      "type": "string"
    },
    "symbols": {
      "description": "Stock symbols to remove (e.g. ['AAPL']). Mutually exclusive with currency_pair_ids and index_ids.",
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
    "list_id"
  ],
  "type": "object"
}
```

## add_option_to_watchlist

Add option contracts to the user's options watchlist. Works for both equity options (AAPL, NVDA) and index options (SPX, NDX, RUT). Source option_ids from get_option_instruments. Confirm with the user before calling — this is a real write.

```json
{
  "additionalProperties": false,
  "properties": {
    "option_ids": {
      "description": "Option contract UUIDs to add. Each becomes a single-leg position on the user's options watchlist. Source from get_option_instruments.",
      "items": {
        "type": "string"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "position_type": {
      "description": "\"long\" (default) or \"short\". Applies to every option_id in this call. For mixed long/short adds, issue two calls.",
      "type": "string"
    }
  },
  "required": [
    "option_ids"
  ],
  "type": "object"
}
```

## remove_option_from_watchlist

Remove option contracts from the user's options watchlist. Specify the same position_type used when the contract was added (defaults to "long"). Contracts not on the list are no-ops. Confirm with the user before calling.

```json
{
  "additionalProperties": false,
  "properties": {
    "option_ids": {
      "description": "Option contract UUIDs to remove. The position_type must match how each contract was added (most likely \"long\").",
      "items": {
        "type": "string"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "position_type": {
      "description": "\"long\" (default) or \"short\". Must match how the contract was originally added.",
      "type": "string"
    }
  },
  "required": [
    "option_ids"
  ],
  "type": "object"
}
```

## get_option_watchlist

List the single-leg option contracts on the user's options watchlist. Use this instead of get_watchlist_items for the options watchlist — get_watchlist_items returns a generic shape that drops the option-specific title and the upstream rejects it with 400 anyway. Works for both equity options (AAPL, NVDA) and index options (SPX, NDX, RUT). Multi-leg strategies (verticals, condors, etc.) that may exist in the user's watchlist from app-side order placement are not shown — direct the user to the Robinhood app to view those.

```json
{
  "additionalProperties": false,
  "type": "object"
}
```

## get_popular_watchlists

Discover Robinhood-curated lists the user can follow (e.g. '100 Most Popular', 'Daily Movers'). Use to find a list_id, then pass it to follow_watchlist.

```json
{
  "additionalProperties": false,
  "type": "object"
}
```

## follow_watchlist

Follow a Robinhood-curated list so it appears in the user's watchlists. Confirm with the user before calling. Use only for curated lists; the user already owns their custom lists.

```json
{
  "additionalProperties": false,
  "properties": {
    "list_id": {
      "description": "UUID of the Robinhood-curated list to follow. Obtain from get_popular_watchlists.",
      "type": "string"
    }
  },
  "required": [
    "list_id"
  ],
  "type": "object"
}
```

## unfollow_watchlist

Stop following a Robinhood-curated list. The list itself is unchanged — it just no longer appears in the user's watchlists. Confirm with the user before calling.

```json
{
  "additionalProperties": false,
  "properties": {
    "list_id": {
      "description": "UUID of the Robinhood-curated list to unfollow.",
      "type": "string"
    }
  },
  "required": [
    "list_id"
  ],
  "type": "object"
}
```

## create_alert

Create a price or indicator alert on an equity or crypto symbol. Confirm the symbol and condition with the user before calling — this is a real write; when it fires the user gets their usual Robinhood notification. Parameter rules: - price_above / price_below / price_crosses: threshold is the trigger price. Do not send indicator. - sma_above, sma_below, sma_crosses, ema_above, ema_below, ema_crosses, vwap_above, vwap_below, vwap_crosses, rsi_above, rsi_below, rsi_crosses (the indicator's own value vs your target): threshold is the target indicator value AND indicator is required. - price_above_sma, price_below_sma, price_crosses_sma, price_above_ema, price_below_ema, price_crosses_ema, price_above_vwap, price_below_vwap, price_crosses_vwap, price_above_boll_upper, price_below_boll_lower, price_crosses_boll_mid, macd_above_signal, macd_below_signal, macd_crosses_signal (price vs indicator line): indicator only — do not send threshold. - Pick the family by WHAT crosses WHAT: sma_crosses means the SMA's own value crosses your numeric threshold; price_crosses_sma means the market price crosses the SMA line and takes no threshold. "Alert me when the price crosses the 50-day SMA" is price_crosses_sma, never sma_crosses — a mixed-up sibling still creates a well-formed but wrong alert. - indicator fields: period + interval_secs for sma/ema/rsi; interval_secs only for vwap (must be 300 / 5m bars); fast_period + slow_period + signal_period + interval_secs for macd; period + std_dev + ma_type + interval_secs for boll. - Crypto symbols support price conditions only.

```json
{
  "additionalProperties": false,
  "properties": {
    "asset_class": {
      "description": "Optional: equity or crypto, pinning which asset the symbol names. When omitted the symbol is resolved as an equity first, then as crypto.",
      "type": "string"
    },
    "condition_type": {
      "description": "The alert condition: price_above, price_below, price_crosses, sma_above, sma_below, sma_crosses, ema_above, ema_below, ema_crosses, vwap_above, vwap_below, vwap_crosses, rsi_above, rsi_below, rsi_crosses, price_above_sma, price_below_sma, price_crosses_sma, price_above_ema, price_below_ema, price_crosses_ema, price_above_vwap, price_below_vwap, price_crosses_vwap, price_above_boll_upper, price_below_boll_lower, price_crosses_boll_mid, macd_above_signal, macd_below_signal, or macd_crosses_signal. Same vocabulary get_alerts returns in condition_type.",
      "type": "string"
    },
    "indicator": {
      "additionalProperties": false,
      "description": "Indicator configuration. Required for every indicator condition; not used for price conditions.",
      "properties": {
        "fast_period": {
          "description": "macd only: fast EMA period (commonly 12).",
          "maximum": 2147483647,
          "minimum": -2147483648,
          "type": "integer"
        },
        "interval_secs": {
          "description": "Bar size in seconds — one of 300 (5m), 600 (10m), 3600 (1h), 86400 (1d), 604800 (1w), or 2592000 (30d). Required for every indicator condition; vwap conditions support 300 only.",
          "maximum": 2147483647,
          "minimum": -2147483648,
          "type": "integer"
        },
        "ma_type": {
          "description": "boll only: which moving average the bands are built on — sma or ema.",
          "type": "string"
        },
        "period": {
          "description": "Lookback window in bars. Required for sma/ema/rsi/boll conditions.",
          "maximum": 2147483647,
          "minimum": -2147483648,
          "type": "integer"
        },
        "signal_period": {
          "description": "macd only: signal-line EMA period (commonly 9).",
          "maximum": 2147483647,
          "minimum": -2147483648,
          "type": "integer"
        },
        "slow_period": {
          "description": "macd only: slow EMA period (commonly 26).",
          "maximum": 2147483647,
          "minimum": -2147483648,
          "type": "integer"
        },
        "std_dev": {
          "description": "boll only: number of standard deviations for the bands (commonly 2).",
          "type": "number"
        }
      },
      "required": [
        "interval_secs"
      ],
      "type": [
        "null",
        "object"
      ]
    },
    "symbol": {
      "description": "The equity symbol or crypto asset the alert watches, e.g. AAPL or BTC.",
      "type": "string"
    },
    "threshold": {
      "description": "Decimal string: the trigger price for price conditions, or the target indicator value for sma_above-style conditions. Not used for price-vs-indicator-line conditions.",
      "type": "string"
    }
  },
  "required": [
    "symbol",
    "condition_type"
  ],
  "type": "object"
}
```

## update_alert

Change an existing price or indicator alert: enable/disable it, adjust its threshold or indicator settings, or switch its condition within the same family. Confirm the change with the user before calling — this is a real write. Parameter rules: - At least one of enabled, condition_type, threshold, or indicator is required; only the fields you send change, the rest keep their stored values. - condition_type may only change within the alert's current condition family — e.g. price_above -> price_below, or sma_above -> price_above_sma (both sma family). To change the indicator family or the symbol, create a new alert instead. - threshold/indicator rules per condition family match create_alert; conditions comparing price to an indicator line take no threshold.

```json
{
  "additionalProperties": false,
  "properties": {
    "alert_id": {
      "description": "The alert to change — take it from get_alerts or create_alert; never construct one.",
      "type": "string"
    },
    "condition_type": {
      "description": "New condition for the alert — same vocabulary as create_alert. Omitted = unchanged.",
      "type": "string"
    },
    "enabled": {
      "description": "Set false to pause the alert or true to re-enable it. Omitted = unchanged.",
      "type": [
        "null",
        "boolean"
      ]
    },
    "indicator": {
      "additionalProperties": false,
      "description": "New indicator configuration — replaces the stored one wholesale, so send the complete configuration when provided. Omitted = unchanged.",
      "properties": {
        "fast_period": {
          "description": "macd only: fast EMA period (commonly 12).",
          "maximum": 2147483647,
          "minimum": -2147483648,
          "type": "integer"
        },
        "interval_secs": {
          "description": "Bar size in seconds — one of 300 (5m), 600 (10m), 3600 (1h), 86400 (1d), 604800 (1w), or 2592000 (30d). Required for every indicator condition; vwap conditions support 300 only.",
          "maximum": 2147483647,
          "minimum": -2147483648,
          "type": "integer"
        },
        "ma_type": {
          "description": "boll only: which moving average the bands are built on — sma or ema.",
          "type": "string"
        },
        "period": {
          "description": "Lookback window in bars. Required for sma/ema/rsi/boll conditions.",
          "maximum": 2147483647,
          "minimum": -2147483648,
          "type": "integer"
        },
        "signal_period": {
          "description": "macd only: signal-line EMA period (commonly 9).",
          "maximum": 2147483647,
          "minimum": -2147483648,
          "type": "integer"
        },
        "slow_period": {
          "description": "macd only: slow EMA period (commonly 26).",
          "maximum": 2147483647,
          "minimum": -2147483648,
          "type": "integer"
        },
        "std_dev": {
          "description": "boll only: number of standard deviations for the bands (commonly 2).",
          "type": "number"
        }
      },
      "required": [
        "interval_secs"
      ],
      "type": [
        "null",
        "object"
      ]
    },
    "threshold": {
      "description": "New trigger price or target indicator value, as a decimal string. Omitted = unchanged.",
      "type": "string"
    }
  },
  "required": [
    "alert_id"
  ],
  "type": "object"
}
```

## delete_alert

Permanently delete a price or indicator alert, with a server-enforced confirmation step: a call without confirm deletes nothing and returns a preview of exactly what would be deleted; show that preview to the user, and only after they approve call again with confirm=true. There is no undo. If the user only wants to stop notifications for a while, disable the alert with update_alert (enabled=false) instead of deleting it.

```json
{
  "additionalProperties": false,
  "properties": {
    "alert_id": {
      "description": "The alert to delete — take it from get_alerts or create_alert; never construct one.",
      "type": "string"
    },
    "confirm": {
      "description": "Set true only after the user approved the preview returned by a prior call without confirm. Omitted or false = preview only: nothing is deleted.",
      "type": "boolean"
    }
  },
  "required": [
    "alert_id"
  ],
  "type": "object"
}
```

## get_alerts

List the price/indicator alerts the user currently has configured (equities and crypto only), optionally filtered to one symbol. Use to look up alert_id values for update_alert/delete_alert, or to check what's currently being watched.

```json
{
  "additionalProperties": false,
  "properties": {
    "asset_class": {
      "description": "Only alongside symbol: equity or crypto, pinning which asset the symbol names. When omitted the symbol is resolved as an equity first, then as crypto. Cannot be used without symbol.",
      "type": "string"
    },
    "cursor": {
      "description": "Pagination cursor from a previous response's next_cursor. Omit for the first page.",
      "type": "string"
    },
    "symbol": {
      "description": "Filter to one asset's alerts, e.g. AAPL or BTC. Omit for all alerts.",
      "type": "string"
    }
  },
  "type": "object"
}
```

## get_alert_log

Read the log of fired alerts — what fired, when, and at what price or indicator value — with read/unread state. Poll this to learn whether any alert has fired; alert events are not pushed to the agent.

```json
{
  "additionalProperties": false,
  "properties": {
    "asset_class": {
      "description": "Optional: equity or crypto, limiting the log to one asset class. Omitted = all classes.",
      "type": "string"
    },
    "cursor": {
      "description": "Opaque pagination token from a prior response's next_cursor. Omit for the first page. When passing a cursor, repeat the same asset_class, since, and limit as the call that returned it — the token's shape depends on which index served that page, and changing filters between pages causes an upstream error.",
      "type": "string"
    },
    "limit": {
      "description": "Max events per page, 1-100. Omitted = 20. Raise it (e.g. to 100) to collect a full mark_alerts_read batch in one page.",
      "maximum": 2147483647,
      "minimum": -2147483648,
      "type": "integer"
    },
    "since": {
      "description": "Optional RFC3339 timestamp: only events triggered at or after this instant. Omitted = the full retention window.",
      "type": "string"
    }
  },
  "type": "object"
}
```

## mark_alerts_read

Mark fired alerts as read so already-handled events are not reported to the user twice. Call it after relaying fired alerts from get_alert_log. Parameter rules: - Provide exactly one of alert_log_ids or all_through. - alert_log_ids marks specific events; all_through marks every event triggered at or before that instant, across ALL symbols — not only the ones just discussed.

```json
{
  "additionalProperties": false,
  "properties": {
    "alert_log_ids": {
      "description": "Fired-alert event ids from get_alert_log's alert_log_id (never alert_id). At most 100 per call — the server enforces the exact cap and rejects oversized batches naming the current limit.",
      "items": {
        "type": "string"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "all_through": {
      "description": "RFC3339 timestamp: mark every event triggered at or before this instant as read, across all symbols. Set it to the triggered_at of the newest event you relayed so later events stay unread. Never a future timestamp — the upstream rejects it, and one would silently suppress unread state for alerts that have not fired yet.",
      "type": "string"
    }
  },
  "type": "object"
}
```
