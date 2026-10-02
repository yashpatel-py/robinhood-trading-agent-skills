# Robinhood Trading MCP — Equity orders and advanced orders (verbatim schemas)

Source: schemas loaded via ToolSearch from MCP server `mcp__00000000-0000-4000-8000-000000000000__*` on 2026-09-21. No Robinhood tool was invoked.

Notes on fidelity:
- Each description is reproduced verbatim inside a ```text fence (single line in the source; nothing trimmed or reflowed). Typographic characters (em dash, en dash, curly apostrophe, arrows) are preserved as delivered.
- Each input schema is reproduced verbatim (same keys, values, and property order as delivered), pretty-printed.
- No tool annotations (readOnlyHint / destructiveHint / idempotentHint / openWorldHint / title) were present in the loaded schemas for any of these eight tools. No `default` or `enum` keywords appear in any schema; defaults and allowed values exist only as prose inside descriptions.

Tools in this file: review_equity_order, place_equity_order, get_equity_orders, cancel_equity_order, review_advanced_order, place_advanced_order, get_advanced_orders, cancel_advanced_order.

---

## review_equity_order

Description (verbatim):

```text
Simulate a stock order without placing it. Returns the current quote plus pre-trade alerts (buying power, PDT, instrument halt, etc.). Call this by default before place_equity_order unless the user has very explicitly asked to skip review. Requires an agentic_allowed=true account; non-agentic accounts are rejected — do not call. Parameter rules: - If the user has not specified type, ask. For immediate fills with price protection, prefer a marketable limit at the current ask over a plain market. - Outside regular hours, only limit orders execute. For an immediate fill during extended or overnight/24-hour sessions, place a limit order (a marketable limit at the current ask) with market_hours set to that session — not a market order. Market and stop orders are regular_hours-only; placed after hours as regular_hours they queue for the next regular open. - Provide exactly one of quantity or dollar_amount, and take the value from the user — if they did not say how much, ask. Never substitute a default such as 1 share or $100. dollar_amount requires type=market (server computes shares from last_trade_price). - Fractional shares: only on type=market with market_hours=regular_hours, eligible accounts, up to 6 decimal places, no short sells. - limit_price required for limit/stop_limit; stop_price required for stop_market/stop_limit. - Fractional and dollar-based orders only place in regular_hours; the tool rejects them in other sessions. - tax_lots (specified-lot selling, sell only): to sell specific lots, first call get_equity_tax_lots for the symbol, then pass tax_lots as {open_lot_id, quantity} pairs whose quantities sum to the order quantity. Omit for default (FIFO) cost basis. US accounts only; not allowed with dollar_amount, stop orders, all_day_hours, or fractional limit orders.
```

Input schema (verbatim):

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account number. Must come from the user or be clearly implied — never default from get_accounts. Must be agentic_allowed=true; non-agentic accounts are rejected.",
      "type": "string"
    },
    "dollar_amount": {
      "description": "USD notional (e.g. '100.00'). Only valid with type=market.",
      "type": "string"
    },
    "limit_price": {
      "description": "Limit price; required for limit or stop_limit.",
      "type": "string"
    },
    "market_hours": {
      "description": "'regular_hours' (default, 9:30–16:00 ET), 'extended_hours' (pre-/post-market), or 'all_day_hours' (the 24 Hour Market / overnight session). extended_hours and all_day_hours execute limit orders only — market, stop_market, and stop_limit are regular_hours-only and are rejected if tagged to another session.",
      "type": "string"
    },
    "quantity": {
      "description": "Number of shares. Decimals (fractional) allowed for market + regular_hours only.",
      "type": "string"
    },
    "side": {
      "description": "'buy' or 'sell'.",
      "type": "string"
    },
    "stop_price": {
      "description": "Stop trigger price; required for stop_market or stop_limit.",
      "type": "string"
    },
    "symbol": {
      "description": "Stock symbol.",
      "type": "string"
    },
    "tax_lots": {
      "description": "Optional specified-lot selection for a SELL order. To sell specific tax lots instead of the default FIFO cost basis, pass the exact lots as {open_lot_id, quantity} objects, where open_lot_id comes from get_equity_tax_lots and the quantities sum to the order quantity. Omit for default FIFO. Sell only; at most 30 lots; US accounts only. Not allowed with dollar_amount, stop_market/stop_limit, all_day_hours, or fractional-share limit orders.",
      "items": {
        "additionalProperties": false,
        "properties": {
          "open_lot_id": {
            "description": "open_lot_id of the open tax lot to sell, from get_equity_tax_lots.",
            "type": "string"
          },
          "quantity": {
            "description": "Shares to sell from this lot as a decimal string; at most the lot's quantity_available.",
            "type": "string"
          }
        },
        "required": [
          "open_lot_id",
          "quantity"
        ],
        "type": "object"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "time_in_force": {
      "description": "'gfd' (good for day) or 'gtc' (good till cancelled). Default: gfd.",
      "type": "string"
    },
    "type": {
      "description": "'market', 'limit', 'stop_market', or 'stop_limit'.",
      "type": "string"
    }
  },
  "required": [
    "account_number",
    "symbol",
    "side",
    "type"
  ],
  "type": "object"
}
```

