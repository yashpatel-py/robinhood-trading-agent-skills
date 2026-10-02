# Formulas: the prose twin of `scripts/options_screen.py`

*Unofficial — not affiliated with Robinhood.* Use this file only when Python can't run on this surface,
and label every figure "computed by hand". The script and this file must agree; the formulas follow
the owner's original options workflow tables.

## Contents
- [Conventions](#conventions)
- [preflight: structure, account and funding](#preflight-structure-account-and-funding)
- [Underlying checks](#underlying-checks)
- [Contract checks](#contract-checks)
- [Single-leg metrics](#single-leg-metrics)
- [Debit spread metrics](#debit-spread-metrics)
- [Account gates](#account-gates)
- [Earnings](#earnings)
- [Limit price by the entry rule](#limit-price-by-the-entry-rule)
- [Exit levels](#exit-levels)
- [Sort, status line and report-only](#sort-status-line-and-report-only)
- [Reason codes](#reason-codes)
- [Check numbers](#check-numbers)

## Conventions

- All math in exact decimals on the strings the tools return. Money is rounded half-up to cents;
  percentages are shown to one decimal (half-up), with full precision kept for sorting.
- `as_of` is today's date in US Eastern time. **DTE** = expiration date − `as_of`, in calendar days.
- Every contract is 100 shares. A contract with any other multiplier (an adjusted contract after a
  corporate action) is rejected: these formulas do not describe its deliverable.
- **mid** = (bid + ask) / 2. A quote with no ask, a zero ask, a negative bid, or bid above ask has no
  usable mid and is rejected `QUOTE_UNAVAILABLE`.
- **spread %** = (ask − bid) / mid × 100. This is the round-trip cost before the trade does anything:
  a 0.45/0.55 quote is 20% wide.
- **natural price** = what you pay crossing the spread: the ask for a single leg; long ask − short bid
  for a debit spread. It is worse than mid − mid because both spreads are crossed in the unfavorable
  direction. **Every cost, account-% and capital check uses the natural price**, whatever the entry
  rule; size against the ask/bid version, never the mid/mid version.
- `|delta|` is the absolute value (put deltas are negative).

## preflight: structure, account and funding

| Structure | Options level | Account type | Retirement account |
|---|---|---|---|
| `long_call`, `long_put` | `option_level_2` or higher | cash, margin or limited margin | allowed |
| `debit_call_spread`, `debit_put_spread` | `option_level_3` | margin or limited margin | not available through these tools |

Empty, missing or `option_level_0` means no options access. Checks, in order:

1. `agentic_allowed` false → stop `NOT_AGENTIC_ACCOUNT`.
2. Spread on a retirement account → stop `RETIREMENT_ACCOUNT` (no route).
3. Spread on a cash account → stop `ACCOUNT_TYPE`, route: `get_limited_margin_upgrade_info` → the user
   completes it → re-read `get_accounts` → `get_option_level_upgrade_info` only if the level is still
   below `option_level_3`.
4. Level too low (account type fine) → stop `OPTIONS_LEVEL`, route: `get_option_level_upgrade_info`.
5. Spread with unknown retirement status → stop `RETIREMENT_UNKNOWN` until confirmed.
6. Funding: need = `max_cost_per_contract_usd × contracts_per_entry + reserve_cash_usd`. Buying power
   below need → stop `NOT_FUNDED`.

Cash account: add "option trades settle T+1; buying again with unsettled proceeds risks a good-faith
violation, and 5 in 12 months trigger a 90-day restriction."

## Underlying checks

Per scanned symbol, every failing rule is recorded:

- on `symbols_blocklist` → `BLOCKLISTED`; allowlist non-empty and symbol not on it → `NOT_IN_ALLOWLIST`
- price missing → `UNDERLYING_PRICE_UNAVAILABLE`; outside `price_min`..`price_max` (inclusive) →
  `PRICE_OUT_OF_RANGE`
- average volume below `min_avg_volume` → `AVG_VOLUME`; missing → passes only if the scan's own
  average-volume filter was applied this run (`scan_filters_applied` includes `avg_volume`), otherwise
  `AVG_VOLUME_UNAVAILABLE`
- IV rank set (either bound not `OFF`): value missing → `IV_RANK_UNAVAILABLE`; converted to 0–100
  (× 100 when the source's scale is 0–1) and outside the set bounds → `IV_RANK_OUT_OF_RANGE`

The run stops instead when IV rank is set but no verified source was given
(`IV_RANK_SOURCE_UNVERIFIED`), its scale is not stated (`IV_RANK_SCALE_UNKNOWN`), or the values
contradict the stated scale (`IV_RANK_SCALE_MISMATCH`: any value above 1 on a 0–1 source, or every one
of five or more values at or below 1 on a 0–100 source).

Contracts of a rejected symbol are skipped and counted. A contract whose symbol did not come from this
pass's scan is rejected `NOT_IN_SCAN`.

## Contract checks

Only the structure's side is eligible (calls for call structures, puts for put structures); the other
side is rejected `WRONG_TYPE`.

**Every leg** (the one bought and, for spreads, the one sold):
- tradable: not `untradable`, state `active`, chain not closing-only, else `NOT_TRADABLE`
- multiplier 100, else `NON_STANDARD_MULTIPLIER`
- usable quote, else `QUOTE_UNAVAILABLE`; spread % ≤ `max_spread_pct`, else `SPREAD_PCT`
- open interest present, else `DELTA_OI_UNAVAILABLE`; ≥ `min_open_interest`, else `OPEN_INTEREST`

A short-leg failure carries the prefix `SHORT_LEG_` (for example `SHORT_LEG_OPEN_INTEREST`): a clean
long leg paired with an illiquid short leg is an unexitable position.

**The leg bought** also needs:
- `dte_min ≤ DTE ≤ dte_max`, else `DTE_OUT_OF_RANGE`
- DTE > `time_stop_dte`, else `INSIDE_TIME_STOP` (the time stop would fire on entry)
- delta present, else `DELTA_OI_UNAVAILABLE`; `delta_min ≤ |delta| ≤ delta_max`, else
  `DELTA_OUT_OF_RANGE`

**Spread pairing.** For each eligible long leg, every short leg on the same underlying, chain,
expiration and type that is further out of the money:
- debit call spread: buy the lower strike, sell the higher
- debit put spread: buy the higher strike, sell the lower
- width = |short strike − long strike|, within `spread_width_min`..`spread_width_max` (inclusive). No
  such strike in the fetched chain → the long leg is rejected `NO_SHORT_LEG_IN_WIDTH`.
- net debit ≤ 0 → `NON_POSITIVE_DEBIT` (a quote anomaly); net debit ≥ width → `NO_MAX_GAIN` (no
  expiration price makes money)

## Single-leg metrics

| Metric | Formula |
|---|---|
| Cost per contract | `ask × 100` |
| Cost per entry | `ask × 100 × contracts_per_entry` |
| Max loss | the cost per entry: the entire premium; a long option can expire worthless |
| Max gain | call: unlimited · put: `(strike − ask) × 100 × contracts` (if the stock fell to $0) |
| Breakeven | call: `strike + ask` · put: `strike − ask` |
| Breakeven move (`required_move_pct`) | `(breakeven − underlying) / underlying × 100`: the signed price move to breakeven (+ a rise, − a fall) |
| Move needed (`move_needed_pct`) | in the trade's direction: call `(breakeven − underlying) / underlying × 100`, put `(underlying − breakeven) / underlying × 100`; see [Required move](#required-move-and-past-breakeven) |
| Spread % | `(ask − bid) / mid × 100` |
| Account % | `cost per entry / total account value × 100` |

## Debit spread metrics

| Metric | Debit call spread | Debit put spread |
|---|---|---|
| Net debit (natural) | `long ask − short bid` | `long ask − short bid` |
| Cost / max loss | `net debit × 100 × contracts` | same |
| Width | `short strike − long strike` | `long strike − short strike` |
| Max gain | `(width − net debit) × 100 × contracts` | same |
| Breakeven | `long strike + net debit` | `long strike − net debit` |
| Risk/reward | `net debit : (width − net debit)`; reward per $1 risked = `(width − net debit) / net debit` | same |
| Spread % shown | the wider of the two legs | same |
| Breakeven move, move needed | as for a long call | as for a long put |

Show risk/reward explicitly on spreads. A spread costing $0.80 on a $1.00 width risks $80 to make $20:
the position can be right and still be a poor trade, and that only becomes visible when the ratio is
stated rather than left for the reader to compute.

### Required move and past breakeven

**Required move is the one to lead with.** It answers "how far does the stock have to travel before I
stop losing money", and on cheap out-of-the-money contracts it is routinely a double-digit percentage
within a few weeks. Everything else about a candidate can look clean while this number quietly makes
it implausible. Breakeven and required move are at the natural price.

Measure it in the trade's direction (a call needs a rise, a put a fall), not by the sign of the price
move. When the move needed is below zero the stock is already past breakeven, which is common for an
in-the-money debit spread: the position makes money at expiration if the stock doesn't move. Then:

- `past_breakeven` = true, `move_needed_pct` = 0
- `breakeven_cushion_pct` = the size of the negative move needed: how far the stock can move against
  the trade by expiration before the position loses money
- the card says "Breakeven already passed: the stock can fall 2.2% by expiration (32 days) before the
  position loses money" (rise, for puts) instead of "Required move". A call spread shown as
  "Required move −2.2%" would read as needing a fall.

Otherwise `move_needed_pct` is the size of `required_move_pct`, and the card shows the signed value:
"Required move +6.1% (a rise)" for a call, "Required move −6.6% (a fall)" for a put.

## Account gates

With `unit cost` = natural price × 100 (per contract, or per spread) and `entry cost` = unit cost ×
`contracts_per_entry`:

- unit cost > `max_cost_per_contract_usd` → `COST_PER_CONTRACT`
- entry cost / total value × 100 > `max_position_pct` → `ACCOUNT_PCT`
- buying power − entry cost < `reserve_cash_usd` → `RESERVE_CASH`
- open position rows + legs of the new entry (1 single, 2 spread) > `max_concurrent` →
  `MAX_CONCURRENT`. Open rows come from `get_option_positions` with `nonzero: true`, all pages.

## Earnings

Evaluated only for contracts that passed every other rule. Window =
`[as_of − earnings_buffer_days, expiration + earnings_buffer_days]`, both ends included. A report "hits"
when its date is inside the window.

| Policy | Rejected when |
|---|---|
| `avoid` | a verified hit (`EARNINGS_IN_WINDOW`) or an unverified hit, which counts as possible (`EARNINGS_UNVERIFIED_IN_WINDOW`) |
| `require` | no hit (`EARNINGS_NOT_IN_WINDOW`), or only unverified hits (`EARNINGS_REQUIRE_UNVERIFIED`) |
| `allow` | never; the card shows any hit |

No `get_earnings_results` read for the symbol → `EARNINGS_UNKNOWN` under `avoid` and `require` (unknown
is never clear); a card flag `EARNINGS_NOT_CHECKED` under `allow`. A read that lists no upcoming report
passes, flagged `EARNINGS_NONE_LISTED` under `avoid`. A `pm` report on the expiration date is inside
the window and is flagged `EARNINGS_AFTER_EXPIRY_CLOSE`: the contract stops trading before the report.

## Limit price by the entry rule

The limit is used for the simulated entry only; every check above uses the natural price.

- `natural`: the natural price (the ask; for a spread, long ask − short bid).
- `mid`, single leg: the mid rounded half-up to the contract's price increment from `min_ticks`
  (`below_tick` under `cutoff_price`, `above_tick` at or above it). No increment known → rounded to the
  cent and flagged `TICK_NOT_CHECKED`.
- `mid`, spread: long mid − short mid, rounded half-up to the cent and flagged
  `SPREAD_NET_TICK_NOT_CHECKED` (the review rejects a net price off the allowed increment).
- `ask_each_time`: no limit; ask the user. A mid that is not positive also means asking.

## Exit levels

Planned per contract from the user's `[options.exits]` percentages, on the planned entry price (the
limit by rule, or the natural price when there is no limit yet). After a fill they are recomputed from
the actual fill price.

| Level | Formula |
|---|---|
| Profit target price | `entry × (1 + profit_target_pct / 100)` |
| Stop price | `entry × (1 − stop_loss_pct / 100)`; at 100 or more it is $0.00 and flagged `STOP_AT_TOTAL_LOSS` (a bought option can lose only its premium) |
| Time stop date | `expiration − time_stop_dte` days (theta decay accelerates into expiry) |
| Hard exit date | `as_of + max_hold_days` days (expiration comes first if it is earlier) |

For a spread whose target price exceeds the width, flag `TARGET_ABOVE_MAX_VALUE`: the target needs the
spread worth more than it can ever be worth at expiration.

## Sort, status line and report-only

- `sort_by` (default `required_move_pct`): `required_move_pct` sorts by the move still needed in the
  trade's direction (`move_needed_pct`), smallest first; candidates already past breakeven (move
  needed 0) come first, the largest cushion first. Never by the size of the signed price move: that
  would rank a spread that can absorb a 2.2% drop behind one that still needs a 0.7% rise. `cost_usd`,
  `dte`, `spread_pct`, `account_pct` sort ascending. Ties: symbol, expiration, strikes. The sort is
  disclosed on every report; it is an ordering, not a recommendation.
- Line 1: `STOPPED: …` when the run stopped; `UNKNOWN: data incomplete (…)` while chains or required
  earnings reads are missing; `POSSIBLE: n candidates matched your criteria (nothing placed) · Dollars
  at stake: <three largest max losses>`; `UNKNOWN: 0 candidates; k of n contracts could not be checked
  (<codes>)` when nothing passed and some contracts (or underlyings) failed **only** for missing data
  (`DELTA_OI_UNAVAILABLE`, `QUOTE_UNAVAILABLE`, `EARNINGS_UNKNOWN`, `*_UNAVAILABLE`): those were
  excluded, not cleared; otherwise `NO ACTION: 0 of n contracts passed your criteria · Dollars at
  stake: none found`. When there are candidates and some contracts could not be checked, say so
  under the rejects.
- Report-only (no tickets) when the regular session is closed or unknown.

## Reason codes

| Code | Meaning |
|---|---|
| `CRITERIA_UNSET`, `CRITERIA_INVALID` | run stopped: config values unset or invalid |
| `NOT_FUNDED`, `ACCOUNT_VALUE_UNKNOWN`, `BUYING_POWER_UNKNOWN`, `OPEN_POSITIONS_UNKNOWN` | run stopped: capital or position data missing |
| `IV_RANK_SOURCE_UNVERIFIED`, `IV_RANK_SCALE_UNKNOWN`, `IV_RANK_SCALE_MISMATCH` | run stopped: IV rank set but not safely applicable |
| `BLOCKLISTED`, `NOT_IN_ALLOWLIST`, `PRICE_OUT_OF_RANGE`, `AVG_VOLUME`, `IV_RANK_OUT_OF_RANGE`, `*_UNAVAILABLE` | underlying rejected |
| `NOT_IN_SCAN`, `WRONG_TYPE` | contract not eligible for this pass |
| `DTE_OUT_OF_RANGE`, `INSIDE_TIME_STOP` | expiration outside your window |
| `DELTA_OI_UNAVAILABLE`, `DELTA_OUT_OF_RANGE`, `OPEN_INTEREST`, `SPREAD_PCT`, `QUOTE_UNAVAILABLE`, `NOT_TRADABLE`, `NON_STANDARD_MULTIPLIER` | contract data or liquidity |
| `NO_SHORT_LEG_IN_WIDTH`, `NON_POSITIVE_DEBIT`, `NO_MAX_GAIN`, `SHORT_LEG_*` | spread pairing |
| `COST_PER_CONTRACT`, `ACCOUNT_PCT`, `RESERVE_CASH`, `MAX_CONCURRENT` | capital and concentration |
| `EARNINGS_*` | earnings policy |

## Check numbers

Illustrative, from the kit's synthetic test fixture (not live data):

- AMD at $161.40, 165 call 2026-12-18 quoted 6.10 / 6.30, `as_of` 2026-11-16: DTE 32, cost $630.00,
  breakeven 165 + 6.30 = $171.30, required move (171.30 − 161.40) / 161.40 = +6.1%, spread %
  0.20 / 6.20 = 3.2%. With exits 50 / 40 / 7 / 30: target $9.45, stop $3.78, time stop 2026-12-11,
  hard exit 2026-12-16.
- 165/166 debit call spread, long ask 2.60, short bid 1.80: net debit $0.80, width $1.00, max loss
  $80.00, max gain $20.00, breakeven $165.80, $0.25 per $1 risked.
- 155/160 debit call spread, long ask 10.00, short bid 7.20: net debit $2.80, breakeven $157.80,
  below AMD's $161.40, so past breakeven: move needed 0, cushion (161.40 − 157.80) / 161.40 = 2.2%
  (the stock can fall 2.2% by expiration before the position loses). The 160/165 spread at
  7.40 − 4.90 = $2.50 breaks even at $162.50 and needs +0.7%; the 155/160 sorts first.
