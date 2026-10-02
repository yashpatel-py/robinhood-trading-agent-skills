# Robinhood Trading MCP: Option orders, exercise, crypto orders (verbatim schemas)

Source: schemas loaded via ToolSearch on 2026-09-21 from MCP server `00000000-0000-4000-8000-000000000000` (Robinhood Trading). No Robinhood tool was invoked. Descriptions are copied verbatim inside ```text fences (JSON escapes such as `\"` rendered as plain `"`). Input schemas are the exact `parameters` objects, pretty-printed with key order preserved.

Notes on fidelity:

- `place_option_order`: the tool description delivered by the harness ends in `… [truncated]` (truncated mid-sentence after "one option leg paired with 100 shares o"). This truncation was present on the batch load AND on an individual retry, so the remainder of that description (after the "Not currently supported anywhere" section begins) is not available here. The text below is everything that was delivered, including the truncation marker.
- No MCP tool annotations (readOnlyHint, destructiveHint, idempotentHint, openWorldHint, title) were exposed in the loaded schemas for any of these tools. No `enum` or `default` keywords appear in any schema; allowed values and defaults are stated only in the prose `description` fields.
- Every schema has `"additionalProperties": false` at the top level. `legs` and `tax_lots` are typed `["null", "array"]`.

Tools in this file (in order): `review_option_order`, `place_option_order`, `exercise_option`, `get_option_orders`, `cancel_option_order`, `cancel_option_exercise`, `preview_crypto_order`, `place_crypto_order`, `get_crypto_orders`, `cancel_crypto_order`

## review_option_order

Full MCP name: `mcp__00000000-0000-4000-8000-000000000000__review_option_order`

### Description (verbatim)

```text
Simulate an options order without placing it. Returns the current quote plus pre-trade alerts. Call this by default before place_option_order or replace_option_order unless the user has very explicitly asked to skip review. Capability: single-leg options orders — Level 2 strategies (covered calls, cash-secured puts, long calls and puts). Multi-leg spreads (Level 3 strategies) are supported on option_level_3 accounts. Account requirements: confirm via get_accounts that the chosen account is agentic_allowed=true AND has option_level_2 or option_level_3. If agentic_allowed=false do NOT call. If option_level is empty or option_level_0, do NOT call; follow the get_accounts guide for how to direct the user to enroll. Parameter rules: - legs: option_id (from get_option_instruments), side, position_effect, optional ratio_quantity per leg. - Multi-leg leg layouts — vertical spread: two legs, same expiration, different strikes, opposite sides. Calendar: two legs, same strike, different expirations. Iron condor: four legs, a put spread plus a call spread. Roll: close the leg you hold (position_effect 'close', side opposite the position) plus open the replacement ('open'). - Get the net price from the user rather than inferring it from individual leg quotes. - Multi-leg is not available on cash or retirement accounts through this tool. - type: 'limit' (default), 'market', 'stop_limit', 'stop_market'. If unspecified, ask. price for limit/stop_limit; stop_price for stop_market/stop_limit. - market, stop_market, and stop_limit are single-leg only. market and stop_market are also GFD, regular_hours only. stop_market is sell-to-close only with stop_price below the current ask. Non-limit-immediate types are blocked in any extended-hours session. - Surface order_checks alerts verbatim — the detail strings carry the actual time/contract/BP values for the user.
```

### Input schema (verbatim)

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account number. Must come from the user or be clearly implied — never default from get_accounts. Must be agentic_allowed=true.",
      "type": "string"
    },
    "chain_symbol": {
      "description": "Underlying ticker (e.g. 'AAPL', 'SPXW'). Supply alongside underlying_type to include fees and collateral in the response — always do so when known.",
      "type": "string"
    },
    "direction": {
      "description": "Net direction of the whole order: 'debit' (you pay the net premium) or 'credit' (you receive it). Required with 2 or more legs; for one leg it is derived from that leg's side, so omit it.",
      "type": "string"
    },
    "legs": {
      "description": "1 to 4 legs, all on the same underlying and each a different contract. Several legs are filled together as one strategy.",
      "items": {
        "additionalProperties": false,
        "properties": {
          "option_id": {
            "description": "Option instrument UUID (from get_option_instruments).",
            "type": "string"
          },
          "position_effect": {
            "description": "'open' (new position) or 'close' (existing position). To close a long use sell; to close a short use buy.",
            "type": "string"
          },
          "ratio_quantity": {
            "description": "Contracts for this leg per unit of the order's quantity. Defaults to 1, which is what standard spreads, condors, calendars, and rolls use. Must be 1 on a single-leg order; across legs the ratios must be in lowest terms (1:2, not 2:4).",
            "type": "integer"
          },
          "side": {
            "description": "'buy' or 'sell'.",
            "type": "string"
          }
        },
        "required": [
          "option_id",
          "side",
          "position_effect"
        ],
        "type": "object"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "market_hours": {
      "description": "'regular_hours' (default), 'regular_curb_hours', or 'regular_curb_overnight_hours'. Extended-hours sessions only accept limit+immediate. CURB requires an index chain with extended_hours_state='enabled' (per get_option_chains); rejection surfaces at place time.",
      "type": "string"
    },
    "price": {
      "description": "Limit price (e.g. '1.50'). Per contract for one leg; with several legs it is the net premium of the whole strategy per unit of quantity, always positive — direction says whether it is paid or received. Required for limit/stop_limit; must be omitted for market/stop_market.",
      "type": "string"
    },
    "quantity": {
      "description": "Positive integer contract count. With several legs it counts whole strategies — each leg fills quantity × its ratio_quantity contracts.",
      "type": "string"
    },
    "stop_price": {
      "description": "Stop trigger price per contract. Required for stop_limit/stop_market; must be omitted for limit/market. For sell-side stop_market, must be below the current ask.",
      "type": "string"
    },
    "time_in_force": {
      "description": "'gfd' (default) or 'gtc'. Market orders must be 'gfd'.",
      "type": "string"
    },
    "type": {
      "description": "'limit' (default), 'market', 'stop_limit', or 'stop_market'. Only 'limit' is available with 2 or more legs.",
      "type": "string"
    },
    "underlying_type": {
      "description": "'equity' or 'index'. Required alongside chain_symbol to enable the fee + collateral fetch.",
      "type": "string"
    }
  },
  "required": [
    "account_number",
    "legs",
    "quantity"
  ],
  "type": "object"
}
```

