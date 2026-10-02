# Connector changes

A dated record of what Robinhood's Trading MCP connector (`https://agent.robinhood.com/mcp/trading`)
exposes, as seen by this kit. Robinhood publishes no changelog for the connector, and its published
tool list trails the live server, so this file is the kit's record of both. Unofficial; not affiliated
with Robinhood Markets, Inc.

- **What is compared:** tool names, verbatim descriptions and verbatim input schemas, taken from an MCP
  client's tool list. Taking a snapshot never calls a tool.
- **Where it lives:** `connector/tools.snapshot.json` (machine-readable, with a sha256 per description and
  schema), `connector/schemas/*.md` (the same text, readable), `connector/tool-classes.json` (the class a
  human assigned to each tool) and `connector/FIELDS.md` (response field names seen on read-only calls).
- **How it is refreshed:** `python3 tools/snapshot_tools.py ingest <tools-list.json>` appends a dated entry
  below. A human then classifies any new tool, and `python3 tools/check_drift.py` names every rule, skill,
  hook and manifest that has to follow. CI runs `check_drift.py --staleness 30` weekly, so a snapshot older
  than 30 days opens an issue.
- **Seen a change first?** Open a "connector behavior" issue with the tool name, what changed and the date.
  Never paste account numbers, balances or positions.

## 2026-09-21: baseline snapshot (81 tools)

Captured from the live connector's tool list on 2026-09-21. Schemas only: no tool was called.

- **81 tools** in seven families: accounts, positions and P&L (11), market data (15), research (11), equity
  and OCO orders (8), option and crypto orders (10), scanner (8), watchlists and alerts (18).
- **Classes** (`connector/tool-classes.json`): 48 read, 3 enrollment link, 5 simulate, 14 non-money write,
  6 cancel or delete, and 5 money: `place_equity_order`, `place_advanced_order`, `place_option_order`,
  `place_crypto_order` and `exercise_option`.
- **24 live tools are not in Robinhood's published list** of 57 (support article "Trading with your agent",
  read 2026-09-21): `cancel_advanced_order`, `cancel_option_exercise`, `create_alert`, `delete_alert`,
  `exercise_option`, `get_advanced_orders`, `get_alert_log`, `get_alerts`,
  `get_crypto_account_onboarding_info`, `get_equity_analyst_ratings`, `get_equity_news`,
  `get_index_historicals`, `get_limited_margin_upgrade_info`, `get_politician_trades`,
  `get_scanner_datapoints`, `get_sec_filing`, `get_sec_filing_facts`, `get_sec_filing_facts_catalog`,
  `get_sec_filing_index`, `mark_alerts_read`, `place_advanced_order`, `preview_scan`,
  `review_advanced_order` and `update_alert`. Two of them place or exercise orders with real money.
- **Named in descriptions but not exposed:** `replace_option_order` (named by `review_option_order`; to
  change an option order, cancel it and submit a new one) and `get_crypto_tax_lots` (named by
  `preview_crypto_order` and `place_crypto_order`, so their `tax_lots` parameter cannot be filled in).
  `get_quotes` is named by `get_watchlist_items` and is not exposed either. `get_market_hours` does not
  exist; an earlier version of this kit assumed it did.
- **Live although this kit's v1 called them unavailable:** `preview_scan` and `get_scanner_datapoints`.
- **Truncated at the source:** the descriptions of `place_option_order`, `create_scan` and `preview_scan`
  end in `… [truncated]` in the client's tool list; the rest of that text was not served. Their input
  schemas are complete.
- **Schema conventions:** no tool carries MCP annotations (`readOnlyHint`, `destructiveHint` and so on). No
  schema uses the `enum` or `default` keywords, so allowed values and defaults exist only in description
  text; `connector/param-facts.json` pins the ones this kit relies on. Every array parameter is typed
  `["null", "array"]`, even when it is required, and every schema sets `additionalProperties: false`.

## 2026-09-22: behavior observed on read-only calls

Recorded from read-only calls and one `review_equity_order` simulation. Field names only; see
`connector/FIELDS.md`. These are behavior notes, not a tool-list refresh; the tool list was not
re-captured on this date.

- Every response is wrapped as `{"data": ..., "guide": "..."}`. The guide carries Robinhood's instructions
  for presentation, masking and pagination.
- `get_advanced_orders` and `review_advanced_order` returned "the tool you requested cannot be found or does
  not exist" on both capture accounts, although the client lists them. The kit treats the OCO family as
  possibly not enabled for an account, and never reads that error as "no OCO orders".
- `get_index_quotes` returned an empty `state`, so the kit does not use it as a market-session signal.
- `review_equity_order` returns `order_checks` as an object (empty when there are no alerts), not an array,
  and a `market_data_disclosure` that must be shown verbatim with the ticket. The review had no side effects.
- `get_pnl_trade_history` rows carry no asset-class or holding-term field, and option closes appear under the
  underlying ticker, so a row is never assumed to be a share sale.
