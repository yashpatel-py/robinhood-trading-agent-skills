# Robinhood Trading MCP — Scanner / screening tool schemas (verbatim)

Source: ToolSearch schema load of MCP server `00000000-0000-4000-8000-000000000000` (Robinhood Trading connector), captured 2026-09-21. No Robinhood tool was invoked; schemas only.

Capture notes:

- The ToolSearch payload exposes only `description`, `name`, and `parameters` (input JSON schema). No `annotations` (readOnlyHint / destructiveHint / idempotentHint / openWorldHint) and no `title` field were present in the payload for any of these tools, so none are reproduced here.
- Descriptions for `create_scan` and `preview_scan` arrive truncated at the source: the harness cuts them and appends `… [truncated]`. Retrying each individually returned the identical truncated text. The text below is everything that was served; the missing tail is NOT recoverable from schema loading. Parameter-level descriptions in the JSON schemas are complete.
- Parameter key order is as served (alphabetical).

Tools in this family: `get_scanner_filter_specs`, `create_scan`, `get_scans`, `run_scan`, `update_scan_filters`, `update_scan_config`, `preview_scan`, `get_scanner_datapoints`

## get_scanner_filter_specs

Full tool name: `mcp__00000000-0000-4000-8000-000000000000__get_scanner_filter_specs`

### Description (verbatim)

List every valid scanner filter type and how to use it. Call this before constructing filters for create_scan or update_scan_filters — do not guess filter_type names. This tool takes no parameters.

### Input schema (verbatim)

```json
{
  "additionalProperties": false,
  "type": "object"
}
```

## create_scan

Full tool name: `mcp__00000000-0000-4000-8000-000000000000__create_scan`

### Description (verbatim)

Create a new saved scanner (screener) on the user's account — or, with scan_id, update an existing one by appending a new configuration version. Filters can be enum-based (from get_scanner_filter_specs) or expression-based (a raw market-data expression built from get_scanner_datapoints). Returns the scan's id, title, applied filters, and live market results. Prefer an enum filter_type whenever one covers the request: enum filters are pre-validated and render as standard, user-editable filters in Legend. Build a raw expression only when no enum filter covers the request, and validate it with preview_scan before saving it here. This is the only tool that saves expression filters — update_scan_filters rejects them — so to change a scan that has an expression filter, call this tool with that scan's scan_id and the full desired filter set. Columns display values, filters screen. To show a datapoint in results without restricting matches, pass it in columns — never fake it with a no-op filter like ">= 0", which pollutes the scan's filter list. A column is a display_name plus an expression, or a standard column's display name alone. A couple of contextual columns supporting the filters make a scan read better in Legend. Two execution paths: - New scan, enum-only filters (or none), no columns: composes multiple Beacon operations — create the scan, apply the preset configuration if non-INITIAL, apply filters, set the title. If a step after the initial create fails, the scan still exists in its partial state; the response surfaces what was applied, and update_scan_filters / update_scan_config can fix the rest. - scan_id set, any expression filter present, or any columns present: a single transactional Beacon call that persists the filters and columns as a new ACTIVE configuration version (on the given scan, or on a brand-new one). All-or-nothing — an expression the market-data provider rejects means NOTHING is persisted, and the provider's validation error is returned. Earlier versions of a scan are never edited. A non-INI… [truncated]

> NOTE: description truncated at the source (ToolSearch output ends in `… [truncated]`); the remainder was not served.

### Input schema (verbatim)