## place_option_order

Full MCP name: `mcp__00000000-0000-4000-8000-000000000000__place_option_order`

### Description (verbatim)

```text
Place a real options order with real money. Capability: single-leg options orders — Level 2 strategies (covered calls, cash-secured puts, long calls and puts). Multi-leg spreads (Level 3 strategies) are supported on option_level_3 accounts. Account requirements: confirm via get_accounts that the chosen account is agentic_allowed=true AND has option_level_2 or option_level_3. If agentic_allowed=false do NOT call. If option_level is empty or option_level_0, do NOT call; follow the get_accounts guide for how to direct the user to enroll. Alert handling: pre-trade alerts surface only in review_option_order. After the user has acknowledged the reviewed alert, call this tool with the same parameters. Idempotency: pass a fresh UUID as ref_id on the first call for each logical order, and re-send the SAME ref_id on retries of transient transport failures. Use a new ref_id only when the user wants a new order. To find option_id: get_option_chains → get_option_instruments filtered by expiration_date/strike_price/type. Parameter rules: - Multi-leg leg layouts — vertical spread: two legs, same expiration, different strikes, opposite sides. Calendar: two legs, same strike, different expirations. Iron condor: four legs, a put spread plus a call spread. Roll: close the leg you hold (position_effect 'close', side opposite the position) plus open the replacement ('open'). - Get the net price from the user rather than inferring it from individual leg quotes. - Multi-leg is not available on cash or retirement accounts through this tool. - type: 'limit' (default), 'market', 'stop_limit', 'stop_market'. price for limit/stop_limit; stop_price for stop_market/stop_limit. - market, stop_market, and stop_limit are single-leg only. market and stop_market are also regular_hours, GFD only. stop_market is sell-to-close only with stop_price below the current ask. Not currently supported anywhere (including the Robinhood apps): - combo orders. At Robinhood, "combo" specifically means a stock-option combo: one option leg paired with 100 shares o… [truncated]
```

