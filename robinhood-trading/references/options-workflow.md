# Automated options scanning and review

An unattended loop that finds option candidates matching the user's criteria, analyzes
them, and produces review-ready order specs. It automates everything except the decision
to place — the user keeps that.

Read `options-criteria.md` first. Every threshold used here comes from that file, and an
unset value is a stop condition, not a prompt to improvise.

## Contents
- [Preflight — run before every scan](#preflight--run-before-every-scan)
- [Stage 1: underlying universe](#stage-1-underlying-universe)
- [Stage 2: contract selection](#stage-2-contract-selection)
- [Stage 3: analysis](#stage-3-analysis)
- [Stage 4: gates](#stage-4-gates)
- [Stage 5: present and hand off](#stage-5-present-and-hand-off)
- [Position monitoring](#position-monitoring)
- [PDT accounting](#pdt-accounting)

## Preflight — run before every scan

Each of these is a hard stop. Failing one means reporting the failure, not degrading to a
partial run — a scan that silently skips its capital check will happily surface a trade
the account cannot afford.

1. **Criteria complete.** Any `<UNSET>` in `options-criteria.md` → stop, name the missing
   fields, ask the user.
2. **Options approved.** `get_accounts` → the tradable account's options level must be
   `option_level_2` or higher. Empty or `option_level_0` → stop and route to
   `get_option_level_upgrade_info`. Re-fetch `get_accounts` rather than trusting a cached
   level; it changes the moment an application is approved.
3. **Funded.** `get_portfolio` → buying power must exceed `max_premium_per_contract * 100`
   plus `reserve_cash`. A zero-balance agentic account is the most common blocker and the
   easiest to miss, because every other tool still returns data.
4. **PDT budget.** See [PDT accounting](#pdt-accounting). No budget left → scan in
   report-only mode and say so.
5. **Market state.** `get_market_hours` if available, else `get_equity_tradability` on a
   liquid proxy. Outside `scan_sessions` → report only, no order specs. Option quotes
   outside regular hours are wide and often stale.

## Stage 1: underlying universe

Call `get_scanner_filter_specs` before building anything — filter names are wire enums and
cannot be guessed.

Build or reuse a scan filtered to `price_min`/`price_max` and `min_avg_volume`, then
`run_scan`. Apply `symbols_allowlist` / `symbols_blocklist` after the scan rather than as
filters, so the blocklist can't be silently dropped by a scan-config replacement.

Liquidity in the *underlying* is a prerequisite for liquidity in its *options*. A thin
stock's option chain will fail the spread gate anyway, so screening it out here saves the
chain fetch.

## Stage 2: contract selection

For each surviving symbol:

1. `get_option_chains` → expirations. Keep those inside `dte_min`..`dte_max`.
2. `get_option_instruments` → contracts for those expirations, `structure` side only.
3. `get_option_quotes` → bid, ask, greeks, open interest.

Then keep contracts where all of these hold:

- `|delta|` within `delta_min`..`delta_max`
- `open_interest >= min_open_interest`
- `ask * 100 <= max_premium_per_contract * 100`
- spread% `<= max_spread_pct`

Batch the quote calls. A chain fetch per symbol per scan is the expensive part of this
loop, so filter expirations before pulling instruments, not after.

**If `structure` is a debit spread**, pair each surviving contract with a further-OTM
contract on the same expiration to form the short leg:

- **Debit call spread** — buy the lower strike, sell the higher.
- **Debit put spread** — buy the higher strike, sell the lower.
- Strike distance must fall inside `spread_width_min`..`spread_width_max`.
- Both legs must independently clear `min_open_interest` and `max_spread_pct`. A clean
  long leg paired with an illiquid short leg is an unexitable position — you can be forced
  to leg out at a bad price precisely when you most want out.
- Net debit = long ask − short bid. That is the true entry cost, and it is worse than
  (long mid − short mid) because you cross both spreads in the unfavorable direction.
  Size against the ask/bid version, never the mid/mid version.

## Stage 3: analysis

Compute these for every surviving contract. They are the numbers that decide whether a
trade is sane, and most are not returned directly:

| Metric | Formula |
|---|---|
| Cost | `ask * 100` |
| Max loss | `ask * 100` — the entire premium; a long option can expire worthless |
| Breakeven | call: `strike + ask` · put: `strike - ask` |
| Required move | `(breakeven - underlying) / underlying * 100` |
| Spread % | `(ask - bid) / ((ask + bid) / 2) * 100` |
| Account % | `cost / total_value * 100` |
| Profit target price | `ask * (1 + profit_target_pct/100)` |
| Stop price | `ask * (1 - stop_loss_pct/100)` |

For debit spreads the arithmetic changes — max loss is still capped, but so is max gain:

| Metric | Debit call spread | Debit put spread |
|---|---|---|
| Net debit | `long_ask - short_bid` | `long_ask - short_bid` |
| Cost / max loss | `net_debit * 100` | `net_debit * 100` |
| Width | `short_strike - long_strike` | `long_strike - short_strike` |
| Max gain | `(width - net_debit) * 100` | `(width - net_debit) * 100` |
| Breakeven | `long_strike + net_debit` | `long_strike - net_debit` |
| Risk/reward | `net_debit : (width - net_debit)` | same |

Show risk/reward explicitly on spreads. A spread costing $0.80 on a $1.00 width risks $80
to make $20 — the position can be right and still be a poor trade, and that only becomes
visible when the ratio is stated rather than left for the reader to compute.

**Required move is the one to lead with.** It answers "how far does the stock have to
travel before I stop losing money," and on cheap out-of-the-money contracts it is
routinely a double-digit percentage within a few weeks. Everything else about a candidate
can look clean while this number quietly makes it implausible.

Then check events: `get_earnings_calendar` for any earnings between now and expiration,
applying `earnings_policy` and `earnings_buffer_days`.

## Stage 4: gates

Reject, with the reason recorded, any candidate that:

- costs more than `max_position_pct` of account value
- would exceed `max_concurrent` given current `get_option_positions`
- would leave buying power below `reserve_cash`
- violates `earnings_policy`
- is on `symbols_blocklist`

Report what was rejected and why, not just what survived. A scan that returns two
candidates out of forty is telling you something about the criteria, and that signal is
lost if the rejects are dropped silently.

If nothing survives, say so plainly. Zero candidates is a valid, common, and correct
outcome — never loosen a threshold to produce results. Loosening criteria to fill a report
converts the user's risk limits into decoration.

## Stage 5: present and hand off

For each surviving candidate, run `review_option_order` with `position_effect: open`,
`type: limit`, and `chain_symbol` + `underlying_type` supplied so fees and collateral
resolve. Surface every `order_checks` alert verbatim.

- **Single leg:** one leg, price at the ask. Omit `direction` — it is derived.
- **Debit spread:** two legs, same expiration, opposite sides, `direction: debit`,
  `ratio_quantity: 1` on both. `price` is the net premium of the whole strategy per unit
  of quantity, always positive. Only `limit` is available with 2+ legs.
  Multi-leg requires a margin or limited-margin account — it is rejected on cash and
  retirement accounts.

Then present, ranked by required move ascending:

```
## <SYMBOL> <STRIKE><C|P> <EXPIRY>   —   $<cost>  (<account%> of account)

Underlying   $<price>          Breakeven  $<breakeven>
Contract     $<bid> / $<ask>   Required move  <+X.X%>  in <N> days
Spread       <X.X%>            Delta <d>  ·  OI <n>  ·  DTE <n>
Max loss     $<cost>  (100% of premium)
Earnings     <date in window, or none>

Exits per your criteria:  target $<price> (+X%)  ·  stop $<price> (-X%)
                          time stop at <N> DTE  ·  hard exit <date>

Pre-trade alerts: <verbatim, or "none">
```

Close with the order spec and an explicit handoff: the user places it in the Robinhood app.
Never call `place_option_order`.

## Position monitoring

Separate from scanning, and it should run more often — an open position needs watching more
than a hypothetical one needs finding.

For each open option position (`get_option_positions`, quotes via `get_option_quotes`):

- current P&L vs `profit_target_pct` and `stop_loss_pct`
- DTE vs `time_stop_dte`
- days held vs `max_hold_days`
- earnings now inside the remaining window

When any exit condition is met, alert immediately with the current quote and a
review-ready closing spec (`position_effect: close`, opposite side). Say which rule fired
and what the position is worth now.

Exits are where automation earns its keep. Entries can wait for the next scan; a stop that
fires while nobody is looking cannot.

## PDT accounting

Under $25,000 in equity, four or more day trades in five rolling business days flags the
account as a pattern day trader and gets it restricted. This constrains an automated loop
far more than most people expect — a scanner that opens and closes in the same session
burns the budget in days.

Before proposing anything that could close same-day, count day trades in the trailing five
business days from `get_option_orders` and `get_equity_orders` — a day trade is an open
and close of the same contract on the same calendar day. Compare against `pdt_budget`.

When the budget is exhausted, keep scanning and reporting but mark every candidate
`report-only — PDT budget exhausted`. Do not propose an entry whose exit rules would
likely force a same-day close.