```json
{
  "additionalProperties": false,
  "properties": {
    "columns": {
      "description": "Optional result columns to display on top of what the filters and the standard default columns already show. Each column: display_name plus either an expression (from get_scanner_datapoints) or nothing (a standard column referenced by name, e.g. \"Market cap\"). Columns display values, filters screen — to show a datapoint without restricting results, add a column here, never a no-op filter (e.g. >= 0). A couple of contextual columns supporting the filters make the scan read better in Legend.",
      "items": {
        "additionalProperties": false,
        "properties": {
          "display_name": {
            "description": "Column header, and the column's identity: matching an existing column's name (case-insensitively) customizes that column instead of adding a duplicate, and two columns cannot share a name. A filter with non-default interval/length names its column with those parameters (e.g. a 5-minute RSI filter's column is \"RSI (14, 5M)\") — matching it requires that exact name; a bare \"RSI\" next to it adds a separate, default-parameters RSI column. Without expression, must name a standard column (e.g. \"Market cap\", \"RSI\", \"Volume\") — an unknown name is rejected with the full list of valid ones.",
            "type": "string"
          },
          "expression": {
            "description": "Raw market-data expression computing the column's value, built from get_scanner_datapoints (same language as expression filters), e.g. \"optionsPutDayVolume / optionsCallDayVolume\". Omit to reference a standard column by display_name alone. If the expression equals a standard column's canonical form, the column is saved as that standard column and display_name is REPLACED by its canonical name (e.g. \"optionsPutDayVolume\" always saves as \"Total put volume\").",
            "type": "string"
          },
          "order": {
            "description": "Relative ordering among the columns passed in this call: lower comes first, columns without an order go last in the order given. The scan's standard default columns (Symbol, Name, price, Volume, ...) always come before these.",
            "maximum": 2147483647,
            "minimum": -2147483648,
            "type": [
              "null",
              "integer"
            ]
          },
          "visible": {
            "description": "Whether the column shows in results. When omitted, a NEW column is visible, but a column customizing an existing filter-created column keeps that column's current visibility (some filter columns are deliberately hidden) — pass an explicit value to change it. A hidden column is still computed and saved.",
            "type": [
              "null",
              "boolean"
            ]
          }
        },
        "required": [
          "display_name"
        ],
        "type": "object"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "filters": {
      "description": "Custom filters, enum-based and/or expression-based. Enum filter: filter_type (FILTER_TYPE_... enum from get_scanner_filter_specs) plus predicate/values and optional interval/length/plot. Expression filter: expression (built from get_scanner_datapoints; omit filter_type) plus predicate/values and an optional display_title. Required when scan_id is set. Prefer an enum filter_type whenever one covers the request; validate an expression with preview_scan before saving it here.",
      "items": {
        "additionalProperties": false,
        "properties": {
          "display_title": {
            "description": "Optional short label for an expression filter, shown as the results-column header (e.g. \"Relative volume (30D)\"). Only used with expression.",
            "type": "string"
          },
          "expression": {
            "description": "Raw market-data expression to screen on, e.g. \"dayVolume / volumeAvg(candleCount=30, candlePeriod=\\\"1d\\\", session=\\\"all\\\")\" with a numeric predicate, or a whole comparison like \"tradeAllDay.price > closeAvg(candleCount=50, candlePeriod=\\\"1d\\\", session=\\\"all\\\")\" with predicate \"=\" and values [\"True\"]. Accepted by preview_scan (validate without saving) and create_scan (save); update_scan_filters rejects expressions — to change an expression filter on a saved scan, call create_scan with that scan's scan_id and the full filter set (a new configuration version is appended; earlier versions are preserved). Omit filter_type when set. Prefer an enum filter_type whenever one covers the request.",
            "type": "string"
          },
          "filter_type": {
            "description": "Wire-format enum name, e.g. \"FILTER_TYPE_RSI\". See the scanner-filter-specs resource for valid values. Omit when supplying expression.",
            "type": "string"
          },
          "interval": {
            "description": "Time granularity for time-series filters (e.g. \"1d\"). Use one of the supported_intervals from the filter's scanner-filter-specs entry. Not used with expression — encode granularity inside the expression.",
            "type": "string"
          },
          "length": {
            "description": "Lookback length for filters that need one (e.g. RSI period of 14). Use one of the supported_lengths from the filter's scanner-filter-specs entry. Not used with expression.",
            "maximum": 2147483647,
            "minimum": -2147483648,
            "type": "integer"
          },
          "plot": {
            "description": "Plot / price-field input for filters that have one (e.g. \"open\" or \"close\" for % Change). Use one of the supported_plots from the filter's scanner-filter-specs entry. Not used with expression.",
            "type": "string"
          },
          "predicate": {
            "description": "Wire-format enum name, e.g. \"PREDICATE_GREATER_THAN\". See the scanner-filter-specs resource for the predicates supported by each filter.",
            "type": "string"
          },
          "values": {
            "description": "Threshold values. Single-element for unary predicates, two-element for BETWEEN, multi-element for IN_LIST/ANY_OF. For a boolean expression screen, exactly [\"True\"].",
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
          "predicate",
          "values"
        ],
        "type": "object"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "preset": {
      "description": "Starting preset for the new scan. One of: INITIAL (no preset; only valid if filters are provided), DAILY_GAINERS, DAILY_LOSERS, HIGH_OPTIONS_VOLUME_IV, UPCOMING_EARNINGS. Defaults to DAILY_GAINERS when no filters are provided, INITIAL when filters are provided. A non-INITIAL preset cannot be combined with expression filters, columns, or scan_id.",
      "type": "string"
    },
    "scan_id": {
      "description": "Optional. Update an existing scan by appending the given filters (and columns) as a NEW configuration version, which becomes the active one — earlier versions are preserved, never edited. The version holds exactly what is passed (REPLACE semantics, not merge): read the scan's current filters and columns with get_scans first and pass the complete intended set. This is how to change a scan that has expression filters, which update_scan_filters cannot carry. Cortex-managed scans are rejected. Omit to create a new scan.",
      "type": "string"
    },
    "title": {
      "description": "Optional custom title for the saved scan. If omitted: a new scan gets Beacon's default title for the preset, and a scan_id update keeps the scan's existing title.",
      "type": "string"
    }
  },
  "type": "object"
}
```