### Input schema (verbatim)

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account number. Must come from the user or be clearly implied — never default from get_accounts. Must be agentic_allowed=true.",
      "type": "string"
    },
    "direction": {
      "description": "Net direction of the whole order: 'debit' (you pay the net premium) or 'credit' (you receive it). Required with 2 or more legs; for one leg it is derived from that leg's side, so omit it.",
      "type": "string"
    },
    "legs": {
      "description": "1 to 4 legs, all on the same underlying and each a different contract. Several legs are filled together as one strategy. Should match the legs the user reviewed.",
      "items": {
        "additionalProperties": false,
        "properties": {
          "option_id": {
            "description": "Option instrument UUID (from get_option_instruments).",
            "type": "string"
          },
          "position_effect": {
            "description": "'open' (new position) or 'close' (existing position). To close a long use sell; to close a short use buy.",
            "type": "string"
          },
          "ratio_quantity": {
            "description": "Contracts for this leg per unit of the order's quantity. Defaults to 1, which is what standard spreads, condors, calendars, and rolls use. Must be 1 on a single-leg order; across legs the ratios must be in lowest terms (1:2, not 2:4).",
            "type": "integer"
          },
          "side": {
            "description": "'buy' or 'sell'.",
            "type": "string"
          }
        },
        "required": [
          "option_id",
          "side",
          "position_effect"
        ],
        "type": "object"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "market_hours": {
      "description": "'regular_hours' (default), 'regular_curb_hours', or 'regular_curb_overnight_hours'. Non-limit-immediate orders only place in regular_hours. CURB requires an index chain with extended_hours_state='enabled'.",
      "type": "string"
    },
    "price": {
      "description": "Limit price. Per contract for one leg; with several legs it is the net premium of the whole strategy per unit of quantity, always positive — direction says whether it is paid or received. Required for limit/stop_limit; must be omitted for market/stop_market.",
      "type": "string"
    },
    "quantity": {
      "description": "Positive integer contract count. With several legs it counts whole strategies — each leg fills quantity × its ratio_quantity contracts.",
      "type": "string"
    },
    "ref_id": {
      "description": "Idempotency key (UUID). Generate once per logical order and re-send on retry. Omitting falls back to a server-generated key.",
      "type": "string"
    },
    "stop_price": {
      "description": "Stop trigger price per contract. Required for stop_limit/stop_market; must be omitted for limit/market.",
      "type": "string"
    },
    "time_in_force": {
      "description": "'gfd' (default) or 'gtc'. Market orders must be 'gfd'.",
      "type": "string"
    },
    "type": {
      "description": "'limit' (default), 'market', 'stop_limit', or 'stop_market'. Should match the type the user reviewed. Only 'limit' is available with 2 or more legs.",
      "type": "string"
    }
  },
  "required": [
    "account_number",
    "legs",
    "quantity"
  ],
  "type": "object"
}
```

## exercise_option

Full MCP name: `mcp__00000000-0000-4000-8000-000000000000__exercise_option`

### Description (verbatim)

```text
Exercise a long options position — a call exercises the right to buy the underlying shares at the strike price; a put exercises the right to sell. Exercise is irrevocable once state moves past queued. Never call this tool without asking the user to explicitly confirm the specific exercise first: state the option, quantity, and expected cash impact (debit for calls, credit for puts), then wait for their affirmative reply before calling. The user's original request to exercise is NOT itself sufficient confirmation — a generic "exercise my calls" does not confirm a specific option and quantity. Position requirements: confirm via get_option_positions that the position type=long and quantity > 0. Account requirements: confirm via get_accounts that the chosen account is agentic_allowed=true AND has option_level_2 or option_level_3. If agentic_allowed=false do NOT call. If option_level is empty or option_level_0, do NOT call; follow the get_accounts guide for how to direct the user to enroll. Index options cannot be manually exercised and will be rejected. Exercises submitted during market hours execute the same day; requests submitted after market close — including on late-close trading days — are queued for overnight processing. Parameter rules: - quantity must be a positive integer and cannot exceed the position's available contracts. - allow_shorts=true only applies to PUT exercises where the account does not own enough shares to deliver. Require explicit user confirmation before setting this — it creates a short equity position in the underlying stock. - reason is optional but recommended; collect it from the user when they volunteer a motivation. - ref_id: use the same UUID on retries of the same logical exercise; use a new UUID for a new exercise.
```

### Input schema (verbatim)

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account number. Must be agentic_allowed=true with option_level_2 or option_level_3.",
      "type": "string"
    },
    "allow_shorts": {
      "description": "When true, allows a PUT exercise to proceed even when the account does not own enough shares to deliver — creating a short equity position. Requires explicit user confirmation that they intend to short the underlying stock. Default false.",
      "type": "boolean"
    },
    "option_id": {
      "description": "Option instrument UUID from get_option_positions or get_option_instruments. The position must be type=long.",
      "type": "string"
    },
    "quantity": {
      "description": "Number of contracts to exercise (positive integer, minimum 1).",
      "type": "integer"
    },
    "reason": {
      "description": "Optional exercise reason: covering_early_assignment | buying_stocks | not_enough_liquidity_or_spread_too_wide | hedging_position.",
      "type": "string"
    },
    "ref_id": {
      "description": "Idempotency key (UUID). Generate once per logical exercise and re-send on retry. Omitting falls back to a server-generated key.",
      "type": "string"
    }
  },
  "required": [
    "account_number",
    "option_id",
    "quantity"
  ],
  "type": "object"
}
```

