# Exit rules: reading them, feeding them, reporting them

*Unofficial — not affiliated with Robinhood.*

Read this before the first rules check of a session, and whenever a rule result looks surprising.
The arithmetic is in `formulas.md`; `scripts/option_exits.py` does it for you.

## The rules belong to the user

The four rules in `[options.exits]` (profit target, stop loss, time stop, maximum holding period)
*are* the exit strategy. Choosing them depends on risk tolerance, taxes and horizon, which you cannot
see, so an agent that fills one in has given investment advice while appearing to follow
instructions.

- Load them with `kitconfig.py get` (`{"section": "options.exits", "cwd": "<the user's project directory>"}`, or
  `{"text": "<pasted block>", "section": "options.exits"}` when the user pasted a
  `# robinhood-skills:config` block). Its `unset` list is what you report as "not configured".
- A rule that is `UNSET` is **skipped and named**, never replaced by a "reasonable" value. The radar
  and the P&L table still work with no rules at all.
- If the user asks for a rules check and nothing is set, ask for the values they want, by name, and
  suggest none. Offer to save only what they state, after showing the change and getting a yes. Never
  loosen a saved rule to make something fire or not fire.
- `radar_days` in `[options.monitor]` is a window, not a threshold; it defaults to 7.

## What the script needs, and where each value comes from

| Script field | Source |
|---|---|
| `option_id`, `symbol` (= `chain_symbol`), `side` (= position `type`: long/short), `quantity`, `expiration` (= `expiration_date`), `multiplier` (= `trade_value_multiplier`), `pending` (every `pending_*` field) | `get_option_positions {account_number, nonzero: true, cursor}`, every page |
| `average_price` + `average_price_unit` | the same rows. Pass the raw value; set the unit only if the response `guide` states it, otherwise `"unknown"` (see `formulas.md` for the check the script runs) |
| `open_fill_price_per_share` | the opening fill in `get_option_orders` (below). It settles the `average_price` unit; without it an unknown unit leaves P&L blank and the profit target and stop loss unevaluated |
| `type` (call/put), `strike` (= `strike_price`), `underlying_type`, `min_ticks` | `get_option_instruments {ids: "<comma-separated option_ids>", cursor}` |
| quotes: `bid`, `ask`, `mark`, `adjusted_mark`, `prev_close`, `ts` | `get_option_quotes {instrument_ids: [≤20]}`: `bid_price`, `ask_price`, `mark_price`, `adjusted_mark_price`, `close.price`, `updated_at` |
| `opened_date` | `get_option_orders` (below); `"unknown"` when not found |
| `account_last4`, `agentic` | `get_accounts` (`agentic_allowed`) |
| `as_of`, `now`, `in_regular_session` | a time the user stated, else quote timestamps, through `rh_time.py session` |

Two different `type` fields: on a **position** it is long/short; on an **instrument** it is
call/put. Mixing them up flips every sign in the report.

## Opening fills: days held and the average-price unit

Fetch them when `max_hold_days` is set, when the `get_option_positions` guide does not state the unit
of `average_price`, and for any position the script reports under `rules_not_evaluated` with
`AVG_PRICE_UNIT_AMBIGUOUS` or `AVG_PRICE_UNIT_CONFLICT` (then rerun it with the fills).

`get_option_orders {account_number, chain_ids: "<chain_id of each held contract>", created_at_gte:
<today − 400 days, as UTC>, cursor}`, with no `state` filter (it takes one value, and you need
several), every page. Option-order fields were not in the live capture, so read them in hedged terms
and follow the response guide: for each held contract, keep the **filled** orders whose leg names
that contract with position effect **open**, after the last time the position went to zero.

- `opened_date`: the fill date (the order's last transaction or update time, converted to ET; if only
  the creation time exists, use it and say so) of the earliest such fill. If none is found inside
  the window, days held is `unknown`, MAX_HOLD is listed under `rules_not_evaluated`, and the report
  says why. Never estimate it from the expiration or the average price.