## get_scans

Full tool name: `mcp__00000000-0000-4000-8000-000000000000__get_scans`

### Description (verbatim)

List the authenticated user's saved scanners (also called screeners). A scan is a saved set of filters and columns that filters the market for instruments matching specific criteria (e.g. "RSI > 70 and Volume > 1M"). The user creates these in Legend or via the create_scan tool. Returns one entry per scan with its id, title, active filters, configured columns, sort order, and a flag indicating whether the scan is managed by Cortex (Legend's AI agent). Cortex-managed scans are read-only via MCP — they can be run with run_scan but not modified with update_scan_filters or update_scan_config. This tool takes no parameters.

### Input schema (verbatim)

```json
{
  "additionalProperties": false,
  "type": "object"
}
```

## run_scan

Full tool name: `mcp__00000000-0000-4000-8000-000000000000__run_scan`

### Description (verbatim)

Execute a saved scanner (also called screener) and return live market results. A scan's filters are evaluated against current market data at request time — results are real-time, not cached. Returns the scan's title, the total number of matching instruments, a list of instrument rows (with ticker, instrument_id, type, and one cell per visible column), plus the active sort and filters. The agent should present results as a table and mention this is live data. Parameters: - scan_id (required) — the scan identifier from get_scans or create_scan. Returns an error if the scan does not exist or does not belong to the calling user. Use get_scans first to discover available scan_ids if the user has not specified one.

### Input schema (verbatim)

```json
{
  "additionalProperties": false,
  "properties": {
    "scan_id": {
      "description": "The scan identifier to execute. Get this from get_scans or create_scan.",
      "type": "string"
    }
  },
  "required": [
    "scan_id"
  ],
  "type": "object"
}
```

## update_scan_filters

Full tool name: `mcp__00000000-0000-4000-8000-000000000000__update_scan_filters`

### Description (verbatim)

Replace the filters on an existing saved scan. The complete filter set provided replaces whatever filters the scan had — this is REPLACE semantics, not merge. To add a single filter to an existing scan, the agent must first read the scan (via get_scans or run_scan), then call this tool with all the existing filters plus the new one. Parameters: - scan_id (required) — the scan to modify. Get from get_scans or create_scan. - filters (required) — the complete new filter set. Send [] to clear all filters. Each filter has filter_type (FILTER_TYPE_... enum), predicate, values, optional interval/length. Call get_scanner_filter_specs for valid combinations. Restrictions: - Cortex-managed scans (cortex_managed: true in get_scans) are rejected with a user-friendly error. - Returns an error and does not apply any change if any filter fails validation. Returns the scan with its new filters and fresh live results.

### Input schema (verbatim)