## get_option_orders

Full MCP name: `mcp__00000000-0000-4000-8000-000000000000__get_option_orders`

### Description (verbatim)

```text
Fetch options orders for an account — list mode (newest first; open and closed, including fills, cancellations, and rejections) or single-order mode by passing order_id. When the user asks broadly for "orders" or "my orders" without naming an asset class or order type, call get_equity_orders, get_option_orders, get_crypto_orders, and get_advanced_orders in parallel so OCO groups are not omitted. Filtering tips: - Prefer narrow queries: combine state and/or created_at_gte for specific questions — the per-page cap is fixed. - chain_ids filters by underlying chain UUID (from get_option_chains). - created_at_gte: interpret relative times in the user's timezone, convert to UTC before sending.
```

### Input schema (verbatim)

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account number. Must come from the user or be clearly implied — never default from get_accounts.",
      "type": "string"
    },
    "chain_ids": {
      "description": "Comma-separated chain UUIDs (from get_option_chains) to filter by underlying.",
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
      "description": "Filter by single state: queued, confirmed, partially_filled, filled, rejected, cancelled, failed, voided, pending_cancelled.",
      "type": "string"
    },
    "underlying_type": {
      "description": "'equity' or 'index'.",
      "type": "string"
    }
  },
  "required": [
    "account_number"
  ],
  "type": "object"
}
```

## cancel_option_order

Full MCP name: `mcp__00000000-0000-4000-8000-000000000000__cancel_option_order`

### Description (verbatim)

```text
Cancel an open option order by account_number + order_id. Always confirm with the user before calling. Resolve order_id via get_option_orders if the user refers to it by description; pass the same account_number you used there. Requires an agentic_allowed=true account; non-agentic accounts are rejected. Cancellation may be rejected if the order has already filled, was already cancelled, or is otherwise ineligible.
```

### Input schema (verbatim)

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account that owns the order. Must come from the user or be clearly implied — never default from get_accounts. Must be agentic_allowed=true. Mismatches against the order's owning account are rejected.",
      "type": "string"
    },
    "order_id": {
      "description": "Order UUID from get_option_orders. Must live in account_number.",
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

## cancel_option_exercise

Full MCP name: `mcp__00000000-0000-4000-8000-000000000000__cancel_option_exercise`

### Description (verbatim)

```text
Cancel all queued exercise requests for an option position. Pass the same account_number and option_id used for exercise_option. Internally looks up all queued exercise events for that option and cancels each one. Typically there is one; multiple means the user submitted separate exercise batches. Only cancels events in state=queued — events already processing are rejected by the broker. Always confirm with the user before calling.
```

### Input schema (verbatim)

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account that owns the exercise. Must be agentic_allowed=true.",
      "type": "string"
    },
    "option_id": {
      "description": "Option instrument UUID — the same option_id used for exercise_option. The tool looks up the queued exercise for this option and cancels it.",
      "type": "string"
    }
  },
  "required": [
    "account_number",
    "option_id"
  ],
  "type": "object"
}
```

