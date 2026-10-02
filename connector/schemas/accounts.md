# Robinhood Trading MCP: Accounts, portfolio, positions, P&L, tax lots, enrollment

Verbatim tool schemas as loaded via ToolSearch (server `mcp__00000000-0000-4000-8000-000000000000`). Descriptions and input schemas are copied exactly from the loaded definitions. No tool annotations (readOnlyHint / destructiveHint / etc.) were present in the loaded definitions for any tool in this family. No tool was invoked.

## get_accounts

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_accounts`

### Description

List the user's brokerage accounts. Each account includes an agentic_allowed field indicating whether it's tradable by you — accounts where it's false are read-only to you. Use this to look up account_number values needed by other tools. Exactly one account is tradable by you; when the user is choosing an account for a trade, use that account directly without asking. Does NOT return reliable buying power — route buying-power questions through get_portfolio.

### Input schema

```json
{
  "additionalProperties": false,
  "type": "object"
}
```

## get_portfolio

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_portfolio`

### Description

Get the account's portfolio market value breakdown by asset type and buying power. Use for "how much is my account worth?", "what's my portfolio breakdown?", "how much do I have in options?", and "how much can I spend / afford?" questions.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account number. Obtain from get_accounts.",
      "type": "string"
    }
  },
  "required": [
    "account_number"
  ],
  "type": "object"
}
```

## get_equity_positions

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_equity_positions`

### Description

List open equity positions for a specific brokerage account. Returns symbol, quantity, average cost, and per-position hold breakdowns.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account number. Must come from the user or be clearly implied — never default from get_accounts.",
      "type": "string"
    },
    "cursor": {
      "description": "Pagination cursor. Omit for the first page; for the next page, pass the cursor query param from the prior response's next URL.",
      "type": "string"
    }
  },
  "required": [
    "account_number"
  ],
  "type": "object"
}
```

## get_option_positions

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_option_positions`

### Description

List options positions for an account. Returns open and closed (zero-quantity) positions. Pass nonzero=true for "what options do I have" / "show me my positions" — the common case.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account number. Must come from the user or be clearly implied — never default from get_accounts.",
      "type": "string"
    },
    "chain_ids": {
      "description": "Comma-separated chain UUIDs (from get_option_chains).",
      "type": "string"
    },
    "cursor": {
      "description": "Pagination cursor. Omit for the first page; for the next page, pass the cursor query param from the prior response's next URL.",
      "type": "string"
    },
    "expiration_date": {
      "description": "Exact expiration (YYYY-MM-DD).",
      "type": "string"
    },
    "expiration_date_gte": {
      "description": "Lower bound on expiration (YYYY-MM-DD).",
      "type": "string"
    },
    "expiration_date_lte": {
      "description": "Upper bound on expiration (YYYY-MM-DD).",
      "type": "string"
    },
    "nonzero": {
      "description": "True to return only currently-open positions; omit/false to include closed ones.",
      "type": "boolean"
    },
    "option_ids": {
      "description": "Comma-separated instrument UUIDs.",
      "type": "string"
    },
    "option_type": {
      "description": "'call' or 'put'.",
      "type": "string"
    },
    "type": {
      "description": "'long' or 'short'.",
      "type": "string"
    }
  },
  "required": [
    "account_number"
  ],
  "type": "object"
}
```

## get_crypto_positions

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_crypto_positions`

### Description

List open crypto positions for a specific brokerage account. Returns asset, quantity, transferable amount, and cost-basis breakdown.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "cursor": {
      "description": "Pagination cursor. Omit for the first page; for the next page, pass the cursor query param from the prior response's next URL.",
      "type": "string"
    },
    "rhs_account_number": {
      "description": "Numeric brokerage account number (the 'rhs_account_number' field on get_accounts entries — distinct from the alphanumeric 'account_number'). Required.",
      "type": "string"
    }
  },
  "required": [
    "rhs_account_number"
  ],
  "type": "object"
}
```

## get_realized_pnl

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_realized_pnl`

### Description

Get a customer's realized profit & loss for an account over a time window — per-bucket realized gain ($ and %) and the number of closing trades, plus window totals. Read-only. Aggregate, bucketed numbers only (not individual trades). Use for post-trade analysis like "how did my last 90 days of trades do?".

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account number (the rhs_account_number from get_accounts). Obtain it from get_accounts.",
      "type": "string"
    },
    "asset_classes": {
      "description": "Filter to one or more of equity, option, crypto. Omit for all asset classes available on the account.",
      "items": {
        "type": "string"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "display_currency": {
      "description": "Currency for returned amounts. Currently USD only; defaults to USD.",
      "type": "string"
    },
    "end_date": {
      "description": "Custom window end, YYYY-MM-DD, inclusive — the entire end_date is covered (through 23:59:59 in timezone). Use with start_date instead of span; an end_date beyond today returns data through the present.",
      "type": "string"
    },
    "span": {
      "description": "Preset window: day, week, month, 3month, year, or all. Defaults to 3month ('last 90 days'). Mutually exclusive with start_date/end_date.",
      "type": "string"
    },
    "start_date": {
      "description": "Custom window start, YYYY-MM-DD, inclusive — interpreted at midnight in timezone (default US Eastern). Use with end_date instead of span; must be on or before end_date and not in the future.",
      "type": "string"
    },
    "timezone": {
      "description": "IANA timezone for bucket day-boundaries (e.g. America/New_York). Defaults to the account timezone (US Eastern).",
      "type": "string"
    }
  },
  "required": [
    "account_number"
  ],
  "type": "object"
}
```

## get_pnl_trade_history

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_pnl_trade_history`