```json
{
  "additionalProperties": false,
  "properties": {
    "filters": {
      "description": "The complete set of filters the scan should have after the update. REPLACE semantics — to add a filter, supply all existing filters plus the new one. To remove a filter, omit it from the array. To clear all filters, send []. Each filter: filter_type (FILTER_TYPE_... enum), predicate (>, <, =, BETWEEN, etc.), values, optional interval (e.g. 1d), optional length (e.g. 14 for RSI). Call get_scanner_filter_specs first for valid filter_type / predicate / interval / length combinations.",
      "items": {
        "additionalProperties": false,
        "properties": {
          "display_title": {
            "description": "Optional short label for an expression filter, shown as the results-column header (e.g. \"Relative volume (30D)\"). Only used with expression.",
            "type": "string"
          },
          "expression": {
            "description": "Raw market-data expression to screen on, e.g. \"dayVolume / volumeAvg(candleCount=30, candlePeriod=\\\"1d\\\", session=\\\"all\\\")\" with a numeric predicate, or a whole comparison like \"tradeAllDay.price > closeAvg(candleCount=50, candlePeriod=\\\"1d\\\", session=\\\"all\\\")\" with predicate \"=\" and values [\"True\"]. Accepted by preview_scan (validate without saving) and create_scan (save); update_scan_filters rejects expressions — to change an expression filter on a saved scan, call create_scan with that scan's scan_id and the full filter set (a new configuration version is appended; earlier versions are preserved). Omit filter_type when set. Prefer an enum filter_type whenever one covers the request.",
            "type": "string"
          },
          "filter_type": {
            "description": "Wire-format enum name, e.g. \"FILTER_TYPE_RSI\". See the scanner-filter-specs resource for valid values. Omit when supplying expression.",
            "type": "string"
          },
          "interval": {
            "description": "Time granularity for time-series filters (e.g. \"1d\"). Use one of the supported_intervals from the filter's scanner-filter-specs entry. Not used with expression — encode granularity inside the expression.",
            "type": "string"
          },
          "length": {
            "description": "Lookback length for filters that need one (e.g. RSI period of 14). Use one of the supported_lengths from the filter's scanner-filter-specs entry. Not used with expression.",
            "maximum": 2147483647,
            "minimum": -2147483648,
            "type": "integer"
          },
          "plot": {
            "description": "Plot / price-field input for filters that have one (e.g. \"open\" or \"close\" for % Change). Use one of the supported_plots from the filter's scanner-filter-specs entry. Not used with expression.",
            "type": "string"
          },
          "predicate": {
            "description": "Wire-format enum name, e.g. \"PREDICATE_GREATER_THAN\". See the scanner-filter-specs resource for the predicates supported by each filter.",
            "type": "string"
          },
          "values": {
            "description": "Threshold values. Single-element for unary predicates, two-element for BETWEEN, multi-element for IN_LIST/ANY_OF. For a boolean expression screen, exactly [\"True\"].",
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
          "predicate",
          "values"
        ],
        "type": "object"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "scan_id": {
      "description": "The scan to modify. Get this from get_scans or create_scan. Cortex-managed scans (cortex_managed: true in get_scans output) are rejected.",
      "type": "string"
    }
  },
  "required": [
    "scan_id",
    "filters"
  ],
  "type": "object"
}
```

## update_scan_config

Full tool name: `mcp__00000000-0000-4000-8000-000000000000__update_scan_config`

### Description (verbatim)

Change the sort order of a saved scan's results table, and/or replace its extra result columns. The scan's filters are always preserved. Parameters: - scan_id (required) — the scan to modify. - sorting_column (required unless columns is provided) — display name of the column to sort by. Must match a column on the scan; the error response lists available columns when no match is found. - sorting_direction — "asc" or "desc". Required with sorting_column. - columns (optional) — REPLACE the scan's extra result columns (beyond the filters' own columns and the standard defaults). The scan keeps its current filters verbatim and gets a new configuration version — earlier versions are preserved. To add one column, pass the complete intended set (current columns from get_scans plus the new one). Columns display values, filters screen — to show a datapoint without restricting results, this is the tool, never a no-op filter. Restrictions: - Cortex-managed scans (cortex_managed: true in get_scans) are rejected. - Omitted/empty columns means sorting-only. To remove every extra column, use create_scan with this scan's scan_id and its current filters. - An expression matching a standard column's canonical form is saved as that standard column, canonical name included. Returns the scan with the changes applied and fresh live results.

