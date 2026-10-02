# Robinhood Trading MCP — Research tool schemas (verbatim)

Family: Research (fundamentals, financials, news, earnings, analyst ratings, politician trades, SEC filings)

Source: schemas loaded via ToolSearch from MCP server `mcp__00000000-0000-4000-8000-000000000000__*` on 2026-09-21. No Robinhood tool was invoked.

Notes on fidelity:
- Descriptions and input schemas below are copied verbatim from the loaded tool definitions.
- The loaded definitions expose only `description`, `name`, and `parameters` (input schema). No annotations (readOnlyHint / destructiveHint / idempotentHint / openWorldHint / title) were present in the loaded definitions for any of these tools. Where a description says "Read-only.", that is description text, not an annotation.
- No `default` keys appear in any input schema; defaults are stated only inside description text.
- Array-typed parameters are declared as `"type": ["null", "array"]` even when listed in `required`.

---

## get_equity_fundamentals

Description (verbatim):

Get today's fundamentals for one or more stock symbols — valuation ratios (PE, P/B), capitalization (market cap, shares outstanding, float), today's session OHLCV, trailing volume averages, 52-week range, dividend schedule, and company profile. For real-time quotes use get_equity_quotes; for time-series price history use get_equity_historicals.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "bounds": {
      "description": "Trading session the day-level fields (open / high / low / volume / overnight_volume) are drawn from. One of 'regular' (regular trading hours), 'trading' (regular + post-market), 'extended' (pre-market + regular + post-market), '24_5' (24-hour, 5-day-trading-week). Does not affect valuation fields. overnight_volume only populates when bounds=24_5. Defaults to 'regular' when omitted.",
      "type": "string"
    },
    "symbols": {
      "description": "One or more stock symbols (max 10 per call). Exact-ticker match — no name or partial-ticker resolution. Lowercase and whitespace-padded input is normalized to uppercase-trimmed before forwarding.",
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

---

## get_financials

Description (verbatim):

Get a company's reported financial metrics over time — revenue, gross profit, net income, and net margin — by fiscal period (annual or quarterly), for one or more symbols. Use this for fundamental analysis like revenue-growth and margin-trend tracking, profitability screens, and period-over-period comparisons. Read-only.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "limit": {
      "description": "Number of most-recent periods to return per symbol (e.g. 8 for the last 8 quarters or years). Defaults to 4; values above 40 are capped to 40.",
      "type": "integer"
    },
    "period": {
      "description": "Reporting period: 'quarterly' or 'annual'. Defaults to 'quarterly' when omitted.",
      "type": "string"
    },
    "symbols": {
      "description": "One or more stock symbols (max 20 per call). Exact-ticker match — no name or partial-ticker resolution. Lowercase and whitespace-padded input is normalized to uppercase-trimmed before forwarding.",
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

---

## get_equity_news

Description (verbatim):

Get recent news articles for a stock, resolved by ticker symbol.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "cursor": {
      "description": "Pagination cursor from a prior response's next_cursor. Omit for the first page.",
      "type": "string"
    },
    "limit": {
      "description": "Max articles to return, 1-50; values above 50 are capped to 50. Omit to use the default.",
      "type": "integer"
    },
    "symbol": {
      "description": "Ticker symbol to get recent news for, e.g. AAPL.",
      "type": "string"
    }
  },
  "required": [
    "symbol"
  ],
  "type": "object"
}
```

---

## get_earnings_calendar

Description (verbatim):

List earnings reports scheduled across the market over a date window (up to 31 days), optionally limited to high-market-cap names. Returns one entry per report event — estimated/actual EPS, report date and timing (am/pm), and company-verification status. Use this for market-wide discovery ("what large-caps report this week?"). For a specific known ticker, use get_earnings_results instead. Read-only.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "days": {
      "description": "Window length in days, measured from start_date. Defaults to 7. Positive = forward window (e.g. 7 = the next 7 days, inclusive of start_date); negative = look-back window (e.g. -7 = the 7 days ending at start_date). Must be a non-zero value between -31 and 31 — windows wider than 31 days are rejected.",
      "type": "integer"
    },
    "filter": {
      "description": "Optional result filter. Set to 'high_market_cap' to limit the calendar to high-market-cap names (market cap over $1B) — useful for 'what large-caps report this week' style questions. Omit for all names.",
      "type": "string"
    },
    "start_date": {
      "description": "Window anchor, YYYY-MM-DD. Defaults to today (US/Eastern) when omitted.",
      "type": "string"
    }
  },
  "type": "object"
}
```

(Note: this schema has no `required` array — all parameters are optional.)

---

## get_earnings_results

Description (verbatim):

