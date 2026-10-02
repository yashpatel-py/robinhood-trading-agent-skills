# Example: an options check: your exit rules plus the expiration radar

> **Sandbox data.** Every account, price and order on this page is synthetic. It comes from the
> fixture household in `evalkit/fixtures/household.json`, served by `sandbox/mock_server.py`
> (variant `base`), the same data the evals use. Unofficial; not affiliated with Robinhood
> Markets, Inc. Not investment or tax advice.

**Skill:** `robinhood-options-monitor` · **Stated time:** Mon 2026-11-16, 8:05 PM ET (after the close) · **How it was
made:** the tool calls below were sent to the sandbox over MCP, and the numbers were computed by
running the skill's bundled scripts on those responses. Only the wording of the answer was written
by hand, following the skill's output template. Account numbers are masked the way the skill masks
them (`••••` plus the last 4); the tools received the full values.

## The request

> Check my options against my rules. What should I know about them this week?
>
> ```toml
> # robinhood-skills:config
> [options.exits]
> profit_target_pct = "50"
> stop_loss_pct = "40"
> time_stop_dte = "7"
> max_hold_days = "UNSET"
> exit_price_rule = "bid"
> ```

The rules are pasted in the chat, the way a claude.ai or ChatGPT project would carry them.
`kitconfig.py get` parsed them: profit target 50, stop 40, time stop 7 DTE, exit price rule "bid";
`unset: ["max_hold_days"]`, so that rule is skipped and named, never guessed. `radar_days` was not
given, so the radar uses its shipped default of 7 days.

## What the agent called

| # | Call | What came back (abridged) |
|---|---|---|
| 1 | `get_accounts {}` | Agentic ••••X4F1 (`option_level_3`); the other two accounts are read-only |
| 2 | `get_option_positions {account_number: ••••X4F1, nonzero: true}` | KO short ×1, avg 1.10, exp 2026-11-20; SPY long ×2, avg 12.00, exp 2026-11-20; AMD long ×1, avg 2.05, exp 2026-11-27; `next` null |
| 3 | `get_option_instruments {ids: "<3 ids>"}` | KO 2026-11-20 70 call, `sellout_datetime` 2026-11-20T20:30:00Z; SPY 2026-11-20 650 call, `sellout_datetime` 2026-11-20T20:30:00Z; AMD 2026-11-27 165 call, `sellout_datetime` 2026-11-27T20:30:00Z |
| 4 | `get_option_quotes {instrument_ids: [3 ids]}` | KO 70C: bid 2.20 / ask 2.30; SPY 650C: bid 21.40 / ask 21.60; AMD 165C: bid 3.30 / ask 3.40 (updated 20:04 ET) |
| 5 | `get_equity_quotes {symbols: ["AMD", "KO", "SPY"]}` | AMD 161.40, KO 72.05, SPY 671.20 |
| 6 | `get_portfolio {account_number: ••••X4F1}` | buying power "2480.00" |
| 7 | `get_equity_positions {account_number: ••••X4F1}` | KO 100 (all 100 held as collateral for the short call), AMD 12.5, PLTR 30 |
| 8 | `get_equity_fundamentals {symbols: ["KO"]}` | KO `ex_dividend_date` 2026-11-18, `dividend_per_share` 0.53 |
| 9 | `get_earnings_results {symbol}` for AMD, KO, SPY | AMD next report 2027-01-27 (after the AMD 165C expires); KO 2027-02-10; SPY is a fund with no earnings. No report falls inside a held contract's life, so no implied-move check |

Scripts:

| Script | Output (abridged) |
|---|---|
| `option_exits.py run` (3 positions, quotes, the rules above) | fired 3 of 3: SPY 650C ×2 `PROFIT_TARGET` + `TIME_STOP` (+78.3%, $1,880.00 at the bid, 4 DTE); AMD 165C ×1 `PROFIT_TARGET` (+61.0%, $125.00); KO 70C ×1 short `STOP_LOSS` + `TIME_STOP` (−109.1%, −$120.00 at the ask); `not_configured` ["max_hold_days"]; every `limit_price` null ("blank: needs your limit price") |
| `expiry_risk.py run` (radar 7 days, KO ex-dividend 2026-11-18 $0.53, buying power $2,480.00, 100 KO held) | SPY `AUTO_EXERCISE_CASH_NEED`: cash needed $130,000.00 vs buying power $2,480.00, shortfall $127,520.00; KO `EARLY_ASSIGNMENT_BEFORE_EX_DIV`: in the money by $2.05, extrinsic $0.15 at the bid / $0.20 at the mid < dividend $0.53, covered by 100 held (estimate) |

## The answer