## preview_crypto_order

Full MCP name: `mcp__00000000-0000-4000-8000-000000000000__preview_crypto_order`

### Description (verbatim)

```text
Simulate a crypto order without placing it — returns the transient order shape with the estimated cost/credit and fees resolved, plus any pre-trade validation errors. Call this by default before place_crypto_order unless the user has very explicitly asked to skip the preview. Requires an agentic-enabled crypto account. Parameter rules: - Provide exactly one of quantity or dollar_amount; dollar_amount works with every type. - limit_price is required for limit and stop_limit; stop_price is required for stop_loss and stop_limit. - symbol is resolved to a currency pair before the preview; pass a bare asset symbol (e.g. 'BTC') or a pair ('BTC-USD'). - tax_lots (specified-lot selling, sell only): first call get_crypto_tax_lots for the symbol (optionally with strategy + quantity), then pass tax_lots as {open_lot_id, quantity} pairs whose quantities sum to the order quantity. Omit for the default disposal. Quantity-based sells only; check selection_state first. Lot quantities take at most 8 decimal places, and the order quantity must be a multiple of the pair's min_order_quantity_increment (see get_currency_pairs); an off-increment quantity is rejected before the preview because the lot quantities would no longer match it. Lot availability is verified when the order is placed, so a preview can pass and the place can still be rejected — e.g. a lot was used by another order, its available quantity changed, or specified-lot selling is no longer enabled for the asset.
```

### Input schema (verbatim)

```json
{
  "additionalProperties": false,
  "properties": {
    "dollar_amount": {
      "description": "USD notional (e.g. '100.00'). Valid with every order type. Provide exactly one of quantity or dollar_amount. For market, quantity is derived server-side: a buy is sized at the current market price — the same sizing as the app — so the typical debit is ~dollar_amount, with a worst case up to ~1% more if the price moves before execution (the buy collar); a sell is likewise sized at the current market price, so the typical credit is ~dollar_amount, with a worst case up to ~5% less if the price moves before execution (the sell collar). For stop_loss, quantity is derived from dollar_amount at stop_price (the trigger price) when the order is placed, not the live quote — the triggered market order executes near the trigger, so the typical fill notional is ~dollar_amount, with a worst case up to the collar worse (a higher debit for buy stops, a lower credit for sell stops). For limit and stop_limit, quantity is derived from dollar_amount at limit_price when the order is placed — fills execute at the limit price or better, so a buy's debit is capped at dollar_amount (no collar buffer; on fee-priced accounts fee rounding can add at most one cent) and a sell's credit is at least the derived quantity's worth at the limit, minus any fee. On fee-priced accounts a buy's quantity is sized from the fee-netted amount so the total debit targets dollar_amount, and a sell's fee is deducted from the credit.",
      "type": "string"
    },
    "limit_price": {
      "description": "Limit price; required for limit and stop_limit.",
      "type": "string"
    },
    "quantity": {
      "description": "Asset quantity to trade (e.g. amount of BTC). Provide exactly one of quantity or dollar_amount. Crypto is measured in coins/units, not shares — when speaking to the user, including order confirmations, NEVER call a crypto quantity 'shares' ('shares' is equity terminology); state the amount of the asset, e.g. '0.001542 ETH'.",
      "type": "string"
    },
    "rhs_account_number": {
      "description": "Numeric brokerage account number (the 'rhs_account_number' field on get_accounts entries — distinct from the alphanumeric 'account_number'). Must be an agentic-enabled crypto account. Required.",
      "type": "string"
    },
    "side": {
      "description": "'buy' or 'sell'. Required.",
      "type": "string"
    },
    "stop_price": {
      "description": "Stop trigger price; required for stop_loss and stop_limit.",
      "type": "string"
    },
    "symbol": {
      "description": "Crypto symbol (e.g. 'BTC', 'ETH', or 'BTC-USD'). Resolved to a currency pair before the preview. Required.",
      "type": "string"
    },
    "tax_lots": {
      "description": "Optional specified-lot selection for a SELL order. To sell specific tax lots instead of the default disposal, pass {open_lot_id, quantity} objects where open_lot_id comes from get_crypto_tax_lots and the quantities sum to the order quantity. Omit for the default. Sell only; quantity-based orders only (not dollar_amount); at most 50 lots; only when get_crypto_tax_lots reports selection_state 'enabled'.",
      "items": {
        "additionalProperties": false,
        "properties": {
          "open_lot_id": {
            "description": "open_lot_id of the open tax lot to sell, from get_crypto_tax_lots.",
            "type": "string"
          },
          "quantity": {
            "description": "Units to sell from this lot as a decimal string; at most the lot's quantity_available (or exactly quantity_to_sell from a strategy result).",
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
      "description": "Allowed values depend on type. market and limit: 'gtc' (good till canceled) only — market orders execute immediately so a bounded duration does not apply, and limit orders are always for 90 days; omit this field or pass 'gtc' for both. stop_loss and stop_limit: 'gtc' (good for 90 days), 'gfd' (good for day), 'gfw' (good for 7 days), or 'gfm' (good for 30 days). If omitted, defaults to gtc for market and limit, and gfd for stop_loss and stop_limit. 'ioc' is NEVER supported for crypto orders.",
      "type": "string"
    },
    "type": {
      "description": "'market', 'limit', 'stop_loss', or 'stop_limit'. Required. 'stop_loss' is a stop-triggered market order — despite the name it applies to buy stops too; the equity/option tools call the same concept 'stop_market'. ALWAYS call these by their app names when speaking to the user, including when listing what types are supported: 'stop_loss' is a 'stop order' and 'stop_limit' a 'stop limit order'; the raw values are tool inputs only. A user asking for a 'stop loss', 'stop order', or 'stop market' order means this stop_loss type — do not ask which stop type (but a limit price given alongside the trigger makes it stop_limit); ask only when the phrasing names no type, e.g. a bare 'stop' or 'protect my position'.",
      "type": "string"
    }
  },
  "required": [
    "rhs_account_number",
    "symbol",
    "side",
    "type"
  ],
  "type": "object"
}
```

