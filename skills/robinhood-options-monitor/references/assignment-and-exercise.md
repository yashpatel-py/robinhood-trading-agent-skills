# Expiration, assignment and exercise

*Unofficial — not affiliated with Robinhood.*

Read this before running the radar (`scripts/expiry_risk.py`) and before answering any question
about exercising, being assigned or what happens at expiration. The formulas are in `formulas.md`.

## Why this is its own check

The surprises that cost real money happen at the edges of a position's life, and the connector hands
you the pieces without the arithmetic:
- A long call that finishes a cent in the money can become a share purchase the account can't fund.
- A short call in the money the day before an ex-dividend date can be assigned early, and the
  dividend goes to whoever exercised.
- A long put exercised without the shares creates a short stock position.
- A contract closing right at its strike leaves the outcome unknown until after the close.
- Robinhood can close a position itself before expiration.

## Inputs (tool by tool)

| Radar input | Tool and fields |
|---|---|
| positions | `get_option_positions {account_number, nonzero: true, cursor}` + `get_option_instruments {ids, cursor}` (strike, call/put, `underlying_type`, `sellout_datetime`) + `get_option_quotes` (`bid_price`, `ask_price`) |
| `underlying` | `get_equity_quotes {symbols: [≤20]}` (the newer of `last_trade_price` / `last_non_reg_trade_price`). Index: `get_indexes {symbols: "SPX,NDX"}` (a comma-separated **string**) → `get_index_quotes {instrument_ids: [id]}` → `value`. For SPXW and other index chains, the underlying is the index in the chain's `underlying_instruments` (pass it as `underlying_symbol`) |
| `buying_power` | `get_portfolio {account_number}` → `buying_power.buying_power` |
| `shares_held` | `get_equity_positions {account_number, cursor}` → `quantity` (total held, not `shares_available_for_sells`: shares pledged to a covered call are the ones that get delivered) |
| `ex_dividends` | `get_equity_fundamentals {symbols: [≤10]}` → `ex_dividend_date`, `dividend_per_share`, `distribution_frequency`, `dividend_yield` (percent units). Pass `{ex_date, dividend_per_share, distribution_frequency, dividend_yield_pct}`; the script checks whether the amount is per payment. Pass `{ex_date, amount}` only when the per-payment amount is known |

`ex_dividend_date` may be the last ex-date rather than the next one. The script ignores ex-dates
before today; if the next one isn't announced, say the check covered no upcoming dividend.

## What each code means, and what to say

**AUTO_EXERCISE_CASH_NEED** (long call, in the money by $0.01 or more, expiring in the radar).
Robinhood's help article on expiration, exercise and assignment says it generally auto-exercises
long options that are $0.01 or more in the money at expiration if buying power covers it, and may
close positions before expiration when it doesn't. State the cash needed (strike × 100 × contracts),
the buying power, the shortfall, and the account total from `cash_needs_by_account` (several calls
draw on the same buying power). The user's choices (closing, exercising, funding, letting it run)
are theirs; list them without ranking them.

**AUTO_EXERCISE_SHARE_DELIVERY / LONG_PUT_EXERCISE_SHORT_STOCK** (long put in the money). With the
shares in the same account, exercise sells them at the strike. Without them, exercise would open a
short stock position: `exercise_option` itself requires a separate explicit confirmation
(`allow_shorts`) for that, and this kit never calls it.

**ASSIGNMENT_SHORT_CALL_DELIVERY / ASSIGNMENT_SHORT_PUT_CASH** (short option in the money). A short
call delivers 100 shares per contract (covered or not, from `shares_held`); a short put buys 100
shares per contract at the strike. A cash-secured put's cash is normally already set aside. On a
spread the long leg can offset the short one, but only if it is exercised or auto-exercised, so say
that rather than calling the position safe.

**EARLY_ASSIGNMENT_BEFORE_EX_DIV** (short call in the money, an ex-date on or before expiration and
inside the radar, and extrinsic value at the bid smaller than the dividend). A call holder who
exercises before the ex-date collects the dividend; when the time value left in the call is smaller
than the dividend, exercising early is worth more to them than selling the call. It usually happens
at the close the day before the ex-date. Always label this an **estimate**: assignment is the
holder's choice. Show extrinsic at the bid and at the mid next to the dividend, and whether the
shares to deliver are held.

**INDEX_NO_MANUAL_EXERCISE** (index option in the radar). Index options settle in cash and cannot be
exercised manually (`exercise_option` rejects them). Show the approximate cash settlement at the
current level. Some index chains settle on the opening print (`settle_on_open` in
`get_option_chains`), so the final value can differ from the current level.

**PIN_RISK** (within max($0.50, 0.5% of strike) of the strike with two trading days or fewer left).
Whether the contract finishes in the money may not be known until after the close, and assignment
on a short leg can arrive after the user could have reacted.

**EXPIRING_WORTHLESS** (long, out of the money). Show what is left at the bid. Letting it expire and
selling what is left are both the user's call.

## Robinhood's own close time: `sellout_datetime`

`get_option_instruments` returns `sellout_datetime` per contract. Robinhood's response guide calls it
the time Robinhood force-closes the position; treat it as authoritative. The radar converts it to ET
(`sellout_text`), and with `now` it reports hours left. Put it on every radar line for a position
expiring in the radar, and lead with it when it is today.

## Pending quantities

A nonzero `pending_*` quantity on a position (the field names come from the response) means an order,
exercise, assignment or expiration is already in flight. Show it on that position's line; don't treat
the position as settled.

## Exercise questions: a handoff, never a call

`exercise_option` is a money tool: it is never called, in any mode (the plugin hook blocks it even in
confirm mode). When the user wants to exercise, prepare a handoff for the Robinhood app with:
- the contract, the account (masked), and the contracts to exercise (the user's number)
- the cash impact: a call **debits** strike × 100 × contracts; a put **credits** it and delivers
  shares (or opens a short if they aren't held)
- timing from the tool's own description: exercises submitted during market hours execute the same
  day; after the close (including late-close days) they queue for overnight processing; once past
  `queued`, an exercise is irrevocable
- index options: not manually exercisable

Then: "**Nothing was exercised.** This kit never exercises options. To exercise, open the position in
the Robinhood app and choose Exercise." A generic "exercise my calls" is not consent for anything; the
app is where the user confirms the specific contract and quantity.

`cancel_option_exercise` cancels **every** queued exercise for that option (it can't target one
batch), works only while the exercise is `queued`, and leaves the long option open, where it can still
auto-exercise or expire. Call it only when the user asks, after saying exactly that and getting an
explicit yes (connector rule R6).
