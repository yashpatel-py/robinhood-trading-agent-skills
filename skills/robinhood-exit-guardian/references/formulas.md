# Formulas: the prose twin of `scripts/protection_audit.py`

Use this only when the script can't run on this surface, and label every figure "computed by hand".
Money is rounded half-up to cents; percentages to one decimal.

## Contents
- [Audit: which exits count](#audit-which-exits-count)
- [Audit: status](#audit-status)
- [Audit: gap codes](#audit-gap-codes)
- [Audit: dollars at stake and the first line](#audit-dollars-at-stake-and-the-first-line)
- [Levels](#levels)

## Audit: which exits count

1. Keep **sell** orders in an open state. Stocks: `new`, `queued`, `confirmed`, `unconfirmed`,
   `partially_filled`. Crypto: `queued`, `confirmed`, `partially_filled`. Everything else (`filled`,
   `cancelled`/`canceled`, `rejected`, `failed`, `voided`) is ignored, as are buy orders.
2. An order's working quantity is `quantity − cumulative_quantity`.
3. OCOs count only when active and sell-side. If an OCO's legs also appear as equity orders (same id, or
   the same shares with the stop price or take-profit price), count the shares once, as the OCO.
4. **Protective exits** are stop-market orders (`type: market` with `trigger: stop`, or crypto
   `stop_loss`), stop-limit orders and OCOs. Limit and market sells are listed but never count as
   downside protection.
5. Walk the protective exits in the order a falling price reaches them: highest stop price first (an
   OCO's stop leg is its trigger), and on a tie the smaller exit first. Start with the shares held minus
   any shares pledged as option collateral (a stop can't sell those). Each exit counts only if its
   quantity is at most the shares still left; if it counts, subtract it. An exit bigger than what is left
   is assumed to be rejected and counts for nothing.
6. `covered` = the sum of the exits that counted; `uncovered` = shares held − covered.

Worked example (build spec B.3): AMD 12.5 sh; OCO 10 sh with stop 142; stop order 5 sh at 140. The OCO
triggers first (142 > 140): 10 ≤ 12.5, so it counts; 2.5 left. The 5 sh stop is bigger than 2.5, so it
doesn't count. Covered 10, uncovered 2.5, partial.

## Audit: status

Checked in this order:
1. `protected` if covered = shares held.
2. `unknown` if the account's open orders weren't fully read (or, for stocks, the OCO read failed, was
   partial or was skipped).
3. `partial` if covered > 0.
4. `backstop_only` if there is an enabled `price_below` or `price_crosses` alert on the symbol (same
   asset class; alerts cover every account).
5. `unprotected` otherwise.

The OCO tools answering "not enabled" (R26) does not make a position unknown; it adds `OCO_UNREADABLE`
and the status counts stops and alerts only.

## Audit: gap codes

| Code | Fires when |
|---|---|
| `UNCOVERED_SHARES` | status `partial` |
| `EXITS_EXCEED_POSITION` | all open sell exits (protective, limit and market) add up to more than the shares held minus option collateral |
| `GFD_EXPIRES_TODAY` | a stock stop or OCO has time in force `gfd` |
| `TIF_UNKNOWN` | a stop or OCO (stock or crypto) has no time in force in the data |
| `GTC_EXPIRING_SOON` | a stock GTC exit's creation date + 90 days (unverified lifetime) is within 7 days of today (ET) |
| `FRACTIONAL_REMAINDER` | the shares free of collateral have a fractional part (it is always uncovered) |
| `COLLATERAL_SHARES` | some shares are held as collateral for a short option |
| `STOP_MARKET_GAP_RISK` | any stop-market exit or OCO is on the position |
| `STOP_LIMIT_MAY_NOT_FILL` | any stop-limit exit is on the position |
| `NO_EXTENDED_HOURS_COVERAGE` | a stock has any stop or OCO (they act 09:30–16:00 ET only) |
| `CRYPTO_STOP_DAY_ONLY` | a crypto stop has time in force `gfd` (a missing one is `TIF_UNKNOWN`: "omitted means gfd" applies to an order being sent) |
| `CRYPTO_GTC_90D` | a crypto stop has time in force `gtc` |
| `EARNINGS_BEFORE_NEXT_SESSION` | a report falls after the last regular open and at or before the next one: `am` = 08:00 ET on the date, `pm` = 16:00 ET, unknown timing spans 08:00–16:00. Unverified dates count. With no holiday calendar, the last open is the latest weekday 09:30 ET at or before now |
| `NOT_AGENT_TRADABLE` | the account isn't the Agentic one |
| `ALERT_DISABLED` | a `price_below` or `price_crosses` alert on the symbol is disabled |
| `ALERT_NOTIFIES_ONLY` | status `backstop_only` |
| `OCO_UNREADABLE` | a stock position isn't fully covered and the account's OCO tools are not enabled |
| `READ_INCOMPLETE` | a position isn't fully covered and its orders, OCOs or the alerts weren't fully read |
| `HELD_SHARES_UNEXPLAINED` | shares held for sell orders (the hold breakdown, or else `quantity − sellable − collateral`) exceed all open sell exits read |
| `STALE_QUOTE` | no price; or no quote time; or, in the regular session (and always for crypto), a quote older than 15 minutes; outside it, a stock quote older than the last regular open |

## Audit: dollars at stake and the first line

- Value = shares × price. Unprotected dollars = the sum over `unprotected` positions; alert-only
  dollars over `backstop_only`; partial dollars = uncovered shares × price over `partial`.
- First line, first match wins:
  - any unprotected, alert-only or partial position → `ACTION NEEDED: Dollars at stake: $X unprotected
    (three largest, by value) · $Y alert-only (…) · $Z uncovered in partial positions (…)`, leaving out
    empty parts, then the count of unknown and unpriced positions and any account whose holdings weren't
    fully read. A symbol held in two accounts is named with the account (`TSLA ••••M7Q5`).
  - any unknown position → `UNKNOWN: Dollars at stake: $V in positions whose exits could not be fully
    read (…)`.
  - holdings not confirmed fully read (no accounts list, or any in-scope account, Agentic or not, whose
    stock positions or crypto positions were partial, failed or not read) → `UNKNOWN: Dollars at stake:
    none found …`, because a position that wasn't read can't be called protected. (`not_applicable`
    crypto means the account has no linked crypto account; it is not a gap.)
  - no positions → `NO ACTION: Dollars at stake: none found (no stock or crypto positions in the
    accounts read)`.
  - otherwise `CLEAR: Dollars at stake: none found; every position read has a working stop or OCO`,
    plus the number of gaps to review.
- Rows sort by status (unprotected, unknown, alert-only, partial, protected), then value, largest first.

## Levels

Round each price half-up to cents (to 4 decimals below $1). `price` is the current price.

| Rule | Stop | Target |
|---|---|---|
| `pct:n` | price × (1 − n/100) | price × (1 + n/100) |
| `atr:m:p` | price − m × ATR(p), daily bars, fetched with the rule's own period p | not a target rule |
| `r:k` | not a stop rule | price + k × (price − stop), using the rounded stop |
| `none` | not a stop rule | no target |
| `UNSET` or `ask` | ask the user | ask the user |

- A per-symbol level the user set (`[exits.equity.symbols.<SYM>]`, or their message) replaces the rule
  for that field.
- No `target_rule` at all means no target was requested: none is computed and none is asked for.
- `r:k` with no stop yet asks for both.
- An ATR value must come with its period (`{"value", "period"}`); a bare number counts as ATR(14), so it
  is refused for any other p, and a period that differs from the rule's is an error. Never stand ATR(14)
  in for ATR(p).
- Checks: stop ≥ price, target ≤ price, or target ≤ stop is an error. A level less than 0.25% from the
  price, or a stop and target less than $0.10 apart, is a warning that an OCO would be rejected.

Worked example (build spec B.3): AMD at $161.40, `pct:8` / `r:2`. Stop = 161.40 × 0.92 = 148.488 →
**$148.49**. Target = 161.40 + 2 × (161.40 − 148.49) = 161.40 + 25.82 = **$187.22**. With `atr:2.0:14`
and ATR(14) $9.80 on NVDA at $228.10: stop = 228.10 − 19.60 = **$208.50**.