## place_crypto_order

Full MCP name: `mcp__00000000-0000-4000-8000-000000000000__place_crypto_order`

### Description (verbatim)

```text
Place a real crypto order with real money. Parameters mirror preview_crypto_order plus the optional ref_id. Requires an agentic-enabled crypto account. Idempotency: pass a fresh UUID as ref_id on the first call for each logical order, and re-send the SAME ref_id on retries of transient failures. Use a new ref_id only when the user wants a new order. Parameter rules: - Provide exactly one of quantity or dollar_amount; dollar_amount works with every type. - limit_price is required for limit and stop_limit; stop_price is required for stop_loss and stop_limit. - symbol is resolved to a currency pair before placement; pass a bare asset symbol (e.g. 'BTC') or a pair ('BTC-USD'). - tax_lots (specified-lot selling, sell only): first call get_crypto_tax_lots for the symbol (optionally with strategy + quantity), then pass tax_lots as {open_lot_id, quantity} pairs whose quantities sum to the order quantity. Omit for the default disposal. Quantity-based sells only; check selection_state first. Lot quantities take at most 8 decimal places, and the order quantity must be a multiple of the pair's min_order_quantity_increment (see get_currency_pairs); an off-increment quantity is rejected before placement because the lot quantities would no longer match it. Lot availability is verified when the order is placed, so a preview can pass and the place can still be rejected — e.g. a lot was used by another order, its available quantity changed, or specified-lot selling is no longer enabled for the asset. - Accounts that require trade approval may not be able to carry a specified-lot sell; if the error mentions TAX_LOT_APPROVALS_DISABLED, tell the user and offer to retry without tax_lots (default disposal).
```

### Input schema (verbatim)

