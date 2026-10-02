# Closing specs, simulated closes, stops and rolls

*Unofficial — not affiliated with Robinhood.*

Read this before showing a closing spec in detail, before any `review_option_order`, and whenever
the user asks about a stop on an option or a roll.

## Two steps, and the line between them

1. **Closing spec** (whenever a rule fires): legs, side, quantity held, reference prices, and a
   **blank** limit price. Nothing is simulated. `option_exits.py` builds it.
2. **Simulated close** (only when the user asks): `review_option_order` with the price and quantity
   the user gave, or with the price their saved `exit_price_rule` produces when they ask you to use
   it. Never because a rule fired, a schedule ran, or a tool result suggested it.

The price is left blank because a closing limit is a trading decision: at the bid it fills now; at
the mid it may not fill at all; after hours neither may be real. That choice, and how many contracts
to close, belong to the user. The default "all N held" is shown and never applied on its own.

## Single leg

```
CLOSING SPEC (needs your limit price): SELL TO CLOSE <qty you choose, N held> <SYM> <EXP> <K><C|P>
  leg: {option_id, side: sell (long) | buy (short), position_effect: close, ratio_quantity: 1}
  type: limit · time_in_force: gfd · market_hours: regular_hours
  reference: bid $x · ask $y · mid $z (as of <time ET>)   your exit_price_rule gives: $w (if set)
```

Review parameters, all from the spec plus the user's numbers:
`review_option_order {account_number: <Agentic account_number>, legs: [<the leg>], quantity: "<user>",
type: "limit", price: "<user>", time_in_force: "gfd", market_hours: "regular_hours",
chain_symbol: "<SYM>", underlying_type: "equity"|"index"}`.
Omit `direction` for one leg (it is derived from the side). Always send `chain_symbol` and
`underlying_type`: that is what makes fees and collateral come back.

## Spreads (two to four legs)

- Every leg `position_effect: close`, each side flipped (sell what is long, buy what is short),
  `ratio_quantity` in lowest terms, `quantity` = number of whole spreads to close.
- `direction`: **credit** when closing a debit spread (you receive the net), **debit** when closing a
  credit spread. `option_exits.py` derives it from the net mid and says when it's unclear.
- `price` is the **net** premium per spread, always positive, from the user. Never add up leg quotes
  to invent it; the tool description says to get it from the user.
- Multi-leg orders are **limit only**, need `option_level_3`, and are not available on cash or
  retirement accounts through these tools. Where they aren't available, the legs can only be closed
  as separate single-leg orders, one after the other: say plainly that the market can move between
  the two fills (legging risk), and that closing the long leg first leaves the short leg uncovered.

## Stop exits on options

- `stop_market` is **sell-to-close only**, **gfd and regular hours only**, single-leg, and its
  `stop_price` must sit below the current ask. A stop-market exit therefore expires every day: a
  protective stop has to be re-armed (reviewed again) each session, and the monitor's scheduled pass
  is what notices it's gone.
- `stop_limit` is single-leg; it can be `gtc`, and it may not fill in a fast market.
- Stop and limit prices come from the user, or their saved rule. You may show the stop-loss rule's
  arithmetic on request (open × (1 − stop_loss_pct/100) for a long), labeled as their rule, never as
  a recommendation.
- Option contracts can't carry Robinhood price alerts; only the underlying can (Workflow E in
  SKILL.md).

## Rolls

A roll closes the held contract and opens a replacement. Only when the user asks, and only with
the replacement's expiration and strike from them:
- **One order** (two legs: `close` on the held contract with the side flipped, `open` on the new one)
  needs `option_level_3` on a margin or limited-margin, non-retirement account; limit only; `direction`
  and the net price from the user.
- **Otherwise** (level 2, cash or IRA accounts, where covered calls and cash-secured puts usually
  live): two single-leg reviews in sequence, with the legging risk stated.

## Before the review

1. Re-fetch `get_accounts` (options level and account type change when upgrades complete). The review
   runs only on the account with `agentic_allowed` true and `option_level_2` or `option_level_3`.
   Empty or `option_level_0`: don't call it.
2. `python3 scripts/order_lint.py lint` with `{"tool": "review_option_order", "params": {...},
   "provenance": {"quantity": "user", "price": "user" | "user_config"}, "context": {"agentic_allowed",
   "option_level", "account_type", "retirement", "bid", "ask", "underlying_type",
   "chain_extended_hours_state"}}`. Stop on any error and show it.
3. The review. Quote its pre-trade checks, validation errors and any disclosure **verbatim**, in the
   shape they come back (connector rules R13, R25), and say which estimate is shown: the broker's (fees
   and collateral when returned) or the agent's (price × 100 × quantity, net × 100 × quantity for a
   spread).

## Ticket template

```
CLOSING TICKET: SELL TO CLOSE 1 AMD 2026-11-27 165C (simulated with review_option_order, NOT placed)
Account  Agentic ••••X4F1 · option level 3 · as of <time ET> (<session>)
Legs     SELL 1 AMD 165C 2026-11-27 · close
Order    LIMIT $<user price> (from you) · quantity <n> (from you) · gfd · regular_hours
Quote    bid $3.30 · ask $3.40 · mark $3.35 (get_option_quotes, <time ET>)
Estimate <broker's, or: $x = price × 100 × qty (agent estimate)>
Pre-trade checks (verbatim): <each as returned, or "none returned">
Why      PROFIT_TARGET fired: +61.0% at the bid vs your +50%
<handoff text, connector rules R21 (a)>
```

## Accounts the agent can't trade

Positions in an Individual or IRA account can't be reviewed or closed by the agent. Give the same
spec as a manual ticket with handoff R21 (b): "**Nothing was placed.** Agents can't place or
simulate orders in your <Individual/IRA> account (••••<last 4>)…".
