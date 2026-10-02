# Example: portfolio across three accounts

> **Sandbox data.** Every account, price and order on this page is synthetic. It comes from the
> fixture household in `evalkit/fixtures/household.json`, served by `sandbox/mock_server.py`
> (variant `base`), the same data the evals use. Unofficial; not affiliated with Robinhood
> Markets, Inc. Not investment or tax advice.

**Skill:** `robinhood-trading` · **Stated time:** Mon 2026-11-16, 8:05 PM ET (after the close) · **How it was
made:** the tool calls below were sent to the sandbox over MCP, and the numbers were computed by
running the skill's bundled scripts on those responses. Only the wording of the answer was written
by hand, following the skill's output template. Account numbers are masked the way the skill masks
them (`••••` plus the last 4); the tools received the full values.

## 1. A one-line question gets a one-line answer

> What's my buying power?

| # | Call | What came back (abridged) |
|---|---|---|
| 1 | `get_accounts {}` | 3 accounts. Agentic ••••X4F1: `agentic_allowed` true, `limited_margin`, `option_level_3`. Individual ••••M7Q5 and Roth IRA ••••P0Z9: `agentic_allowed` false (read-only to the agent). The guide: "Buying power here is not reliable; use get_portfolio." |
| 2 | `get_portfolio {account_number: ••••X4F1}` | `buying_power.buying_power` "2480.00" |

```text
Buying power, Agentic ••••X4F1: $2,480.00 (as of 20:05 ET Mon 2026-11-16, market closed; get_portfolio).
```

Why these two calls: `get_accounts` says which single account the agent can trade, and its own guide
says its buying-power figure is not reliable, so the number comes from `get_portfolio` for that
account. One question, one line, with the source and the time.

## 2. "What do I hold?" covers every account, each one named

> What do I hold across my accounts?

| # | Call | What came back (abridged) |
|---|---|---|
| 3 | `get_portfolio` × 3 (one per account) | ••••X4F1 total $18,308.50, cash $2,480.00; ••••M7Q5 total $54,016.50, cash $1,520.00; ••••P0Z9 total $31,160.00, cash $850.00 |
| 4 | `get_equity_positions {account_number}` × 3 | ••••X4F1: AMD 12.5, PLTR 30, KO 100; ••••M7Q5: NVDA 140, TSLA 40, VOO 12, KO 50; ••••P0Z9: TSLA 5, VTI 100 (`next` null on every page) |
| 5 | `get_option_positions {account_number, nonzero: true}` × 3 | 3 open contracts in ••••X4F1 (KO 70C short ×1, SPY 650C long ×2, AMD 165C long ×1); none elsewhere. Without `nonzero: true` the closed TSLA 250P row would come back too. |
| 6 | `get_crypto_positions {rhs_account_number: ••••3418}` | ETH 0.42 |
| 7 | `get_equity_quotes {symbols: ["AMD", "KO", "NVDA", "PLTR", "TSLA", "VOO", "VTI"]}` | AMD 161.4000; KO 72.0500; NVDA 228.1000; PLTR 31.2000; TSLA 262.0000; VOO 540.0000; VTI 290.0000 (last trades 20:04 ET; official closes of 2026-11-16 included, since the batch is ≤ 20) |
| 8 | `get_option_quotes {instrument_ids: [3 ids]}` | KO 2026-11-20 70C bid 2.2000 / ask 2.3000; SPY 2026-11-20 650C bid 21.4000 / ask 21.6000; AMD 2026-11-27 165C bid 3.3000 / ask 3.4000 |
| 9 | `get_crypto_quotes {symbols: ["ETH-USD"], rhs_account_number: ••••3418}` | ETHUSD bid 2999.00 / ask 3001.00 / mark 3000.00 |

Script: `python3 scripts/exposure.py run` with 13 priced positions and each account's cash
(options valued at the bid for longs and the ask for shorts, × 100; ETH at the mark).