---

## place_equity_order

Description (verbatim):

```text
Place a real equity order with real money. Parameters mirror review_equity_order plus the optional ref_id. Requires an agentic_allowed=true account; non-agentic accounts are rejected — do not call. Idempotency: pass a fresh UUID as ref_id on the first call for each logical order, and re-send the SAME ref_id on retries of transient transport failures. Use a new ref_id only when the user wants a new order. Parameter rules: - If the user has not specified type, ask. For immediate fills with price protection, prefer a marketable limit at the current ask over a plain market. - Outside regular hours, only limit orders execute. For an immediate fill during extended or overnight/24-hour sessions, place a limit order (a marketable limit at the current ask) with market_hours set to that session — not a market order. Market and stop orders are regular_hours-only; placed after hours as regular_hours they queue for the next regular open. - Provide exactly one of quantity or dollar_amount, and take the value from the user — if they did not say how much, ask. Never substitute a default such as 1 share or $100. dollar_amount requires type=market (server computes shares from last_trade_price). - Fractional shares: only on type=market with market_hours=regular_hours, eligible accounts, up to 6 decimal places, no short sells. - limit_price required for limit/stop_limit; stop_price required for stop_market/stop_limit. - Fractional and dollar-based orders only place in regular_hours; the tool rejects them in other sessions. - tax_lots (specified-lot selling, sell only): to sell specific lots, first call get_equity_tax_lots for the symbol, then pass tax_lots as {open_lot_id, quantity} pairs whose quantities sum to the order quantity. Omit for default (FIFO) cost basis. US accounts only; not allowed with dollar_amount, stop orders, all_day_hours, or fractional limit orders.
```

Input schema (verbatim):

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account number. Must come from the user or be clearly implied — never default from get_accounts. Must be agentic_allowed=true; non-agentic accounts are rejected.",
      "type": "string"
    },
    "dollar_amount": {
      "description": "USD notional (e.g. '100.00'). Only valid with type=market.",
      "type": "string"
    },
    "limit_price": {
      "description": "Limit price; required for limit or stop_limit.",
      "type": "string"
    },
    "market_hours": {
      "description": "'regular_hours' (default, 9:30–16:00 ET), 'extended_hours' (pre-/post-market), or 'all_day_hours' (the 24 Hour Market / overnight session). extended_hours and all_day_hours execute limit orders only — market, stop_market, and stop_limit are regular_hours-only and are rejected if tagged to another session.",
      "type": "string"
    },
    "quantity": {
      "description": "Number of shares. Decimals (fractional) allowed for market + regular_hours only.",
      "type": "string"
    },
    "ref_id": {
      "description": "Idempotency key (UUID). Generate once per logical order and re-send on retry — the upstream deduplicates by ref_id. Omitting falls back to a server-generated key (loses client↔gateway idempotency).",
      "type": "string"
    },
    "side": {
      "description": "'buy' or 'sell'.",
      "type": "string"
    },
    "stop_price": {
      "description": "Stop trigger price; required for stop_market or stop_limit.",
      "type": "string"
    },
    "symbol": {
      "description": "Stock symbol.",
      "type": "string"
    },
    "tax_lots": {
      "description": "Optional specified-lot selection for a SELL order. To sell specific tax lots instead of the default FIFO cost basis, pass the exact lots as {open_lot_id, quantity} objects, where open_lot_id comes from get_equity_tax_lots and the quantities sum to the order quantity. Omit for default FIFO. Sell only; at most 30 lots; US accounts only. Not allowed with dollar_amount, stop_market/stop_limit, all_day_hours, or fractional-share limit orders.",
      "items": {
        "additionalProperties": false,
        "properties": {
          "open_lot_id": {
            "description": "open_lot_id of the open tax lot to sell, from get_equity_tax_lots.",
            "type": "string"
          },
          "quantity": {
            "description": "Shares to sell from this lot as a decimal string; at most the lot's quantity_available.",
            "type": "string"
          }
        },
        "required": [
          "open_lot_id",
          "quantity"
        ],
        "type": "object"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "time_in_force": {
      "description": "'gfd' or 'gtc'. Default: gfd.",
      "type": "string"
    },
    "type": {
      "description": "'market', 'limit', 'stop_market', or 'stop_limit'.",
      "type": "string"
    }
  },
  "required": [
    "account_number",
    "symbol",
    "side",
    "type"
  ],
  "type": "object"
}
```

---

## get_equity_orders

Description (verbatim):

```text
Fetch equity orders for an account — list mode (newest first; open and closed, including fills, cancellations, rejections) or single-order mode by passing order_id. When the user asks broadly for "orders" or "my orders" without naming an asset class or order type, call get_equity_orders, get_option_orders, get_crypto_orders, and get_advanced_orders in parallel so OCO groups are not omitted. Filtering tips: - Prefer narrow queries: combine state, symbol, and/or created_at_gte for specific questions (e.g. "my filled AAPL orders this week") — the per-page cap is fixed. - created_at_gte: interpret relative times in the user's timezone, convert to UTC before sending. - symbol forces a symbol→instrument lookup; omit it if you don't need it.
```

Input schema (verbatim):

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account number. Must come from the user or be clearly implied — never default from get_accounts.",
      "type": "string"
    },
    "created_at_gte": {
      "description": "Lower bound (inclusive). ISO 8601 UTC or YYYY-MM-DD; naive values are interpreted as UTC.",
      "type": "string"
    },
    "cursor": {
      "description": "Pagination cursor. Omit for the first page; for the next page, pass the cursor query param from the prior response's next URL.",
      "type": "string"
    },
    "order_id": {
      "description": "Filter to a single order by UUID. The response shape is unchanged (orders[] with at most one entry); empty when the order does not belong to account_number.",
      "type": "string"
    },
    "placed_agent": {
      "description": "Filter to one source: 'user', 'agentic' (MCP), 'recurring', 'drip', etc.",
      "type": "string"
    },
    "state": {
      "description": "Filter by single state: new, queued, confirmed, unconfirmed, partially_filled, filled, cancelled, rejected, failed, voided.",
      "type": "string"
    },
    "symbol": {
      "description": "Filter to one symbol (triggers a symbol→instrument lookup before the orders call).",
      "type": "string"
    }
  },
  "required": [
    "account_number"
  ],
  "type": "object"
}
```