```json
{
  "additionalProperties": false,
  "properties": {
    "dollar_amount": {
      "description": "USD notional (e.g. '100.00'). Valid with every order type. Provide exactly one of quantity or dollar_amount. For market, quantity is derived server-side: a buy is sized at the current market price — the same sizing as the app — so the typical debit is ~dollar_amount, with a worst case up to ~1% more if the price moves before execution (the buy collar); a sell is likewise sized at the current market price, so the typical credit is ~dollar_amount, with a worst case up to ~5% less if the price moves before execution (the sell collar). For stop_loss, quantity is derived from dollar_amount at stop_price (the trigger price) when the order is placed, not the live quote — the triggered market order executes near the trigger, so the typical fill notional is ~dollar_amount, with a worst case up to the collar worse (a higher debit for buy stops, a lower credit for sell stops). For limit and stop_limit, quantity is derived from dollar_amount at limit_price when the order is placed — fills execute at the limit price or better, so a buy's debit is capped at dollar_amount (no collar buffer; on fee-priced accounts fee rounding can add at most one cent) and a sell's credit is at least the derived quantity's worth at the limit, minus any fee. On fee-priced accounts a buy's quantity is sized from the fee-netted amount so the total debit targets dollar_amount, and a sell's fee is deducted from the credit.",
      "type": "string"
    },
    "limit_price": {
      "description": "Limit price; required for limit and stop_limit.",
      "type": "string"
    },
    "quantity": {
      "description": "Asset quantity to trade (e.g. amount of BTC). Provide exactly one of quantity or dollar_amount. Crypto is measured in coins/units, not shares — when speaking to the user, including order confirmations, NEVER call a crypto quantity 'shares' ('shares' is equity terminology); state the amount of the asset, e.g. '0.001542 ETH'.",
      "type": "string"
    },
    "ref_id": {
      "description": "Idempotency key (UUID). Generate once per logical order and re-send the SAME value on retries of transient failures — the upstream deduplicates by ref_id. Omitting falls back to a server-generated key (loses client↔gateway idempotency). Use a new value only when the user wants a new order.",
      "type": "string"
    },
    "rhs_account_number": {
      "description": "Numeric brokerage account number (the 'rhs_account_number' field on get_accounts entries — distinct from the alphanumeric 'account_number'). Must be an agentic-enabled crypto account. Required.",
      "type": "string"
    },
    "side": {
      "description": "'buy' or 'sell'. Required.",
      "type": "string"
    },
    "stop_price": {
      "description": "Stop trigger price; required for stop_loss and stop_limit.",
      "type": "string"
    },
    "symbol": {
      "description": "Crypto symbol (e.g. 'BTC', 'ETH', or 'BTC-USD'). Resolved to a currency pair before the order is placed. Required.",
      "type": "string"
    },
    "tax_lots": {
      "description": "Optional specified-lot selection for a SELL order. To sell specific tax lots instead of the default disposal, pass {open_lot_id, quantity} objects where open_lot_id comes from get_crypto_tax_lots and the quantities sum to the order quantity. Omit for the default. Sell only; quantity-based orders only (not dollar_amount); at most 50 lots; only when get_crypto_tax_lots reports selection_state 'enabled'.",
      "items": {
        "additionalProperties": false,
        "properties": {
          "open_lot_id": {
            "description": "open_lot_id of the open tax lot to sell, from get_crypto_tax_lots.",
            "type": "string"
          },
          "quantity": {
            "description": "Units to sell from this lot as a decimal string; at most the lot's quantity_available (or exactly quantity_to_sell from a strategy result).",
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
      "description": "Allowed values depend on type. market and limit: 'gtc' (good till canceled) only — market orders execute immediately so a bounded duration does not apply, and limit orders are always for 90 days; omit this field or pass 'gtc' for both. stop_loss and stop_limit: 'gtc' (good for 90 days), 'gfd' (good for day), 'gfw' (good for 7 days), or 'gfm' (good for 30 days). If omitted, defaults to gtc for market and limit, and gfd for stop_loss and stop_limit. 'ioc' is NEVER supported for crypto orders.",
      "type": "string"
    },
    "type": {
      "description": "'market', 'limit', 'stop_loss', or 'stop_limit'. Required. 'stop_loss' is a stop-triggered market order — despite the name it applies to buy stops too; the equity/option tools call the same concept 'stop_market'. ALWAYS call these by their app names when speaking to the user, including when listing what types are supported: 'stop_loss' is a 'stop order' and 'stop_limit' a 'stop limit order'; the raw values are tool inputs only. A user asking for a 'stop loss', 'stop order', or 'stop market' order means this stop_loss type — do not ask which stop type (but a limit price given alongside the trigger makes it stop_limit); ask only when the phrasing names no type, e.g. a bare 'stop' or 'protect my position'.",
      "type": "string"
    }
  },
  "required": [
    "rhs_account_number",
    "symbol",
    "side",
    "type"
  ],
  "type": "object"
}
```