### Input schema (verbatim)

```json
{
  "additionalProperties": false,
  "properties": {
    "columns": {
      "description": "REPLACE the scan's extra result columns (the ones beyond what its filters and the standard defaults contribute): the scan keeps its current filters verbatim and gets a new configuration version whose extra columns are exactly this list. To add one column, read the scan's current columns with get_scans and pass the complete intended set, carrying visible: false for any column get_scans shows as hidden (a hidden extra column re-sent without it becomes visible). Omitted or empty means sorting-only, columns untouched; to remove every extra column, use create_scan with this scan's scan_id and its current filters. Each column: display_name plus either an expression (from get_scanner_datapoints) or nothing (a standard column referenced by name, e.g. \"Market cap\").",
      "items": {
        "additionalProperties": false,
        "properties": {
          "display_name": {
            "description": "Column header, and the column's identity: matching an existing column's name (case-insensitively) customizes that column instead of adding a duplicate, and two columns cannot share a name. A filter with non-default interval/length names its column with those parameters (e.g. a 5-minute RSI filter's column is \"RSI (14, 5M)\") — matching it requires that exact name; a bare \"RSI\" next to it adds a separate, default-parameters RSI column. Without expression, must name a standard column (e.g. \"Market cap\", \"RSI\", \"Volume\") — an unknown name is rejected with the full list of valid ones.",
            "type": "string"
          },
          "expression": {
            "description": "Raw market-data expression computing the column's value, built from get_scanner_datapoints (same language as expression filters), e.g. \"optionsPutDayVolume / optionsCallDayVolume\". Omit to reference a standard column by display_name alone. If the expression equals a standard column's canonical form, the column is saved as that standard column and display_name is REPLACED by its canonical name (e.g. \"optionsPutDayVolume\" always saves as \"Total put volume\").",
            "type": "string"
          },
          "order": {
            "description": "Relative ordering among the columns passed in this call: lower comes first, columns without an order go last in the order given. The scan's standard default columns (Symbol, Name, price, Volume, ...) always come before these.",
            "maximum": 2147483647,
            "minimum": -2147483648,
            "type": [
              "null",
              "integer"
            ]
          },
          "visible": {
            "description": "Whether the column shows in results. When omitted, a NEW column is visible, but a column customizing an existing filter-created column keeps that column's current visibility (some filter columns are deliberately hidden) — pass an explicit value to change it. A hidden column is still computed and saved.",
            "type": [
              "null",
              "boolean"
            ]
          }
        },
        "required": [
          "display_name"
        ],
        "type": "object"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "scan_id": {
      "description": "The scan to modify. Get this from get_scans or create_scan. Cortex-managed scans are rejected.",
      "type": "string"
    },
    "sorting_column": {
      "description": "Display name of the column to sort by (e.g. \"Volume\", \"% Change\", \"RSI\"). Must match a column on the scan — call get_scans / run_scan first to see available columns. Required unless columns is provided.",
      "type": "string"
    },
    "sorting_direction": {
      "description": "\"asc\" or \"desc\" (ascending or descending). Required with sorting_column.",
      "type": "string"
    }
  },
  "required": [
    "scan_id"
  ],
  "type": "object"
}
```

## preview_scan

Full tool name: `mcp__00000000-0000-4000-8000-000000000000__preview_scan`

### Description (verbatim)

