# Example: the agent report card for one week

> **Sandbox data.** Every account, price and order on this page is synthetic. It comes from the
> fixture household in `evalkit/fixtures/household.json`, served by `sandbox/mock_server.py`
> (variant `base`), the same data the evals use. Unofficial; not affiliated with Robinhood
> Markets, Inc. Not investment or tax advice.

**Skill:** `robinhood-agent-report-card` · **Stated time:** Mon 2026-11-16, 8:05 PM ET (after the close) · **How it was
made:** the tool calls below were sent to the sandbox over MCP, and the numbers were computed by
running the skill's bundled scripts on those responses. Only the wording of the answer was written
by hand, following the skill's output template. Account numbers are masked the way the skill masks
them (`••••` plus the last 4); the tools received the full values.

## The request

> What did my agent do last week? My audit log folder is evalkit/fixtures/audit.

The folder holds a synthetic, hash-chained audit log for the same fixture week (built by
`tools/make_audit_fixture.py`). On a real install the plugin's hook writes it under
`~/.local/state/robinhood-skills/audit/`, masked and local only.

## What the agent called

`rh_time.py to_utc {"date": "2026-11-09"}` → `created_at_gte` "2026-11-09T05:00:00Z" (midnight ET; a
naive date would be read as UTC and shift the window by five hours). Window: 2026-11-09 → 2026-11-15 ET.

| # | Call | What came back (abridged) |
|---|---|---|
| 1 | `get_accounts {}` | Agentic ••••X4F1; read-only ••••M7Q5 and ••••P0Z9 |
| 2 | `get_equity_orders {account_number: ••••X4F1, placed_agent: "agentic", created_at_gte: "2026-11-09T05:00:00Z"}` | 11 orders |
| 3 | `get_equity_orders {account_number: ••••X4F1, created_at_gte: "2026-11-09T05:00:00Z"}` and `get_option_orders {account_number: ••••X4F1, created_at_gte}`, no `placed_agent` (every source; recorded with filter "all") | 12 equity orders: the 11 above plus your KO buy (`placed_agent` "user"); 0 option orders |
| 4 | `get_option_orders {account_number: ••••X4F1, placed_agent: "agentic", created_at_gte}` | 0 orders |
| 5 | `get_crypto_orders {rhs_account_number: ••••3418, created_at_gte}` | 2 orders (no source filter exists for crypto) |
| 6 | `get_advanced_orders {account_number: ••••X4F1, created_at_gte}` | 1 OCO (no source filter exists for OCOs) |
| 7 | `get_equity_orders` and `get_option_orders {account_number, placed_agent: "agentic", created_at_gte}` for ••••M7Q5 and ••••P0Z9 | 0 orders in each (agents can trade only the Agentic account; verified, not assumed) |
| 8 | `get_realized_pnl {account_number: ••••3418 (rhs), start_date: "2026-11-09", end_date: "2026-11-15"}` | `total_returns` "237.23" |
| 9 | `get_pnl_trade_history {account_number: ••••3418, span: "month"}` | 5 rows over the month; the script keeps the window's |
| 10 | `get_portfolio {account_number: ••••X4F1}` | `total_value` "18308.50" (turnover denominator only) |
| 11 | `get_equity_historicals {symbols: ["SPY"], start_time, end_time, interval: "hour"}` | "The sandbox fixture only has daily bars (interval=day)." |

Scripts:

| Script | Output (abridged) |
|---|---|
| `audit_verify.py run` (the folder above, the window) | `chain_ok` true · 1 file, 70 lines · 37 order simulations · 8 place calls · blocked: place_equity_order (NO_MATCHING_REVIEW, 2026-11-12 10:14 ET) and exercise_option (SIMULATE_ONLY, 2026-11-12 18:26 ET) · `possible_injection`: place_equity_order 1 call after `get_equity_news`, blocked |
| `reconcile.py run` (15 broker order rows, 10 reads, the audit output; the OCO's 2 legs, which `get_equity_orders` also returns, fold into the OCO and count once) | matched 8; no local audit entry: PLTR buy ($412.00); placed without review: KO buy ($285.60); tagged `user` inside Agentic: KO buy ($140.00, cancelled); source unknown: ETH-USD sell ($305.00); read-only accounts: `verified_none` ×2 |
| `scorecard.py run` | orders 9, filled 7, rejected 1, cancelled 1 · realized $237.23 · agent rows 4 (win rate 100.0%, n=4) · slippage 4.4 bps (n=5) · turnover 15.2% · `spy_request` for hourly SPY bars |

The scorecard asked for hourly SPY bars to compare each agent buy→sell pair with SPY over the same
hold. The sandbox answered `The sandbox fixture only has daily bars (interval=day).`, so the comparison is skipped: a daily close would be off by up to a day
on a hold of about a day, which can be the whole result.

## The answer

```text
ACTION NEEDED: Dollars at stake: 1 order ($412.00) in Agentic ••••X4F1 has no local audit entry (placed by another agent, app or machine) · 1 order ($285.60) placed without a matching review in the log · 1 order ($140.00) in Agentic ••••X4F1 that Robinhood marks as not placed by an agent · 1 possible prompt-injection attempt right after get_equity_news
AGENT REPORT · Agentic ••••X4F1 · 2026-11-09 → 2026-11-15 ET (stated window) · as of 2026-11-16 20:05 ET
Verified: no agent orders in read-only accounts ••••M7Q5, ••••P0Z9

| Agent orders | Filled | Rejected | Cancelled | Placed without review | No local audit entry |
|---|---|---|---|---|---|
| 9 | 7 | 1 | 1 | 1 | 1 |

Flagged: PLTR buy $412.00 by dollar amount (2026-11-13 15:42 ET, no local audit entry) · KO buy 4, $285.60 (2026-11-12 11:02 ET, no matching review in the log) · KO buy 2 limit $70.00, cancelled (2026-11-12 12:20 ET, placed by you)
Source not distinguishable (crypto, OCO): 1 order (ETH sell 0.1, $305.00)
Realized in Agentic (Robinhood, all sources): +$237.23 · agent sells matched to realized rows: 4 (win rate 100.0%, n=4, small sample; average gain +$31.88) · unclassified rows: 1
Slippage vs the reviewed quote: 4.4 bps average (n=5; positive = worse than the quote at review)
Flags: averaging down ×0 · new buy within 24 h of a realized loss ×0 · turnover 15.2% of account value
Trade-matched SPY: skipped (only daily SPY bars were available; daily closes are not substituted)
Guard log (local, chain intact): 37 order simulations · 8 orders sent by this kit · 2 blocked attempts (no matching review ×1, exercise blocked ×1) · possible prompt injection ×1: place_equity_order blocked 2026-11-12 10:14 ET, 1 call after get_equity_news
Not measured: account return versus an index (deposits and withdrawals aren't visible to the connector), tax impact, fees
```

## Why it went this way

- **The broker's own filter decides who placed an order.** `get_equity_orders` and `get_option_orders`
  take `placed_agent`, so the report reads the agent's orders, then every source with no filter
  (`placed_agent` is an open set: a `user` filter alone would miss recurring buys, dividend
  reinvestment and other sources). Crypto and OCO lists have no such filter; those rows are labeled
  "source not distinguishable" instead of being counted for or against the agent.
- **"No local audit entry" is the signal worth reading.** The $412.00 PLTR buy is tagged agent-placed by
  Robinhood, yet this machine's log never sent it. Another agent, app or machine holds the same
  connection. That is worth a look even when the trade itself is harmless.
- **The read-only accounts were checked, not assumed.** Agents can trade only the Agentic account, so an
  agent order anywhere else would mean something is wrong upstream. Both came back empty: `verified_none`.
- **The injection flag comes from the call sequence, not from text.** A place call blocked one call after
  `get_equity_news` is the pattern of a news item that told the agent to trade. The guard blocked it; the
  report makes sure you hear about it.
- **Every rate carries its n, and nothing is called performance.** Four matched sells is a small sample.
  Account return against an index is not measured because deposits and withdrawals are invisible to the
  connector, and the SPY comparison was skipped rather than faked with the wrong bars.
- **This fixture week ran in confirm mode.** The log's `mode` field says `confirm`, which is why it shows
  8 orders sent by this kit. In the default simulate-only mode the kit sends none, and the report card is
  mostly about spotting orders that came from somewhere else.
- **The chain is tamper-evident, not tamper-proof.** Each log line hashes the one before it, so an edited
  or deleted line breaks the chain at that point (the repo ships a tampered copy that breaks at line 52).
  Someone who can write the file can rebuild the whole chain; the log is a record, not a vault.

## Reproduce it

```sh
git clone https://github.com/yashpatel-py/robinhood-trading-agent-skills && cd robinhood-trading-agent-skills
claude --plugin-dir . --mcp-config sandbox/mcp.json --strict-mcp-config
```

`--plugin-dir .` loads the skills and hooks from the clone, and `--strict-mcp-config` makes the
sandbox the only MCP server in the session. Start the request with "(Context: it is Monday
2026-11-16, 8:05 PM ET.)" so the skill uses the fixture's clock. The model's wording will differ from
run to run; the figures will not, because they come from the fixture and the scripts.