Get recent and upcoming earnings for ONE equity symbol — estimated/actual EPS, report date and timing (am/pm), and company-verification status. Returns the trailing up to 8 quarters. Use this for earnings-timing questions ("does AAPL report this week?"), EPS surprise analysis, and screening for upcoming earnings risk on a specific stock. For market-wide earnings calendar queries across many symbols, use get_earnings_calendar. Read-only.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "symbol": {
      "description": "Stock symbol to look up (one symbol per call). Exact-ticker match — no name or partial-ticker resolution. Lowercase and whitespace-padded input is normalized to uppercase-trimmed before forwarding. Returns the trailing up to 8 quarters of earnings for the symbol.",
      "type": "string"
    }
  },
  "required": [
    "symbol"
  ],
  "type": "object"
}
```

---

## get_equity_analyst_ratings

Description (verbatim):

Get analyst price targets (high, low, average) and the Buy/Hold/Sell ratings breakdown for one or more equity symbols. Use for consensus/sentiment checks and comparing the current price to the analyst target range. Read-only.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "symbols": {
      "description": "One or more equity symbols (max 75 per call). Exact-ticker match — no name or partial-ticker resolution. Lowercase and whitespace-padded input is normalized to uppercase-trimmed before forwarding.",
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

---

## get_politician_trades

Description (verbatim):

Get disclosed trading activity of US politicians from Tip Ranks. Use when the user asks about politician/congressional trades - either for a specific stock ("which politicians traded NVDA?") or a specific politician. Data comes from public STOCK Act disclosures; amounts are ranges, not exact values, and disclosures lag the actual trade by up to 45 days.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "equity_symbol": {
      "description": "One active US equity ticker, e.g. NVDA. Exact ticker only; lowercase and surrounding whitespace are normalized. Optional when politician_name is provided.",
      "type": "string"
    },
    "politician_name": {
      "description": "Full or partial name of a US politician, e.g. 'Nancy Pelosi'. Case-insensitive. Optional when equity_symbol is provided.",
      "type": "string"
    }
  },
  "type": "object"
}
```

(Note: this schema has no `required` array. Per the parameter descriptions, at least one of `equity_symbol` / `politician_name` is expected.)

---

## get_sec_filing_index

Description (verbatim):

List a company's SEC filings, optionally filtered by form type and date. Use this to find a specific annual report (10-K), quarterly report (10-Q), or material event disclosure (8-K).

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "cursor": {
      "description": "Pagination cursor. Omit for the first page; to fetch the next page, pass the value from the prior response's next field.",
      "type": "string"
    },
    "form_type": {
      "description": "One or more SEC form types to filter by, e.g. ['10-K'] or ['10-K','10-Q']. Omit to return all form types.",
      "items": {
        "type": "string"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "since": {
      "description": "Earliest filing date to include, ISO format YYYY-MM-DD. Omit to return filings from all dates.",
      "type": "string"
    },
    "symbol": {
      "description": "Stock ticker symbol, e.g. AAPL. Exact match — no partial or name-based lookup. Lowercase input is normalized to uppercase.",
      "type": "string"
    },
    "until": {
      "description": "Latest filing date to include, ISO format YYYY-MM-DD. Omit to return filings up to the most recent.",
      "type": "string"
    }
  },
  "required": [
    "symbol"
  ],
  "type": "object"
}
```

---

## get_sec_filing_facts

Description (verbatim):

Get reported facts tagged under specific GAAP concept names in one or more SEC filings — financial figures (revenue, net income, assets, debt) and disclosures (e.g. debt schedules, related-party transactions, subsequent events) alike. Use get_sec_filing_index to find filing_ids first.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "concepts": {
      "description": "GAAP concept names to fetch (1-10) — covers numeric line items (e.g. NetIncomeLoss, Assets, Revenues) and disclosure text blocks (e.g. ScheduleOfDebtTableTextBlock) alike. Guess common names directly; only call get_sec_filing_facts_catalog first if a guess comes back with no matching facts.",
      "items": {
        "type": "string"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "filing_ids": {
      "description": "Filing identifiers to fetch facts for (1-3), from get_sec_filing_index. Default to a single filing_id — most questions are answered by periods already embedded in one filing (10-Ks carry ~3 fiscal years, 10-Qs carry current + prior-year). Pass more than one only when the question needs data a single filing structurally can't have, e.g. a trend longer than 3 years, or confirming a disclosed subsequent event landed in the next filing.",
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
    "filing_ids",
    "concepts"
  ],
  "type": "object"
}
```

---

## get_sec_filing_facts_catalog

Description (verbatim):

List the distinct GAAP concept names tagged in an SEC filing, with their reporting periods and dimension breakdowns. Use this only after a direct get_sec_filing_facts guess comes back empty, or for open-ended "what's unusual/notable" questions — most common financial questions should guess concept names directly rather than calling this first.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "axis_name_in": {
      "description": "Only return concepts broken down by at least one of these dimension names. For 'what's unusual/notable in this filing' questions, try named-entity axes like SubsequentEventTypeAxis, BusinessAcquisitionAxis, RelatedPartyTransactionsByRelatedPartyAxis, LegalEntityAxis, or MajorCustomersAxis before falling back to an unfiltered call. Omit to skip this filter.",
      "items": {
        "type": "string"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "concept_contains": {
      "description": "Case-insensitive substring filter on concept name, e.g. 'Debt'. Omit to return all concepts.",
      "type": "string"
    },
    "filing_id": {
      "description": "Filing identifier to catalog, from get_sec_filing_index.",
      "type": "string"
    },
    "offset": {
      "description": "Pagination offset. Omit for the first page; to fetch the next page, pass the value from the prior response's next_offset field.",
      "type": "integer"
    }
  },
  "required": [
    "filing_id"
  ],
  "type": "object"
}
```

---

## get_sec_filing

Description (verbatim):

Read an SEC filing's table of contents or a specific section's text. Use get_sec_filing_index to find a filing_id first.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "filing_id": {
      "description": "Filing identifier returned by get_sec_filing_index.",
      "type": "string"
    },
    "section": {
      "description": "Section identifier from the table of contents (the id field). Omit to get the full table of contents; provide to get a specific section's text.",
      "type": "string"
    }
  },
  "required": [
    "filing_id"
  ],
  "type": "object"
}
```
