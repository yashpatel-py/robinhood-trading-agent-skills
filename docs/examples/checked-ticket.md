# Example: a checked order ticket that catches a cross-account wash sale

> **Sandbox data.** Every account, price and order on this page is synthetic. It comes from the
> fixture household in `evalkit/fixtures/household.json`, served by `sandbox/mock_server.py`
> (variant `base`), the same data the evals use. Unofficial; not affiliated with Robinhood
> Markets, Inc. Not investment or tax advice.

**Skill:** `robinhood-trading` · **Stated time:** Mon 2026-11-16, 8:05 PM ET (after the close) · **How it was
made:** the tool calls below were sent to the sandbox over MCP, and the numbers were computed by
running the skill's bundled scripts on those responses. Only the wording of the answer was written
by hand, following the skill's output template. Account numbers are masked the way the skill masks
them (`••••` plus the last 4); the tools received the full values.

**Session line** (Claude Code with the plugin; printed by the SessionStart hook):
`Robinhood order mode: SIMULATE-ONLY (enforced by the robinhood-trading plugin hook in this Claude Code session). place_*/exercise_*/replace_* calls will be blocked; prepare a ticket and use the handoff text. CONFIRM MODE: INSTALLED, OFF.`

## The request

> Buy 3 AMD in my agentic account, limit $160.

## What the agent called

| # | Call | What came back (abridged) |
|---|---|---|
| 1 | `get_accounts {}` | Agentic ••••X4F1 is the one `agentic_allowed` account (limited margin); ••••M7Q5 (individual) and ••••P0Z9 (`ira_roth`) are read-only to the agent |
| 2 | `get_equity_quotes {symbols: ["AMD"]}` | last 161.4000 · bid 161.3500 · ask 161.4500 · quote time 20:04 ET |
| 3 | `get_equity_tradability {account_number: ••••X4F1, symbols: ["AMD"]}` | AMD tradable; fractional tradable; sessions regular, extended and all-day all true |
| 4 | `get_portfolio {account_number: ••••X4F1}` | buying power "2480.00" |
| 5 | `get_equity_orders {account_number: ••••X4F1, symbol: "AMD", created_at_gte: "2026-07-19T04:00:00Z"}` + `get_pnl_trade_history {account_number: ••••3418 (rhs), span: "3month", symbol: "AMD"}` | 6 AMD orders, 1 page; trade history: 1 row |
| 6 | `get_equity_orders {account_number: ••••M7Q5, symbol: "AMD", created_at_gte: "2026-07-19T04:00:00Z"}` + `get_pnl_trade_history {account_number: ••••8867 (rhs), span: "3month", symbol: "AMD"}` | 1 AMD order, 1 page; trade history: 1 row |
| 7 | `get_equity_orders {account_number: ••••P0Z9, symbol: "AMD", created_at_gte: "2026-07-19T04:00:00Z"}` | 0 AMD orders, 1 page |
| 8 | `get_earnings_results {symbol: "AMD"}` | next report 2027-01-27 after the close, `verified` false; the 2026-11-03 report is past |
| 9 | `review_equity_order {account_number: ••••X4F1, symbol: "AMD", side: "buy", type: "limit", quantity: "3", limit_price: "160.00", time_in_force: "gfd", market_hours: "regular_hours"}` | `order_checks` `{}` · quote bid 161.3500 / ask 161.4500 · `market_data_disclosure` present |

Scripts, in the order the skill runs them:

| Script | Input (abridged) | Output (abridged) |
|---|---|---|
| `rh_time.py session` | `{"now": "2026-11-16T20:05:00-05:00", "now_source": "user_stated"}` | `session` "outside_regular_trading_day", next regular open 2026-11-17T09:30:00-05:00 |
| `wash_sale.py window` | `{"date": "2026-11-16", "gtc_lookback_days": "90"}` | window 2026-10-17 → 2026-12-16; `created_at_gte` 2026-07-19T04:00:00Z; `pnl_span` "3month" |
| `wash_sale.py run` (mode `planned_buy`, 3 shares in ••••X4F1) | the raw order pages and trade-history rows of all 3 accounts | status `conflict`; loss sale ••••M7Q5 2026-11-03, 10 sh at $41.20/sh; washed 3 sh → $123.60 disallowed, deferred; earliest clean buy 2026-12-04 |
| `order_lint.py lint` | the review parameters, with `quantity` and `limit_price` marked as coming from you | valid; warning `QUEUES_NEXT_OPEN`; estimate $480.00 (qty×limit, agent estimate) |

## The answer