Run a filter set against live market data and return the matching instruments WITHOUT saving a scan. Nothing is persisted and no scan_id is returned. A preview behaves exactly like creating a scan with these filters would — same filters, same default columns, same live evaluation — so it is the way to validate a filter set before create_scan or update_scan_filters. Expression-based filters run here and save through create_scan (update_scan_filters rejects them). An expression filter sets expression (and omits filter_type): either a value expression with a numeric predicate (expression "dayVolume / volumeAvg(candleCount=30, candlePeriod=\"1d\", session=\"all\")", predicate PREDICATE_GREATER_THAN_OR_EQUAL, values ["1.5"]), or a boolean screen carrying the whole comparison inside the expression (expression "tradeAllDay.price > closeAvg(candleCount=50, candlePeriod=\"1d\", session=\"all\")", predicate PREDICATE_EQUAL, values ["True"]). An invalid expression comes back as a validation error from the market-data provider instead of failing a later save. Prefer an enum filter_type whenever one covers the request — enum filters are pre-validated and render as standard, user-editable filters in Legend. Call get_scanner_filter_specs first; build a raw expression only when no enum filter covers the request. Columns display values, filters screen. To show a datapoint in results without restricting matches, pass it in columns — never fake it with a no-op filter like ">= 0", which pollutes the scan's filter list. A column is a display_name plus an expression, or a standard column's display name alone ({"display_name": "Market cap"}). Scans read best with a few contextual columns supporting the filters (e.g. a put/call screen showing both raw volumes). Parameters: - filters (required, non-empty) — the complete filter set to evaluate. - columns (optional) — extra result columns on top of the filters' own columns and the standard defaults. Note: an expression matching a standard column's canonical form is shown as that standard c… [truncated]

> NOTE: description truncated at the source (ToolSearch output ends in `… [truncated]`); the remainder was not served.

### Input schema (verbatim)

```json
{
  "additionalProperties": false,
  "properties": {
    "columns": {
      "description": "Optional result columns to display on top of what the filters and the standard default columns already show. Each column: display_name plus either an expression (raw market-data expression from get_scanner_datapoints) or nothing (a standard column referenced by name, e.g. \"Market cap\"). Columns display values, filters screen — to show a datapoint without restricting results, add a column here, never a no-op filter (e.g. >= 0).",
      "items": {
        "additionalProperties": false,
        "properties": {
          "display_name": {
            "description": "Column header, and the column's identity: matching an existing column's name (case-insensitively) customizes that column instead of adding a duplicate, and two columns cannot share a name. A filter with non-default interval/length names its column with those parameters (e.g. a 5-minute RSI filter's column is \"RSI (14, 5M)\") — matching it requires that exact name; a bare \"RSI\" next to it adds a separate, default-parameters RSI column. Without expression, must name a standard column (e.g. \"Market cap\", \"RSI\", \"Volume\") — an unknown name is rejected with the full list of valid ones.",
            "type": "string"
          },
          "expression": {
            "description": "Raw market-data expression computing the column's value, built from get_scanner_datapoints (same language as expression filters), e.g. \"optionsPutDayVolume / optionsCallDayVolume\". Omit to reference a standard column by display_name alone. If the expression equals a standard column's canonical form, the column is saved as that standard column and display_name is REPLACED by its canonical name (e.g. \"optionsPutDayVolume\" always saves as \"Total put volume\").",
            "type": "string"
          },
          "order": {
            "description": "Relative ordering among the columns passed in this call: lower comes first, columns without an order go last in the order given. The scan's standard default columns (Symbol, Name, price, Volume, ...) always come before these.",
            "maximum": 2147483647,
            "minimum": -2147483648,
            "type": [
              "null",
              "integer"
            ]
          },
          "visible": {
            "description": "Whether the column shows in results. When omitted, a NEW column is visible, but a column customizing an existing filter-created column keeps that column's current visibility (some filter columns are deliberately hidden) — pass an explicit value to change it. A hidden column is still computed and saved.",
            "type": [
              "null",
              "boolean"
            ]
          }
        },
        "required": [
          "display_name"
        ],
        "type": "object"
      },
      "type": [
        "null",
        "array"
      ]
    },
    "filters": {
      "description": "Filters to evaluate, enum-based and/or expression-based. Enum filter: filter_type from get_scanner_filter_specs plus predicate/values and optional interval/length/plot. Expression filter: expression (omit filter_type) plus predicate/values and an optional display_title. At least one filter is required.",
      "items": {
        "additionalProperties": false,
        "properties": {
          "display_title": {
            "description": "Optional short label for an expression filter, shown as the results-column header (e.g. \"Relative volume (30D)\"). Only used with expression.",
            "type": "string"
          },
          "expression": {
            "description": "Raw market-data expression to screen on, e.g. \"dayVolume / volumeAvg(candleCount=30, candlePeriod=\\\"1d\\\", session=\\\"all\\\")\" with a numeric predicate, or a whole comparison like \"tradeAllDay.price > closeAvg(candleCount=50, candlePeriod=\\\"1d\\\", session=\\\"all\\\")\" with predicate \"=\" and values [\"True\"]. Accepted by preview_scan (validate without saving) and create_scan (save); update_scan_filters rejects expressions — to change an expression filter on a saved scan, call create_scan with that scan's scan_id and the full filter set (a new configuration version is appended; earlier versions are preserved). Omit filter_type when set. Prefer an enum filter_type whenever one covers the request.",
            "type": "string"
          },
          "filter_type": {
            "description": "Wire-format enum name, e.g. \"FILTER_TYPE_RSI\". See the scanner-filter-specs resource for valid values. Omit when supplying expression.",
            "type": "string"
          },
          "interval": {
            "description": "Time granularity for time-series filters (e.g. \"1d\"). Use one of the supported_intervals from the filter's scanner-filter-specs entry. Not used with expression — encode granularity inside the expression.",
            "type": "string"
          },
          "length": {
            "description": "Lookback length for filters that need one (e.g. RSI period of 14). Use one of the supported_lengths from the filter's scanner-filter-specs entry. Not used with expression.",
            "maximum": 2147483647,
            "minimum": -2147483648,
            "type": "integer"
          },
          "plot": {
            "description": "Plot / price-field input for filters that have one (e.g. \"open\" or \"close\" for % Change). Use one of the supported_plots from the filter's scanner-filter-specs entry. Not used with expression.",
            "type": "string"
          },
          "predicate": {
            "description": "Wire-format enum name, e.g. \"PREDICATE_GREATER_THAN\". See the scanner-filter-specs resource for the predicates supported by each filter.",
            "type": "string"
          },
          "values": {
            "description": "Threshold values. Single-element for unary predicates, two-element for BETWEEN, multi-element for IN_LIST/ANY_OF. For a boolean expression screen, exactly [\"True\"].",
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
          "predicate",
          "values"
        ],
        "type": "object"
      },
      "type": [
        "null",
        "array"
      ]
    }
  },
  "required": [
    "filters"
  ],
  "type": "object"
}
```