- `open_fill_price_per_share`: the per-share price those opening fills were at, quantity-weighted
  when there were several (the order's average fill price if the response has one, else its limit
  price; the unit check only needs it to within 2×). For a multi-leg opening order, pass this leg's
  own execution price only if the response gives one: the order's net price is for the whole
  strategy, never for one leg. Not found → leave it out.

Why the fill and not the quote: the fill is the same trade as `average_price`, so the right reading
is within 2× of it however far the market has moved since. Against the current quote, a short sold at
$0.10 that is now $2.00 (−1950%) looks exactly like a $10.00 sale that is up 79.5%.

Why 400 days: long-dated contracts are routinely held for months, and `created_at_gte` bounds when an
order was created, not when it filled.

## Spreads are judged as one position

The connector returns one row per contract. A debit spread's short leg "losing 150%" while the
spread is up 40% is the trade working, so judging legs one by one produces false signals in both
directions.

- The script reports long and short legs on the same underlying in the same account under
  `needs_grouping` and does not fire rules on them.
- Ask once: "Are these one spread?" Then rerun with `groups: [{"group_id", "option_ids": [...]}]`
  (2–4 legs, same account and chain; add `"account_last4"` when the same contract is held in more than
  one account), or with their ids in `standalone` if they are separate trades.
- The same contract held in two accounts is two positions: pass both rows; each is judged on its own.
- A covered call (short call plus shares) is not a spread here: it is judged as the short call.

## Reading the result

- `fired`: the rules that fired, P&L at bid/ask, the app's mark-based P&L for comparison, days to
  expiration, days held, and a **closing spec with a blank limit price** (`close-specs.md`).
- `watch`: evaluated, nothing fired. Show P&L, DTE and days held so the user sees the distance to
  each rule.
- `not_configured`: rules that are UNSET. Always name them.
- `skipped`: positions that could not be evaluated at all (no quote, expired but still listed), with
  the reason.
- `rules_not_evaluated`: configured rules that could not be checked on an evaluated position: the
  profit target and stop loss when the open price can't be settled (`AVG_PRICE_UNIT_AMBIGUOUS`,
  `AVG_PRICE_UNIT_CONFLICT`, `AVG_PRICE_MISSING`; that row's P&L is blank and `open_price_problem`
  says why), MAX_HOLD when the opening date is unknown. The time stop still runs on those positions.
- Anything in `skipped` or `rules_not_evaluated` makes the run's status `UNKNOWN` unless a rule fired
  (`EXIT SIGNAL`) or the radar found something (`ACTION NEEDED`); it is listed either way.
- `warnings`: zero bid, crossed or stale quote, pending activity, an open price far from the mark, an
  opening fill that matches neither reading, an adjusted contract. Put each one next to its position.

Quotes outside the regular session are the last session's and are often wide. Say so, and say the
result may change at the open. A long with no bid is worth $0.00 to close at that moment; that can
fire a stop that a two-sided market would not.

## Pending activity

Any nonzero `pending_*` quantity on a position (the exact field names come from the response) means
something is already in flight: a closing order working, an exercise or assignment being processed,
an expiration pending. Show it, and don't prepare a new closing spec for those contracts until the
user has checked `get_option_orders` or the app.

## Scheduled runs

A schedule (every 30 minutes to daily; 15:00 ET on expiration days catches the last useful hour)
runs one stateless pass: read, compute, report. It never calls `review_option_order`, never creates
alerts, never writes config and never places anything. The status line comes first. If no Robinhood
tool is visible, the whole reply is `CONNECTOR UNAVAILABLE: no Robinhood tools in this session`.

Scope on a schedule: nobody is there to ask, so the pass follows `[policy] read_scope` (SKILL.md step
1). `all`, or no setting, reads every account `get_accounts` lists, because covered calls and
cash-secured puts usually sit in the level-2, cash or IRA accounts; `agentic_only`, or accounts named
in the scheduled prompt, reads those and lists the rest as not in scope; `ask` reads the Agentic
account only and lists the rest as not read. Line 2 of the report names every account either way,
and an account that was not read keeps the status at `UNKNOWN`, never `NO ACTION`.