---

## cancel_equity_order

Description (verbatim):

```text
Cancel an open equity order by order_id. Always confirm with the user before calling. Resolve order_id via get_equity_orders if the user refers to it by symbol or description; pass the same account_number. Requires an agentic_allowed=true account; non-agentic accounts are rejected — do not call. Cancellation may be rejected if the order has already filled, was already cancelled, or is otherwise ineligible.
```

Input schema (verbatim):

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account that owns the order. Must come from the user or be clearly implied — never default from get_accounts. Must be agentic_allowed=true. The upstream rejects mismatches against the order's owning account.",
      "type": "string"
    },
    "order_id": {
      "description": "Order UUID from get_equity_orders. Must live in account_number.",
      "type": "string"
    }
  },
  "required": [
    "account_number",
    "order_id"
  ],
  "type": "object"
}
```

---

## review_advanced_order

Description (verbatim):

```text
Simulate an OCO equity order without placing it; returns any pre-trade alert (buying power, instrument halt, etc.). Parameters mirror place_advanced_order. Requires an agentic_allowed=true account; non-agentic accounts are rejected — do not call. OCO rules: - Both legs use the same symbol, side, and quantity. - take_profit_limit_price and stop_loss_stop_price are required and must differ. - For a sell OCO (protecting a long): take_profit_limit_price must be ABOVE stop_loss_stop_price. For a buy OCO (covering a short): stop_loss_stop_price must be ABOVE take_profit_limit_price. - time_in_force can be GFD or GTC. market_hours must be regular_hours (the only session OCO supports) — omit it or pass regular_hours explicitly. - take_profit_limit_price and stop_loss_stop_price must each be at least 0.25% away from the current market price, and at least $0.10 apart from each other, or the order will be rejected — don't propose prices tighter than that.
```

Input schema (verbatim):

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account number. Must come from the user or be clearly implied — never default from get_accounts. Must be agentic_allowed=true; non-agentic accounts are rejected.",
      "type": "string"
    },
    "market_hours": {
      "description": "'regular_hours' — the only session OCO supports; extended_hours and all_day_hours are rejected.",
      "type": "string"
    },
    "quantity": {
      "description": "Whole-share quantity. Both legs use the same quantity.",
      "type": "string"
    },
    "side": {
      "description": "'buy' or 'sell'. Both legs use this side.",
      "type": "string"
    },
    "stop_loss_stop_price": {
      "description": "Stop trigger price of the stop-loss leg. The stop-loss leg is always a stop-market order — there is no stop-limit option for it.",
      "type": "string"
    },
    "symbol": {
      "description": "Stock symbol. Both OCO legs are on this symbol.",
      "type": "string"
    },
    "take_profit_limit_price": {
      "description": "Limit price of the take-profit leg.",
      "type": "string"
    },
    "time_in_force": {
      "description": "Can be 'gfd' or 'gtc'.",
      "type": "string"
    }
  },
  "required": [
    "account_number",
    "symbol",
    "side",
    "quantity",
    "take_profit_limit_price",
    "stop_loss_stop_price"
  ],
  "type": "object"
}
```