```text
ORDER TICKET: BUY 3 AMD (simulated with review_equity_order, NOT placed)
CONFLICT: Dollars at stake: $123.60 of AMD loss deferred into replacement basis if bought before 2026-12-04
As of   20:05 ET Mon 2026-11-16 (you said so) · session: regular session closed; next open 09:30 ET Tue 2026-11-17
Account Agentic ••••X4F1 (limited margin) · buying power $2,480.00 (get_portfolio)
Order   BUY 3 AMD · LIMIT $160.00 (from you; below the $161.45 ask, so it rests until filled or the day ends) · time in force gfd · regular_hours
        Tagged regular_hours after the close, it waits for Tuesday's 09:30 ET open. For it to work overnight
        instead, say so: all_day_hours (24 Hour Market) takes limit orders only.
Size    3 shares (from you)
Est.    $480.00 = 3 × limit (agent estimate; the equity review returns a quote and checks, not a cost)
Robinhood pre-trade checks (order_checks, verbatim):
  > {} (none returned; that does not replace your own check)
Checks run by this kit:
  Wash sale — read ••••X4F1 ••••M7Q5 ••••P0Z9 (window 2026-10-17 → 2026-12-16): CONFLICT. Individual ••••M7Q5 sold 10 AMD
              at a loss on 2026-11-03 ($41.20/share). This buy washes 3 of those shares → $123.60 of that loss is
              disallowed now and added to the cost basis of the 3 new shares (deferred, not lost: this
              buy is in a taxable account). Buying on or after 2026-12-04 avoids it.
              This buy also opens its own window: an AMD loss sale in a taxable account through 2026-12-16 would be washed by it.
  Household — not configured ([policy] max_symbol_pct_household is UNSET)
  Earnings — next report 2027-01-27 after the close (unverified by the company)
  Policy — not configured (no [policy] limits set)
Market data disclosure (verbatim):
  > Market data disclosure (Preflight fixture text, not Robinhood's wording): the quote shown with this simulated order is synthetic test data from the Preflight household fixture. It is not an offer to buy or sell any security, and it may not reflect the price an order would receive.

Nothing was placed. I simulated this order against your Agentic account (••••X4F1) with Robinhood's review
tool. Robinhood's documented flow is: the agent previews, you confirm, and the agent places. This kit runs in
simulate-only mode, so that last step is switched off. To act on it, enter the order yourself in the Robinhood
app. Robinhood does not document placing orders by hand inside the Agentic account, and if you place it from a
different account, the buying power, tax lots and alerts will differ from this simulation, so check them there
first.

Or turn on confirm mode (Claude Code with this plugin) and approve this exact ticket in a permission prompt.

What this is: rule arithmetic on your Robinhood data. What it isn't: tax advice. Whether to wait until
2026-12-04 is your call.
```

## Then the user pushes

> Place it anyway.

The skill does not call `place_equity_order`: it is simulate-only, and the session line says so. The
guard exists for the time a model does call it anyway (a confused model, or text injected into a tool
result). Here is what the hook does with that call, run on its own with the exact event Claude Code
would send:

```text
$ printf '%s' '{"hook_event_name":"PreToolUse","tool_name":"mcp__rh-sandbox__place_equity_order","tool_input":{…the ticket above…},…}' \
    | sh hooks/guard.sh money; echo "exit=$?"
robinhood-trading guard: live order placement is disabled (simulate-only). Nothing was placed. Show the user the review/preview ticket and the handoff text (connector-rules R21). Do not retry or work around this.
exit=2
```

Exit code 2 makes Claude Code block the call and hand that message to the agent, which then answers with
the ticket again and R21(d): "The order guard blocked that call, and nothing was placed." followed by the
handoff text above.

## Why it went this way

- **The wash check read all three accounts, not just the one being traded.** The loss was realized in the
  Individual account; the buy is in the Agentic account. Robinhood reports wash sales per account, on
  separate 1099s, so neither account's paperwork would show this. The agent can read every account, so it
  checks every account.
- **Trade-history rows were matched to real sells before counting.** `get_pnl_trade_history` has no
  asset-class field. The −$412.00 row counted as a share sale only after `wash_sale.py` matched it to the
  filled 10-share AMD sell in the same account.
- **`created_at_gte` is a UTC timestamp 90 days before the window.** It bounds when an order was created,
  not when it filled, and a naive date would be read as UTC.
- **The estimate is labeled as the kit's.** `review_equity_order` returns a quote and pre-trade checks, not
  a cost; `order_checks` came back as the object `{}`, which is quoted as-is rather than read as "all clear".
- **The session is spelled out.** A limit order tagged `regular_hours` after the close waits for the next
  open. The skill says so and names the alternative rather than picking a session for you.
- **The size and the price are yours.** 3 shares and $160.00 came from the request; `order_lint.py` would
  have stopped the review with `NO_USER_SOURCE` if either had been made up.
- **The handoff is honest about where the order would land.** Robinhood documents the agent placing orders
  in the Agentic account, not a person, so "just place it in the app" comes with its caveat.

## Reproduce it

```sh
git clone https://github.com/yashpatel-py/robinhood-trading-agent-skills && cd robinhood-trading-agent-skills
claude --plugin-dir . --mcp-config sandbox/mcp.json --strict-mcp-config
```

`--plugin-dir .` loads the skills and hooks from the clone, and `--strict-mcp-config` makes the
sandbox the only MCP server in the session. Start the request with "(Context: it is Monday
2026-11-16, 8:05 PM ET.)" so the skill uses the fixture's clock. The model's wording will differ from
run to run; the figures will not, because they come from the fixture and the scripts.
