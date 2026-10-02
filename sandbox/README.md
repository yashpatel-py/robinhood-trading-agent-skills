# Preflight sandbox: try the skills without a Robinhood account

*Unofficial. Not affiliated with Robinhood Markets, Inc.*

`mock_server.py` is a local MCP server that stands in for Robinhood's Trading connector. It uses a
synthetic household: an Agentic account, an individual account and a Roth IRA, with stocks, options,
crypto, orders, alerts and scans. It exposes the same 81 tool names and input schemas as the live
connector, and it answers from the same fixture the evals use. It never connects to Robinhood or to any
network, and every number in it is made up.

## Add it to your agent

Run these from the repository root. The server needs Python 3.9 or later and nothing else.

```sh
# Claude Code
claude mcp add rh-sandbox -- python3 "$PWD/sandbox/mock_server.py"

# Any MCP client that reads a JSON config: copy sandbox/mcp.json and make the script path absolute
```

Then install the skills as usual and ask something like "What's my buying power?", "Harvest my TSLA
loss" or "Which of my positions have no protection?". The answers use the fixture's numbers. The README
demo quotes the same numbers, which come from `evalkit/golden/demo.json`.

To use the sandbox, remove or disable your real Robinhood connector first, or keep them clearly apart.
Both expose the same tool names, and only the server prefix tells them apart.

## What is real and what is not

- **Tool names and input schemas are real.** They are copied verbatim from the connector snapshot
  (`connector/tools.snapshot.json`, 81 tools, captured 2026-09-21).
- **Response field names follow the live capture** (`connector/FIELDS.md`). Where a tool returned
  nothing or errored in the capture, the shape is an assumption, and
  `evalkit/fixtures/fieldmap.json` marks it as one.
- **Values are fiction.** Account numbers, prices, orders and news are all fixture text.
- **Order-placing tools place nothing.** `place_*` and `exercise_option` answer
  `CANARY_LIVE_ORDER_PLACED_<tool>` plus `SANDBOX: no order placed`. With the Preflight plugin in
  Claude Code, the order guard blocks those calls before they reach the server.
- **Cancels and deletions change nothing.** Cancels and `delete_alert` with `confirm: true` answer
  `CANARY_CANCEL_EXECUTED_<tool>` plus `SANDBOX: nothing was cancelled or deleted`.
- **Writes are acknowledged but not stored.** Alerts, watchlists, scans and read marks come back as
  accepted, and every call still sees the same household.
- **The simulations validate their inputs.** The review and preview tools check what the connector
  checks and return a response of the captured shape. For example, the equity review returns
  `order_checks` and a `market_data_disclosure` you must show verbatim, and the OCO review enforces the
  0.25% distance rule.

## Options

```text
python3 sandbox/mock_server.py [--variant base|regular|cash_l2|no_greeks|oco_disabled] [--anchor ISO|today] [--log FILE]
```

- `--variant`:
  - `regular` sets the clock to the regular session (Tuesday 11:02 ET).
  - `cash_l2` makes the Agentic account a cash account with options level 2.
  - `no_greeks` drops Greeks and open interest from option quotes.
  - `oco_disabled` makes the OCO tools answer "the tool you requested cannot be found or does not
    exist", as they did on the capture accounts.
- `--anchor`: the default is Mon 2026-11-16 20:05 ET, so the numbers match the README. `today` moves
  every fixture date by the same offset, which keeps the relative dates (for example "18 days to
  long-term") and drops the weekday alignment.
- `--log`: appends one JSON line per tool call (time, tool, error flag) to a local file.

## Protocol

The server speaks MCP over stdio as newline-delimited JSON-RPC 2.0. It handles `initialize`,
`notifications/initialized`, `ping`, `tools/list` and `tools/call`. `resources/list`,
`resources/templates/list` and `prompts/list` return empty lists. The tests in
`tests/test_mock_server.py` run the server as a subprocess.
