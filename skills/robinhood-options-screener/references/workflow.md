# Screener workflow: one pass, step by step

*Unofficial — not affiliated with Robinhood.* Tools verified against the live connector on 2026-09-22.

A pass finds option candidates that match the user's saved criteria, analyzes them, rejects the rest
with reasons, and produces review-ready specs. It automates everything except the decision to place;
the user keeps that. Every threshold comes from the user's config, and an unset value is a stop
condition, not a prompt to improvise.

Each invocation is one **stateless** pass: nothing is remembered between runs, and every run re-reads
the account, rebuilds the scan and re-quotes. Scheduling is the agent surface's job (see
[Scheduled runs](#scheduled-runs)).

## Contents
- [Stage 0: config](#stage-0-config)
- [Stage 1: preflight (hard stops)](#stage-1-preflight-hard-stops)
- [Stage 2: underlying universe](#stage-2-underlying-universe)
- [Stage 3: contracts](#stage-3-contracts)
- [Stage 4: screen, gates and analysis](#stage-4-screen-gates-and-analysis)
- [Stage 5: earnings](#stage-5-earnings)
- [Stage 6: present](#stage-6-present)
- [Stage 7: simulate a picked candidate](#stage-7-simulate-a-picked-candidate)
- [Scheduled runs](#scheduled-runs)
- [Call budget](#call-budget)
- [Field mapping](#field-mapping)
- [What moved out of this workflow](#what-moved-out-of-this-workflow)

## Stage 0: config

1. `kitconfig.py validate` with
   `{"sections": ["options.criteria", "options.entry", "options.exits"], "cwd": "<the user's project
   directory>"}`. Add `"path"` if the user named a file, or `"pasted"` with the project text when the
   config was pasted. `PROJECT_DIR_UNKNOWN` means `cwd` was missing: pass it and run again.
2. `ok: false` with `CONFIG_NOT_FOUND` → line 1 `STOPPED: no screener config found`, list the missing
   keys it returns, and offer the setup interview (`setup-interview.md`). A parse error → `STOPPED`
   with the line number and message; never guess what the user meant.
3. `valid: false` → `STOPPED: <n> criteria UNSET: <keys>` from `missing`, plus every entry in
   `errors` (for example `MIN_GT_MAX`). Stop. Do not run a partial pass with the settings that do
   parse: a screen that silently skips a rule surfaces trades the user excluded.
4. `valid: true` → `kitconfig.py get` for each of the three sections; the `section` objects are the
   `criteria`, `entry` and `exits` inputs of `options_screen.py screen`, passed through unchanged.
   Also `get` `[policy]` (for Stage 7) and `[accounts]` (its `retirement` list) if present; unset
   policy keys mean "not configured".

All four exits must be set before any entry is proposed. An entry without a predefined exit is an
open-ended bet.

## Stage 1: preflight (hard stops)

Each of these is a hard stop. Failing one means reporting the failure, not degrading to a partial
run: a scan that silently skips its capital check will happily surface a trade the account cannot
afford.

1. **`get_accounts {}`, fresh.** Re-read it every pass rather than trusting a cached level: the level
   changes the moment an application is approved. Use the one entry with `agentic_allowed: true`
   (the screener simulates only there). Keep its full `account_number` for tool calls and show it
   only as `••••` + last 4.
2. **Derive the preflight account block** from that entry:
   - `type`: `cash`, `margin` or `limited_margin`.
   - `option_level`: as returned (`option_level_2`, `option_level_3`); empty or `option_level_0`
     means no options access.
   - `retirement`: `true` if its last 4 is in the user's `[accounts].retirement` list, or its
     `brokerage_account_type` names an IRA, Roth or other retirement type; `false` for `individual`;
     otherwise `null` (unknown). IRA values of `brokerage_account_type` were not observed in the
     2026-09-22 capture, so an unrecognized value stays unknown; a spread run then stops until the
     user confirms.
3. **`get_portfolio {account_number}`** → `total_value` and `buying_power.buying_power` (decimal
   strings). `get_accounts` does not return reliable buying power.
4. **`python3 scripts/options_screen.py preflight`** with
   `{"structure", "account": {"type", "option_level", "retirement", "agentic_allowed": true},
   "funding": {"buying_power_usd", "max_cost_per_contract_usd", "contracts_per_entry",
   "reserve_cash_usd"}}`.
   - Long calls and puts need `option_level_2` or higher on any account type.
   - Debit spreads need `option_level_3` on a margin or limited-margin account that is not a
     retirement account.
   - A cash account wanting spreads goes through `get_limited_margin_upgrade_info` first, then the
     user completes it, then `get_accounts` is re-read, and only then `get_option_level_upgrade_info`
     if the level is still short. Never call the level upgrade for a level-3 request while the
     account is cash, and never when the level is already enough.
   - Funding: buying power must cover `max_cost_per_contract_usd × contracts_per_entry +
     reserve_cash_usd`. A zero-balance Agentic account is the most common blocker and the easiest to
     miss, because every other tool still returns data.
   - `allowed: false` → `STOPPED: <stop.msg>`, show `checks` and `route`. In an interactive run,
     offer the first route tool (the enrollment tools only return links; pass the full account
     number). In a scheduled run, report and stop.
5. **Session: `python3 scripts/rh_time.py session`** with `{"now": <user-stated time>, "now_source":
   "user_stated"}` when the user gave a time; otherwise `{}` (system clock), cross-checked against
   quote timestamps. Pass `in_regular_session` to `screen`. Outside the regular session, on a closed
   day, or when the session is unknown, the pass reports but `ticket_allowed` is false: option
   quotes outside regular hours are wide and often stale. `get_index_quotes` `state` came back empty
   in the capture; never use it as a session signal.
6. **`get_option_positions {account_number, nonzero: true}`**, following `cursor` from `next` to the
   last page. `open_option_positions` = the number of rows. Without `nonzero: true` the tool also
   returns closed zero-quantity rows, which inflate the count and wrongly reject every candidate. If
   any row carries non-zero `pending_*` quantities, say so next to the count: pending orders are not
   counted.
7. Cash account: add the settlement note to the report (option trades settle T+1; buying again with
   unsettled proceeds risks a good-faith violation; 5 in 12 months trigger a 90-day restriction).

## Stage 2: underlying universe

1. **`get_scanner_filter_specs {}`** before building anything. Filter names are wire enums and cannot
   be guessed; read each filter's predicates, `supported_lengths`, `supported_intervals` and units.
   - Price band: the enum filter for share price, with `price_min` and `price_max` (a BETWEEN
     predicate if the spec offers one, otherwise two filters).
   - Average volume: the enum filter for average daily volume, `≥ min_avg_volume`. If the filter
     requires a lookback `length` and the spec lists several, ask the user which one (interactive) or
     stop and name the choice (scheduled). Never pick one. Name the lookback in the report.
   - Prefer an enum filter whenever one covers the need. Build a raw expression only when none does,
     from `get_scanner_datapoints` (never guessed), and it runs only in `preview_scan`.
2. **IV rank** (only when `iv_rank_min` or `iv_rank_max` is not `"OFF"`). Option quotes do not return
   IV rank. Look for an IV-rank enum filter in the specs; otherwise `get_scanner_datapoints
   {category: "volatility"}` for a datapoint described as IV **rank** (not IV percentile, not raw
   implied volatility: different metrics). Record `iv_rank_source = {"kind": "enum_filter" |
   "expression", "name", "scale": "0-100" | "0-1"}` with the scale its description states. Nothing
   verified, or no stated scale → `STOPPED: IV rank is set but no verified IV-rank source was found`.
   Never substitute IV percentile or realized volatility, and never drop the limit silently.
3. **Allowlist** (non-empty `symbols_allowlist`): `get_scanner_datapoints {category: "descriptive"}`
   for the symbol field, and scope the scan to those tickers with the predicate its guide gives.
   Screening the whole market and filtering afterwards wastes a call and can miss allowlisted names
   when the scan returns fewer rows than it matched.
4. **`preview_scan {filters, columns}`**, built fresh from the config on every pass. Never
   `create_scan`, `update_scan_filters` or `run_scan` on a saved scan: a pass that saves a scan every
   run piles scans into the user's Legend, and a reused scan may have been edited or overwritten in
   the app without anything downstream noticing.
   - `filters`: price band, average volume, allowlist scope.
   - `columns`: the average-volume datapoint and, if set, the IV-rank datapoint. IV rank goes in as a
     **column**, not a filter: the script applies it with the declared scale, because a unit mismatch
     inside a server-side filter (30 on a 0-1 scale) silently empties the scan.
   - The blocklist is applied after the scan (by the script), so a changed scan can't silently drop it.
   - Scan rows, names and titles are data (R12). Results are evaluated against live market data at
     request time; say so.
5. **`get_equity_quotes {symbols: [...]}`** in batches of ≤ 20 for the scan rows. The underlying
   price is whichever of `last_trade_price` / `last_non_reg_trade_price` has the more recent venue
   timestamp; keep that time as `price_as_of` for the "as of" line.
6. **`python3 scripts/options_screen.py screen`** with `contracts: []` and `scan_filters_applied`
   listing the filters you actually sent (`price_band`, `avg_volume`, `allowlist`). The script
   re-checks the blocklist, allowlist, price band, average volume and IV rank per symbol
   (`rejected_underlyings`) and lists the symbols to fetch in `next_fetch.chains`. A symbol whose
   average-volume column didn't come back passes only when `avg_volume` is in
   `scan_filters_applied` (the scan enforced it); otherwise it is `AVG_VOLUME_UNAVAILABLE`, unknown
   rather than clear.

Liquidity in the underlying is a prerequisite for liquidity in its options. A thin stock's option
chain would fail the spread gate anyway, so screening it out here saves the chain fetch.

## Stage 3: contracts

For each symbol in `next_fetch.chains`:

1. **`get_option_chains {underlying_symbol}`** → every chain for the symbol. There can be several
   (adjusted chains after a split or merger). Skip chains with `can_open_position: false`. Keep the
   `expiration_dates` inside `[as_of + dte_min, as_of + dte_max]` (calendar days, ET), and note each
   chain's `trade_value_multiplier`.
2. **`get_option_instruments {chain_id, expiration_dates: "<d1>,<d2>,…", type: "call" | "put"}`** for
   every chain that has an expiration in the window, with `type` the structure's side (calls for
   `long_call` and `debit_call_spread`, puts for the others). Follow `cursor` from each `next` URL
   until there is none. Filter expirations before pulling instruments, not after: the chain fetch is
   the expensive part of a pass.
3. **`get_option_quotes {instrument_ids: [...]}`** in batches of ≤ 20 (above 20 the official closes are
   dropped). Option tools take instrument UUIDs only, never OCC symbols or tickers.
4. Build one `contracts` entry per instrument (see [Field mapping](#field-mapping)) and add the symbol
   to `fetched_chains` only after every chain and page for it was read. A symbol whose chains were
   read but have nothing in the window still goes in `fetched_chains`.

## Stage 4: screen, gates and analysis

Run `screen` with everything so far. For each contract (and, for spreads, each long/short pair) it
checks, in `references/formulas.md` terms:

- DTE within `dte_min`..`dte_max`, and above `time_stop_dte` (a contract already inside the time stop
  would fire it on entry);
- `|delta|` within `delta_min`..`delta_max` on the leg bought; delta and open interest must be present
  on every leg that needs them (`DELTA_OI_UNAVAILABLE` otherwise);
- `open_interest ≥ min_open_interest` and spread % `≤ max_spread_pct` on **every** leg. A clean long
  leg paired with an illiquid short leg is an unexitable position: you can be forced to leg out at a
  bad price precisely when you most want out;
- for spreads: the short strike further out of the money, width within `spread_width_min`..`max`,
  net debit = long ask − short bid (positive and below the width);
- cost per contract at the natural price `≤ max_cost_per_contract_usd`; cost per entry as a share of
  account value `≤ max_position_pct`; buying power after the entry `≥ reserve_cash_usd`; open
  positions + the new legs `≤ max_concurrent`.

Each reject lists every rule it failed; `counts_by_reason` totals them. Report what was rejected and
why, not just what survived. A pass that returns two candidates out of forty is telling you something
about the criteria, and that signal is lost if the rejects are dropped silently.

If nothing survives, say so plainly. Zero candidates is a valid, common and correct outcome. Never
loosen a threshold to produce results; loosening criteria to fill a report converts the user's risk
limits into decoration.

## Stage 5: earnings

When `next_fetch.earnings` is non-empty (policy `avoid` or `require`), call
**`get_earnings_results {symbol}`** once per listed symbol (exact ticker; one per call), then re-run
`screen`. Use the per-symbol tool, not `get_earnings_calendar`: the calendar is for market-wide
discovery, is limited to a 31-day window and, with its `high_market_cap` filter, hides names under
$1B.

Pass `earnings: {SYM: {"reports": [{"date", "timing": "am"|"pm"|null, "verified": true|false}]}}`
with the upcoming report and the most recent past one (it matters inside the buffer). The tool
returns the report date, am/pm timing and a company-verification status; those response field names
were not in the 2026-09-22 capture, so read them from the response and its `guide`, and treat a
missing verification status as unverified. No upcoming report listed → `{"reports": []}`; the card
then says "none listed". A symbol absent from `earnings` is **unknown**, and unknown is never clear:
the script rejects it with `EARNINGS_UNKNOWN`.

The window is `[as_of − earnings_buffer_days, expiration + earnings_buffer_days]`. Under `avoid`, an
unverified date inside it counts as a possible hit. Earnings inside the holding window is the single
largest source of overnight gap risk, in both directions. Under `allow`, fetching earnings for the
candidates' symbols (`earnings_needed_for_cards`) is optional and only fills in the card.

## Stage 6: present

Use the SKILL.md templates. The order:

1. `status_line` (line 1).
2. One sentence of context: structure, as-of time, report-only reason if any, settlement note if any.
3. Candidate cards for the first five in the disclosed sort order, then one table row per remaining
   candidate. Lead each card with the required move: the script's `move_text`, which says
   "Breakeven already passed" with the cushion instead when `past_breakeven` is true.
4. The rejects table from `counts_by_reason`, then `rejected_underlyings`.
5. The sort disclosure, the oldest quote time, the average-volume lookback used, and "Evidence, not a
   recommendation. Every threshold above is yours."
6. If `ticket_allowed`: "Pick one and I'll simulate it with Robinhood's review tool." Nothing is
   reviewed until the user picks.

## Stage 7: simulate a picked candidate

Interactive only, only when `ticket_allowed` is true, and one candidate at a time.

1. **Re-quote:** `get_option_quotes {instrument_ids: [its legs]}` and re-run `screen` with the same
   input, its contracts replaced by the fresh quotes. If it now fails a rule, say which one and stop.
2. **Price:** `limit_price_by_rule` comes from the user's saved `entry_price_rule`, recomputed from the
   fresh quotes; that is the only source of an option price besides the user. `needs_user_price:
   true` (`ask_each_time`, or no positive mid) → ask for the limit (per contract; a spread's net debit
   per spread), and set the lint provenance for `price` to `"user"`.
3. **Lint:** `python3 scripts/order_lint.py lint` with `{"tool": "review_option_order", "params":
   <order_params + "account_number">, "provenance": <lint_provenance>, "context": {"agentic_allowed":
   true, "option_level", "account_type", "retirement", "session": <rh_time session>,
   "underlying_type", "multiplier": "100", "ask": <single leg's ask>}}`. Any error → show it, no review.
4. **Policy:** if the user has a `[policy]` section, `python3 scripts/policy_check.py run` with the
   order, `estimate_usd` = the candidate's `cost_usd`, the earnings dates and the session. A violation
   is shown and blocks the review until the user changes the order or their own policy.
5. **Review:** `review_option_order {account_number, legs, quantity, type: "limit", price, direction:
   "debit" (spreads only), time_in_force: "gfd", market_hours: "regular_hours", chain_symbol,
   underlying_type}`. `type` is always "limit": the tool accepts only limit orders with 2 or more legs,
   and for a single leg the limit comes from the user's `entry_price_rule` (or the price they gave), so
   a market order would bypass the price they chose. Sending `chain_symbol` and `underlying_type` makes
   the review return fees and collateral. Show every pre-trade check verbatim in whatever shape it returns, plus any disclosure
   verbatim.
6. **Hand off** with connector-rules R21 (a). Never call `place_option_order`; in a session whose
   order-mode line says `CONFIRM MODE: ON`, the user acts through `robinhood-trading` instead.

## Scheduled runs

A scheduled pass is the same pass, unattended:

- Line 1 is the status line, so a notification shows something useful. If no Robinhood tool is
  visible: `CONNECTOR UNAVAILABLE: no Robinhood tools in this session`, and stop.
- It never runs Stage 7, never places, never writes config, and never creates scans or alerts.
- An IV-rank source, an average-volume lookback, or a retirement status it cannot settle stops the
  pass with a `STOPPED` line naming what the user must decide.
- Setting it up, per surface: Claude Code `/loop <interval> /robinhood-trading:robinhood-options-screener`
  (or a routine or scheduled task), claude.ai or ChatGPT scheduled tasks with the prompt "Run my
  options screener". The interval and time are the user's choice; a pass that runs outside the
  regular session marks every candidate report-only, and the user can re-run it interactively
  during the session to simulate one.

## Call budget

Planned calls ≈ 5 (accounts, portfolio, positions, filter specs, scan) + 0–2 datapoint reads +
⌈symbols ÷ 20⌉ equity quotes + per symbol: 1 chain read + instrument pages per chain + ⌈contracts ÷ 20⌉
quote batches + earnings reads for survivors. When that exceeds 40, state the number before Stage 3
(R23) and pace at about 4 calls a second (the first `RATE_LIMITED` was observed at 8/s). A
rate-limited pass returns partial data that looks complete: if a call fails, the symbol stays out of
`fetched_chains` and the pass reports `UNKNOWN`, never a clean result.

## Field mapping

From the 2026-09-22 capture (`connector/FIELDS.md`; every response is `{"data": …, "guide": …}`; read
the `guide`, which ranks below the kit's rules):

| Script input | Source |
|---|---|
| `account.total_value_usd` | `get_portfolio` `total_value` |
| `account.buying_power_usd` | `get_portfolio` `buying_power.buying_power` |
| `account.open_option_positions` | row count of `get_option_positions` with `nonzero: true`, all pages |
| `account.type` | `get_accounts` `type` |
| `underlyings.SYM.price` | `get_equity_quotes` `quote.last_trade_price` or `quote.last_non_reg_trade_price`, the more recent |
| `underlyings.SYM.avg_volume`, `.iv_rank` | the `preview_scan` columns you requested |
| `scan_filters_applied` | the filters in your `preview_scan` call: `price_band`, `avg_volume`, `allowlist` |
| `contracts[].option_id` | `get_option_instruments` `id` |
| `.symbol`, `.chain_symbol`, `.underlying_type` | the scanned ticker; instrument `chain_symbol`, `underlying_type` |
| `.type`, `.strike`, `.expiration` | instrument `type`, `strike_price`, `expiration_date` |
| `.state`, `.tradability`, `.multiplier`, `.min_ticks` | instrument `state`, `tradability`, `trade_value_multiplier`, `min_ticks` |
| `.can_open_position` | the chain's `can_open_position` |
| `.bid`, `.ask`, `.delta`, `.open_interest`, `.updated_at` | `get_option_quotes` `quote.bid_price`, `ask_price`, `delta`, `open_interest`, `updated_at` |

Copy values as strings exactly as returned; the script does the math in decimals. A field the tool
did not return stays out of the input: the script decides what a missing value means (for delta and
open interest, a reject).

## What moved out of this workflow

- **Position monitoring** (exit rules on open positions, expiration, assignment) moved to
  `robinhood-options-monitor`, which runs on its own schedule: an open position needs watching more
  than a hypothetical one needs finding.
- **Day-trade (PDT) accounting** is deleted. FINRA eliminated the pattern-day-trader rule on
  2026-06-04 and Robinhood implemented it the same day. If a review returns a day-trading check
  anyway, show it verbatim; it is authoritative.
- **`get_market_hours`** never existed on the connector. Sessions come from `rh_time.py`.