### Description

Get a customer's per-trade realized profit & loss — a chronological, paginated list of closed/realizing trades (equities, options, crypto, prediction markets) with symbol, side, quantity, price, and realized gain/loss. This is the same data behind the app's PnL hub ("Realized profit & loss"). Read-only. Trades only. Use get_realized_pnl for aggregate/bucketed totals.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account number (the rhs_account_number from get_accounts). Obtain it from get_accounts.",
      "type": "string"
    },
    "cursor": {
      "description": "Pagination cursor from a previous response's next_cursor. Omit for the first page.",
      "type": "string"
    },
    "span": {
      "description": "Preset window: week (default), month, 3month, ytd, or all. Wormhole offers preset spans only (no arbitrary date range).",
      "type": "string"
    },
    "symbol": {
      "description": "Optional single stock symbol filter (trimmed + uppercased). Omit for all symbols; one symbol per call.",
      "type": "string"
    }
  },
  "required": [
    "account_number"
  ],
  "type": "object"
}
```

## get_equity_tax_lots

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_equity_tax_lots`

### Description

List the open tax lots for one equity holding in an account — each lot is a separate acquisition with its own quantity, cost basis, acquisition date, and long/short-term status. Requires a symbol (tax lots are tracked per instrument). Use it for cost-basis, holding-period, or which-lots-would-sell questions.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account number. Must come from the user or be clearly implied — never default from get_accounts.",
      "type": "string"
    },
    "cursor": {
      "description": "Pagination cursor. Omit for the first page; for the next page, pass the cursor query param from the prior response's next URL.",
      "type": "string"
    },
    "symbol": {
      "description": "Ticker symbol of the holding whose tax lots you want, e.g. AAPL. Tax lots are tracked per instrument — one symbol per call.",
      "type": "string"
    }
  },
  "required": [
    "account_number",
    "symbol"
  ],
  "type": "object"
}
```

## get_option_level_upgrade_info

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_option_level_upgrade_info`

### Description

Get the upgrade URL to apply for — or raise — options access on an account. The returned link routes the customer into the correct application for their target tier, including upgrading an existing option_level_2 account to option_level_3. Call when the user requests an options level their account doesn't yet have: option_level_2 enables long calls/puts, covered calls, and cash-secured puts; option_level_3 adds spreads and other multi-leg/complex strategies. option_level_3 additionally requires a margin or limited-margin account. If the account type is cash, do NOT call this tool for an L3 request — the customer must first switch to a margin/limited-margin account via get_limited_margin_upgrade_info, then re-fetch get_accounts; call this tool for L3 only once the account type is margin or limited margin. option_level_2 has no account-type requirement and is available on cash accounts. Map the user's requested strategy to its required level and call this tool if the account is below it — null/empty/option_level_0 for any options, or option_level_2 for an L3-only strategy such as a spread. Do NOT call when the account already has the required level or higher.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account number to generate the upgrade URL for. Obtain from get_accounts.",
      "type": "string"
    }
  },
  "required": [
    "account_number"
  ],
  "type": "object"
}
```

## get_limited_margin_upgrade_info

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_limited_margin_upgrade_info`

### Description

Check whether a cash account is eligible to upgrade to limited margin and return the links (web and mobile) that start the upgrade flow. Limited margin lets the account trade with unsettled funds — proceeds from a sale can go into a new order before that sale settles — while adding no borrowing or leverage. Call when the user asks about that capability, about trading with unsettled funds, or about enabling limited margin.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "account_number": {
      "description": "Brokerage account number to check. Obtain from get_accounts.",
      "type": "string"
    }
  },
  "required": [
    "account_number"
  ],
  "type": "object"
}
```

## get_crypto_account_onboarding_info

Full name: `mcp__00000000-0000-4000-8000-000000000000__get_crypto_account_onboarding_info`

### Description

Get the link a user opens to sign the crypto agreement and open a crypto account. Call when the user wants to trade crypto but has no crypto account.

### Input schema

```json
{
  "additionalProperties": false,
  "type": "object"
}
```