---

## place_advanced_order

Description (verbatim):

```text
Place an OCO (one-cancels-the-other) equity order with real money: a take-profit limit leg and a stop-loss leg on the same symbol, side, and quantity, where filling one cancels the other. Requires an agentic_allowed=true account; non-agentic accounts are rejected — do not call. Idempotency: pass a fresh UUID as ref_id on the first call and re-send the same ref_id on transient retries. OCO rules: - Both legs use the same symbol, side, and quantity. - take_profit_limit_price and stop_loss_stop_price are required and must differ. - For a sell OCO (protecting a long): take_profit_limit_price must be ABOVE stop_loss_stop_price. For a buy OCO (covering a short): stop_loss_stop_price must be ABOVE take_profit_limit_price. - time_in_force can be GFD or GTC. market_hours must be regular_hours (the only session OCO supports) — omit it or pass regular_hours explicitly. - take_profit_limit_price and stop_loss_stop_price must each be at least 0.25% away from the current market price, and at least $0.10 apart from each other, or the order will be rejected — don't propose prices tighter than that.
```

Input schema (verbatim):

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account number. Must come from the user or be clearly implied — never default from get_accounts. Must be agentic_allowed=true; non-agentic accounts are rejected.",
      "type": "string"
    },
    "market_hours": {
      "description": "'regular_hours' — the only session OCO supports; extended_hours and all_day_hours are rejected. Both legs share it.",
      "type": "string"
    },
    "quantity": {
      "description": "Whole-share quantity. Both legs use the same quantity.",
      "type": "string"
    },
    "ref_id": {
      "description": "Idempotency key (UUID) for the advanced order. Generate once per logical order and re-send on transient retries; a fresh key is generated when omitted.",
      "type": "string"
    },
    "side": {
      "description": "'buy' or 'sell'. Both legs use this side (an exit for an existing position: 'sell' to protect a long, 'buy' to cover a short).",
      "type": "string"
    },
    "stop_loss_stop_price": {
      "description": "Stop trigger price of the stop-loss leg. The stop-loss leg is always a stop-market order — there is no stop-limit option for it.",
      "type": "string"
    },
    "symbol": {
      "description": "Stock symbol. Both OCO legs are on this symbol.",
      "type": "string"
    },
    "take_profit_limit_price": {
      "description": "Limit price of the take-profit leg.",
      "type": "string"
    },
    "time_in_force": {
      "description": "Can be 'gfd' or 'gtc'. Both legs share it.",
      "type": "string"
    }
  },
  "required": [
    "account_number",
    "symbol",
    "side",
    "quantity",
    "take_profit_limit_price",
    "stop_loss_stop_price"
  ],
  "type": "object"
}
```

---

## get_advanced_orders

Description (verbatim):

```text
Fetch advanced orders (OCO — one-cancels-the-other — and other multi-leg contingency orders) for an account, each with its legs' underlying equity orders hydrated. Call this alongside get_equity_orders when you want to get a full picture of the user’s orders.
```

Input schema (verbatim):

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account number. Must come from the user or be clearly implied — never default from get_accounts.",
      "type": "string"
    },
    "contingency_type": {
      "description": "Filter by kind: 'oco', 'oto', or 'fx_at_trade'.",
      "type": "string"
    },
    "created_at_gte": {
      "description": "Lower bound (inclusive). ISO 8601 UTC or YYYY-MM-DD; naive values are interpreted as UTC.",
      "type": "string"
    },
    "cursor": {
      "description": "Pagination cursor. Omit for the first page; for the next page, pass the cursor query param from the prior response's next URL.",
      "type": "string"
    },
    "order_id": {
      "description": "Filter to a single advanced order by its UUID (the id from a prior result).",
      "type": "string"
    }
  },
  "required": [
    "account_number"
  ],
  "type": "object"
}
```

---

## cancel_advanced_order

Description (verbatim):

```text
Cancel an advanced order (e.g. an OCO) by order_id, cancelling all of its legs. Resolve order_id via get_advanced_orders; pass the same account_number. Requires an agentic_allowed=true account; non-agentic accounts are rejected — do not call.
```

Input schema (verbatim):

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account that owns the advanced order. Must come from the user or be clearly implied — never default from get_accounts. Must be agentic_allowed=true. The upstream rejects mismatches against the order's owning account.",
      "type": "string"
    },
    "order_id": {
      "description": "Advanced-order UUID from get_advanced_orders. Must live in account_number.",
      "type": "string"
    }
  },
  "required": [
    "account_number",
    "order_id"
  ],
  "type": "object"
}
```