## get_crypto_orders

Full MCP name: `mcp__00000000-0000-4000-8000-000000000000__get_crypto_orders`

### Description (verbatim)

```text
List crypto order history for an account — newest first. Open and closed orders, including fills, cancellations, and rejections. Pass order_id to retrieve a single order by UUID. Filtering tips: - Prefer narrow queries: combine state (or state_group), symbol, and/or created_at_gte for specific questions. - created_at_gte: interpret relative times in the user's timezone, convert to UTC before sending. - updated_at_gte: useful when polling for order fills or state transitions. - symbol triggers a symbol→currency_pair_id lookup; omit it if you don't need it. When the user asks broadly for "orders" or "my orders" without naming an asset class or order type, call get_equity_orders, get_option_orders, get_crypto_orders, and get_advanced_orders in parallel so OCO groups are not omitted. For requests spanning all accounts, re-fetch the account list with get_accounts first — a list from earlier in the conversation may be missing newly created accounts.
```

### Input schema (verbatim)

```json
{
  "additionalProperties": false,
  "properties": {
    "created_at_gte": {
      "description": "Lower bound (inclusive) on created_at. ISO 8601 UTC; naive values are interpreted as UTC.",
      "type": "string"
    },
    "cursor": {
      "description": "Pagination cursor. Omit for the first page; for the next page, pass the cursor query param from the prior response's next URL.",
      "type": "string"
    },
    "order_id": {
      "description": "Filter to a single order by UUID. The response shape is unchanged (results[] with at most one entry); empty when the order does not belong to the account.",
      "type": "string"
    },
    "rhs_account_number": {
      "description": "Numeric brokerage account number (the 'rhs_account_number' field on get_accounts entries — distinct from the alphanumeric 'account_number'). Required.",
      "type": "string"
    },
    "side": {
      "description": "Filter by side: 'buy' or 'sell'.",
      "type": "string"
    },
    "state": {
      "description": "Filter by single state: queued, confirmed, partially_filled, filled, canceled, rejected, failed, voided. For open-vs-closed shortcuts, use state_group instead.",
      "type": "string"
    },
    "state_group": {
      "description": "Filter by state group: 'open' or 'closed'. Quick shortcut — 'open' covers queued/confirmed/partially_filled, 'closed' covers filled/canceled/rejected/failed/voided. Mutually exclusive with state.",
      "type": "string"
    },
    "symbol": {
      "description": "Filter by crypto symbol (e.g. 'BTC', 'ETH', or 'BTC-USD'). Triggers a symbol→currency_pair_id lookup before the orders call.",
      "type": "string"
    },
    "updated_at_gte": {
      "description": "Lower bound (inclusive) on updated_at. ISO 8601 UTC. Useful for polling for fills — each state transition bumps updated_at.",
      "type": "string"
    }
  },
  "required": [
    "rhs_account_number"
  ],
  "type": "object"
}
```

## cancel_crypto_order

Full MCP name: `mcp__00000000-0000-4000-8000-000000000000__cancel_crypto_order`

### Description (verbatim)

```text
Cancel an open crypto order by order_id. Always confirm with the user before calling. Resolve order_id via get_crypto_orders if the user refers to it by description; pass the same rhs_account_number that owns the order. Cancellation may be rejected if the order has already filled, was already canceled, or is otherwise ineligible.
```

### Input schema (verbatim)

```json
{
  "additionalProperties": false,
  "properties": {
    "order_id": {
      "description": "Order UUID from get_crypto_orders. Must belong to rhs_account_number. Required.",
      "type": "string"
    },
    "rhs_account_number": {
      "description": "Numeric brokerage account number (the 'rhs_account_number' field on get_accounts entries — distinct from the alphanumeric 'account_number'). The order must belong to this account. Required.",
      "type": "string"
    }
  },
  "required": [
    "rhs_account_number",
    "order_id"
  ],
  "type": "object"
}
```