```text
HOLDINGS · 3 accounts read (Agentic ••••X4F1 · Individual ••••M7Q5 · Roth IRA ••••P0Z9) · as of 20:05 ET Mon 2026-11-16, market closed
This read every account because the question named none; the data went to your AI provider. Say "Agentic only" to narrow it.
Household total $103,455.00 (positions $98,605.00 + cash $4,850.00)
Agentic ••••X4F1: AMD 12.5 sh $2,017.50 · PLTR 30 sh $936.00 · KO 100 sh $7,205.00 · KO 2026-11-20 70C short ×1 −$230.00 · SPY 2026-11-20 650C long ×2 $4,280.00 · AMD 2026-11-27 165C long ×1 $330.00 · ETH 0.42 coins $1,260.00 · cash $2,480.00
Individual ••••M7Q5: NVDA 140 sh $31,934.00 · TSLA 40 sh $10,480.00 · VOO 12 sh $6,480.00 · KO 50 sh $3,602.50 · cash $1,520.00
Roth IRA ••••P0Z9: TSLA 5 sh $1,310.00 · VTI 100 sh $29,000.00 · cash $850.00
Largest holdings across the household (by value, all accounts combined):
  NVDA $31,934.00 (30.9%) in ••••M7Q5
  VTI $29,000.00 (28.0%) in ••••P0Z9
  TSLA $11,790.00 (11.4%) in ••••M7Q5, ••••P0Z9
  KO $10,577.50 (10.2%) in ••••M7Q5, ••••X4F1
  VOO $6,480.00 (6.3%) in ••••M7Q5
  SPY $4,280.00 (4.1%) in ••••X4F1
Notes: options valued at bid (long) / ask (short) × 100, grouped under the underlying; no ETF look-through; household total includes cash
```

Why it looks like this:
- **Every account is named.** The agent can read all your Robinhood accounts but trade in one.
  Presenting one account's holdings as "your positions" is how a later sell or wash-sale check goes
  wrong, so each line carries its account.
- **Positions carry cost, not value.** `get_equity_positions` returns average cost, so every holding
  is priced with a quote before any dollar figure appears.
- **Options are valued at the side you would trade into** (bid for longs, ask for shorts), which is
  what closing would actually bring, not the mark.
- **Sellable is not the same as held.** The Agentic account's KO shows `shares_available_for_sells`
  of 0 because all 100 shares cover the short KO call. A sell ticket would stop on that.

## 3. Realized P&L uses the other account number

> How did my closed trades in my individual account do over the last 90 days?

| # | Call | What came back (abridged) |
|---|---|---|
| 10 | `get_realized_pnl {account_number: ••••8867 (the rhs value), span: "3month"}` | window 2026-08-18 → 2026-11-16; `total_returns` "-412.00"; one bucket with 1 trade |
| 11 | `get_pnl_trade_history {account_number: ••••8867, span: "3month"}` | 1 row: 2026-11-03T15:40:02Z sell AMD 10 @ 150.00, `realized_gain` "-412.00"; `next_cursor` "" (last page) |
| 12 | `get_equity_orders {account_number: ••••M7Q5, symbol: "AMD", created_at_gte: "2026-08-18"}` | a filled sell of 10 AMD at 150.00, last transaction 2026-11-03T15:40:00Z: the P&L row is a share sale, not an option close |

```text
Realized P&L, Individual ••••M7Q5, last 90 days (2026-08-18 → 2026-11-16, span 3month on both tools): −$412.00
1 closed trade: sold 10 AMD at $150.00 on 2026-11-03 (10:40 ET), realized −$412.00.
Short/long-term split: not provided by the connector; see Robinhood's tax center.
This AMD loss matters for 30 days: buying AMD in any account before 2026-12-04 would wash some or all of it.
```

Why it went this way:
- **Both P&L tools take the key `account_number` with the `rhs_account_number` value.** Sent the
  alphanumeric number instead, the sandbox answers `Account not found.`. On the live connector the two numbers
  can be identical on some accounts, so a wrong call can still succeed there. That is why the kit
  keeps a per-tool table instead of trusting a call that happened to work.
- **The window is stated and identical on both tools.** Their default spans differ (`3month` for
  realized P&L, `week` for trade history), so leaving `span` out would compare different windows.
- **A trade-history row is checked before it is called a share sale.** The rows carry no asset class
  (option closes appear under the underlying), so the row was matched to a filled `get_equity_orders`
  sell before the wash-sale note was added.

## Reproduce it

```sh
git clone https://github.com/yashpatel-py/robinhood-trading-agent-skills && cd robinhood-trading-agent-skills
claude --plugin-dir . --mcp-config sandbox/mcp.json --strict-mcp-config
```

`--plugin-dir .` loads the skills and hooks from the clone, and `--strict-mcp-config` makes the
sandbox the only MCP server in the session. Start the request with "(Context: it is Monday
2026-11-16, 8:05 PM ET.)" so the skill uses the fixture's clock. The model's wording will differ from
run to run; the figures will not, because they come from the fixture and the scripts.