## get_scanner_datapoints

Full tool name: `mcp__00000000-0000-4000-8000-000000000000__get_scanner_datapoints`

### Description (verbatim)

List the market-data datapoints available for building raw expressions: functions (with signatures) and fields, each with a category and description. Expressions serve two purposes — filters (screen on the expression's value) and columns (display it in results) — on preview_scan, create_scan and update_scan_config. Call this before constructing one; do not guess datapoint names or signatures. For filters, use expressions only for screens no enum filter covers: check get_scanner_filter_specs first, and prefer an enum filter_type whenever one fits. For columns, a standard column referenced by display_name alone needs no expression at all. Scoping a scan to a set of instruments is done here, not with an enum filter: the descriptive category's symbol field restricts a scan to named tickers or to an index's members. See the guide for the exact predicates. Requires a category, since the full catalog is too large to return at once. Pass "technical", "price_volume", "fundamental", "options", "volatility", "quote" or "descriptive"; the response lists every category with a description so you can pick a different one if needed.

### Input schema (verbatim)

```json
{
  "additionalProperties": false,
  "properties": {
    "category": {
      "description": "Which part of the catalog to return. One of: technical (chart studies - rsi, macd, adx, bollinger, pivots), price_volume (prices, changes, moving averages, volume), fundamental (marketCap, peRatio, eps, dividends, sector, earnings dates), options (options volume and open interest on the underlying), volatility (implied and realized volatility, IV rank), quote (bid/ask, sizes, spread), descriptive (symbol, type, listing, date arithmetic). Also accepts \"function\" or \"field\" to slice by kind instead, or any category name from a previous response. Required - the full catalog is too large to return at once.",
      "type": "string"
    }
  },
  "required": [
    "category"
  ],
  "type": "object"
}
```
