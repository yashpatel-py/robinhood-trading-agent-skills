# Protection audit: procedure, field mapping and gap codes

Read this before the first protection audit of a session. `protection_audit.py` does the arithmetic;
this file says what to feed it, what its answers mean, and what to tell the user.

## Contents
- [Scope](#scope)
- [The reads](#the-reads)
- [Mapping responses to the script input](#mapping-responses-to-the-script-input)
- [Read statuses](#read-statuses)
- [Statuses](#statuses)
- [Gap codes](#gap-codes)
- [Reporting](#reporting)

## Scope

`get_accounts {}` first, fresh (R5): a list from earlier in the conversation can miss an account opened
since. Each entry gives `account_number`, `rhs_account_number`, `rhc_account_number` (the linked crypto
account; empty when there is none), `agentic_allowed`, `type`, `brokerage_account_type` and an optional
`nickname` (connector/FIELDS.md).

Read scope follows R3 in `references/connector-rules.md` and `[policy] read_scope` (load it with
`kitconfig.py get {"section": "policy", "cwd": "<the user's project directory>"}`):
- `"all"` → every account, interactive or scheduled, no question. `"agentic_only"` → the Agentic
  account, plus an account the user names in this request; the scope line lists the others as not read.
- `"ask"` → before the first read beyond the Agentic account in a session, ask once, and put the R18 notice in the
  question: *"This reads positions and open orders in ••••X4F1 (Agentic), ••••M7Q5 (Individual) and
  ••••P0Z9 (Roth IRA); that data goes to your AI provider. OK?"* A no leaves the others out of scope.
- Not set, in a conversation: a request about the user's positions in general ("which of my positions
  have no stop", "protect everything") is the consent to read every account, as in `robinhood-trading`:
  the job is household-wide, and a check that silently reads only the Agentic account hides the
  positions that most need an alert. Name the accounts and say their data goes to the AI provider (R18)
  as you start, and offer to narrow. A request that names one account reads only that account.
- A **scheduled pass** can't ask. With `"ask"`, it reads the Agentic account plus the accounts the
  scheduled prompt names; the rest are out of scope for that pass, reported as "not read (read_scope =
  ask)", never clear. With `read_scope` not set, the prompt is the request: the accounts it names, or
  every account when it names none. With `"agentic_only"`, the Agentic account only.
- Out-of-scope accounts go into the script's `accounts` list with `in_scope: false`, so the scope line
  names them as not read.

Why the whole household: agents can place orders only in the Agentic account, but the positions that
need a backstop most are often in the main account or an IRA. Native alerts are profile-wide, so an
alert is the one protection the agent can add for those holdings.

## The reads

Per account in scope (full `account_number` for the equity tools, that account's own
`rhs_account_number` for crypto; R4):

| Read | Call | Notes |
|---|---|---|
| Stock positions | `get_equity_positions {account_number, cursor}` | Every page (cursor from the `next` URL). |
| Crypto positions | `get_crypto_positions {rhs_account_number, cursor}` | Every in-scope account, not only the Agentic one (the tool takes any account); skip only where `rhc_account_number` is an empty string (no linked crypto account). Crypto units, never "shares". |
| OCOs | `get_advanced_orders {account_number, contingency_type: "oco", created_at_gte, cursor}` | No state filter exists; filter active ones yourself. The R26 error → `not_enabled`; do not retry. |
| Open stock orders | `get_equity_orders {account_number, created_at_gte, cursor}` | No `state` filter: it takes one value, and "open" is five. Newest first. |
| Open crypto orders | `get_crypto_orders {rhs_account_number, state_group: "open", cursor}` | The same accounts: a stop the user placed in the app is coverage, even where the agent can't trade. |
| Alerts | `get_alerts {cursor}` | Once for the profile, following `next_cursor` to the end. |
| Quotes | `get_equity_quotes {symbols}`, `get_crypto_quotes {symbols, rhs_account_number}` | 20 symbols per equity call keeps the official close. |
| Earnings (optional) | `get_earnings_results {symbol}` | One symbol per call; never the calendar (R14). |

`created_at_gte`: 95 days before now as a UTC date (`python3 scripts/rh_time.py add_days`, then
`to_utc`). The window is the 90-day equity GTC lifetime (unverified) plus 5 days. A GTC stop created
earlier than that is invisible here; the `HELD_SHARES_UNEXPLAINED` gap is how the audit notices one.

Call budget: about 6 calls per account (4 without a crypto account) plus pages, alerts, quotes and
earnings. Say the count before starting when it will exceed 40 (R23).

## Mapping responses to the script input

Every response is `{"data": …, "guide": "…"}`; read the guide and follow it where it doesn't conflict
with the kit's rules (R24). Field names below come from the 2026-09-22 capture (connector/FIELDS.md);
rows marked *not captured* are best effort, so map what the response actually shows.

| Script field | Source |
|---|---|
| `accounts[].account_last4` | last 4 of `account_number` (never the full number: the script refuses it) |
| `accounts[].agentic`, `.label` | `agentic_allowed`; label "Agentic" for that one, else `nickname` or `brokerage_account_type` ("Individual", "Roth IRA") |
| `accounts[].read.crypto_positions`, `.crypto_orders` | the crypto reads' status on **every** account; `not_applicable` only when `rhc_account_number` is an empty string. An omitted key counts as `not_read` |
| `positions[].quantity` | `quantity` |
| `positions[].sellable` | `shares_available_for_sells` (the guide: sellable is this, not `quantity`) |
| `positions[].held_for_orders`, `.held_for_collateral` | the per-position hold breakdown, when the response itemizes it: shares held for sell orders, and shares held as option collateral (for example a covered call). The capture names the breakdown but not its fields; omit what you can't map, and the script falls back to `quantity − sellable` |
| `positions[].price`, `.price_as_of` | the newer of `last_trade_price` (`venue_last_trade_time`) and `last_non_reg_trade_price` (`venue_last_non_reg_trade_time`); crypto: the mark and its time (*not captured*) |
| `open_orders[]` (stock) | `id` → `order_id`; `symbol`, `side`, `type`, `trigger`, `state`, `quantity`, `cumulative_quantity`, `stop_price`, `price` → `limit_price`, `created_at`; `time_in_force` if the response has it (it is not in the captured list; send null and the audit says "TIF not shown") |
| `open_orders[]` (crypto) | `type` (`stop_loss`, `stop_limit`, `limit`, `market`), `side`, `state`, `quantity`, `stop_price`; `time_in_force` if the response has it (*not captured*; send null and the audit says "TIF not shown", as for stock: the "omitted means gfd" rule is for an order being *sent*, not for a row that lacks the field) |
| `advanced_orders[]` | *not captured* (the tools were disabled on the capture accounts): `id_short` = first 3–6 characters of the advanced-order id; `active` = its state is still working; the stop leg's `stop_price` → `stop_loss_stop_price`; the take-profit leg's `price` → `take_profit_limit_price`; `quantity`, `side`, `time_in_force`; the legs' equity order ids → `leg_order_ids` |
| `alerts[]` | `alert_id`, `symbol`, `asset_class`, `condition_type`, `condition.target_price` → `threshold`, `enabled` |
| `earnings[SYMBOL]` | the upcoming report's `date`, `timing` (`am`, `pm` or null) and whether the company verified it (*not captured*) |
| `session` | the output of `rh_time.py session` (it supplies `in_regular_session` and `next_regular_open_et`) |

The stock order `type` may come back as `market` or `limit` with `trigger: "stop"` (a stop-market or
stop-limit); pass both fields and the script normalizes them. Pass open **and** closed rows if that is
easier: the script keeps only open states (`new`, `queued`, `confirmed`, `unconfirmed`,
`partially_filled`; crypto `queued`, `confirmed`, `partially_filled`) and sell-side orders.

## Read statuses

Per account, `read` has one value for each of `positions`, `equity_orders`, `advanced_orders`,
`crypto_positions` and `crypto_orders`:

| Value | Meaning |
|---|---|
| `complete` | every page was read |
| `partial` | stopped early (rate limit, page cap, a later page failed) |
| `failed` | the call errored |
| `not_enabled` | `advanced_orders` only: the exact R26 error |
| `not_read` | skipped (also what an omitted key means) |
| `not_applicable` | nothing to read: the crypto reads of an account whose `rhc_account_number` is an empty string. Never for a read that errored (`failed`) or was skipped (`not_read`) |

`alerts_read` takes `complete`, `partial`, `failed` or `not_read`. Be honest here: a position whose
exits weren't fully read comes back `unknown`, which is the point. A check that reports "unprotected"
or "clear" on half the data is worse than no check.

## Statuses

| Status | Means | Say |
|---|---|---|
| `protected` | every share is under a working stop or OCO | which exits, and their gaps (GFD, gap risk) |
| `partial` | some shares are covered | how many shares and dollars are not |
| `backstop_only` | no exit order, but an enabled price-below (or price-crossing) alert | the alert notifies; nothing sells |
| `unprotected` | no exit order and no enabled alert | the rungs this position can use (`can_add`) |
| `unknown` | exits weren't fully read and don't already cover everything | what couldn't be read; never guess |

Coverage counts only open stop, stop-limit and OCO sell exits, walked in the order a falling price
reaches them; an exit bigger than the shares still free at that point counts for nothing, and shares
pledged as option collateral are never free (`references/formulas.md`). Limit and market sells are listed as "not counted": they aren't downside
protection.

## Gap codes

| Code | What it means | What to offer |
|---|---|---|
| `UNCOVERED_SHARES` | part of a position has no working exit | cover the rest, or an alert |
| `EXITS_EXCEED_POSITION` | sell exits add up to more shares than are held; the extra can't execute | ask which exit the user wants to keep; don't stack another |
| `GFD_EXPIRES_TODAY` | a good-for-day exit lapses at the close | re-arm tomorrow, or a GTC exit |
| `TIF_UNKNOWN` | the order data didn't show its time in force (stock or crypto) | check it in the app; if it is gfd it ends today |
| `GTC_EXPIRING_SOON` | a GTC stop is near the 90-day lifetime (unverified) | re-arm before it lapses |
| `FRACTIONAL_REMAINDER` | fractional shares can't sit under a stop or OCO | an alert is the only backstop |
| `COLLATERAL_SHARES` | shares pledged to a short option (a covered call) can't be sold by a stop | an alert; the call itself is `robinhood-options-monitor`'s job |
| `STOP_MARKET_GAP_RISK` | a triggered stop sells at market; a gap fills lower | say it; a stop-limit trades this for non-fill risk |
| `STOP_LIMIT_MAY_NOT_FILL` | a stop-limit won't sell below its limit | say it |
| `NO_EXTENDED_HOURS_COVERAGE` | stock stops and OCOs act only 09:30–16:00 ET (crypto has no session gap) | an alert covers the other hours (it notifies only) |
| `CRYPTO_STOP_DAY_ONLY` | a crypto stop with time in force gfd ends today | a replacement preview with the user's time in force |
| `CRYPTO_GTC_90D` | a GTC crypto stop lasts 90 days | note the date |
| `EARNINGS_BEFORE_NEXT_SESSION` | a report lands before the next regular open (unverified dates count) | say the date; the decision is the user's |
| `NOT_AGENT_TRADABLE` | the agent can't place or simulate orders in this account (stock or crypto) | an alert, or a stop the user enters in the app |
| `ALERT_DISABLED` | a price-below alert exists but is off | `update_alert {alert_id, enabled: true}` after a yes |
| `ALERT_NOTIFIES_ONLY` | the only protection is an alert | say it notifies and does not sell |
| `OCO_UNREADABLE` | the OCO tools are not enabled for this account (R26) | a stop order (Agentic) or an alert |
| `READ_INCOMPLETE` | orders, OCOs or alerts weren't fully read | re-run the reads; the status may be wrong |
| `HELD_SHARES_UNEXPLAINED` | shares are reserved by sell orders the audit didn't see | check the app (an OCO, or an order older than the lookback) |
| `STALE_QUOTE` | no quote, no quote time, or an old one | re-quote before any ticket |

## Reporting

- Line 1 is the script's `first_line`: `<STATUS>: Dollars at stake: …`, with the three largest
  unprotected positions in dollars. `ACTION NEEDED` when anything is unprotected, alert-only or partial;
  `UNKNOWN` when the rest couldn't be read; `CLEAR` only when every position read is fully covered and
  every read was complete; `NO ACTION` when there is nothing to protect.
- Line 2: `PROTECTION CHECK · <as-of time ET> (<market open or closed>)`. Then the `scope_line`, the
  `table`, a "Proposed (nothing created yet)" list, and the `footer`, word for word.
- Proposals use `can_add`: `oco` (only where the OCO tools answered), `stop_order`, `crypto_stop_order`,
  `alert`. Outside the Agentic account (stock or crypto) that is `alert` alone. Levels are placeholders
  ("at your level") until the user gives them.
- Show `not_counted` exits, `orphan_exits` and `alert_cleanup` as cleanup proposals (workflow D).
- Round money to cents; crypto quantities are coin units. Never print a full account number.