```text
EXIT SIGNAL: Dollars at stake: SPY 650C ×2 auto-exercise needs $130,000.00 vs $2,480.00 buying power (Fri) · KO 70C short: assignment would deliver 100 KO for $7,000.00 (covered) · SPY 650C ×2 hit your +50% target and 7-DTE time stop ($4,280.00 at the bid)
OPTIONS CHECK · as of 20:05 ET Mon 2026-11-16 (you said so; regular session closed) · read: Agentic ••••X4F1
SPY 2026-11-20 650C ×2 (long, Agentic ••••X4F1): PROFIT_TARGET and TIME_STOP fired (+78.3%, $1,880.00, at bid $21.40 vs your +50%; 4 DTE ≤ your 7)
  Closing spec (needs your limit price and quantity): SELL TO CLOSE · LIMIT $____ · gfd · regular hours · bid $21.40 / ask $21.60 / mid $21.50
AMD 2026-11-27 165C ×1 (long, Agentic ••••X4F1): PROFIT_TARGET fired (+61.0%, $125.00, at bid $3.30 vs your +50%) · opened at $2.05 · 11 DTE
  Closing spec (needs your limit price and quantity): SELL TO CLOSE · LIMIT $____ · gfd · regular hours · bid $3.30 / ask $3.40 / mid $3.35
KO 2026-11-20 70C ×1 (short, Agentic ••••X4F1): STOP_LOSS and TIME_STOP fired (−109.1%, −$120.00, at ask $2.30 vs your −40%; opened at $1.10; 4 DTE)
  Closing spec (needs your limit price and quantity): BUY TO CLOSE · LIMIT $____ · gfd · regular hours · bid $2.20 / ask $2.30 / mid $2.25
Radar (next 7 days):
  Wed 11-18: KO ex-dividend $0.53; your short 70C extrinsic $0.20 (mid), $0.15 (bid) < dividend → early-assignment risk, estimate (covered by 100 KO)
  Fri 11-20: SPY 650C ×2 in the money by $21.20 → auto-exercise cash need $130,000.00; buying power $2,480.00; shortfall $127,520.00.
             Robinhood's force-close time for these contracts (sellout_datetime): 15:30 ET Fri 2026-11-20.
  Fri 11-20: KO 70C short in the money by $2.05: if assigned, deliver 100 KO (covered by the 100 held)
  Earnings: none inside a held contract's life (AMD reports 2027-01-27, after the AMD call expires)
Rules not configured: max_hold_days (set it in [options.exits] to evaluate)
Not evaluated: none · Quotes outside regular hours may change at the open.
Nothing was reviewed, placed or exercised. Say which position to close, how many contracts and at what limit,
and I'll simulate that closing order with review_option_order.
```

## Why it went this way

- **`nonzero: true` on the positions call.** Without it, the connector also returns closed contracts
  (here a TSLA 250P that expired in October), and a monitor would "check" a position that no longer exists.
- **Longs are marked at the bid, shorts at the ask.** That is what closing now would actually get or
  cost. The app's mark would show the SPY calls a little higher; the rule is judged on the conservative side.
- **The closing specs have a blank limit price.** Your `exit_price_rule` of "bid" is used only when you
  ask for a simulated close. A fired rule is evidence that your threshold was crossed; the price and the
  quantity to trade are still your decision.
- **The biggest dollar figure is not a P&L.** Two in-the-money SPY calls expiring Friday would, if
  exercised, buy 200 SPY at $650.00: $130,000.00 against $2,480.00 of buying power. Robinhood says it
  generally auto-exercises long options $0.01 or more in the money at expiration if buying power covers
  it, and may close positions before expiration when it does not; `sellout_datetime` is its own
  force-close time for each contract. Check the app for your account.
- **Early assignment is an estimate.** A call holder tends to exercise early the day before an
  ex-dividend date when the time value left is smaller than the dividend. That is the holder's choice,
  so the radar calls it a risk, not a fact.
- **Nothing was exercised or closed.** `exercise_option` is blocked in every order mode, and the monitor
  reviews a closing order only when you ask and give the price.

## Reproduce it

```sh
git clone https://github.com/yashpatel-py/robinhood-trading-agent-skills && cd robinhood-trading-agent-skills
claude --plugin-dir . --mcp-config sandbox/mcp.json --strict-mcp-config
```

`--plugin-dir .` loads the skills and hooks from the clone, and `--strict-mcp-config` makes the
sandbox the only MCP server in the session. Start the request with "(Context: it is Monday
2026-11-16, 8:05 PM ET.)" so the skill uses the fixture's clock. The model's wording will differ from
run to run; the figures will not, because they come from the fixture and the scripts.
