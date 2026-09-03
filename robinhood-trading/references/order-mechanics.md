# Order mechanics

Order construction is where this connector punishes carelessness. A market order sent at
8pm doesn't fail loudly — it quietly queues until the next open, and the user finds out
the following morning at a price nobody agreed to. Read this before building any order.

## Contents
- [The review handoff](#the-review-handoff)
- [Equity order types and sessions](#equity-order-types-and-sessions)
- [Quantity, notional, and fractional shares](#quantity-notional-and-fractional-shares)
- [Selling specific tax lots](#selling-specific-tax-lots)
- [Options](#options)
- [Crypto](#crypto)
- [Idempotency](#idempotency)

## The review handoff

`review_equity_order`, `review_option_order`, and `preview_crypto_order` simulate. They
return the live quote, resolved fees and collateral, estimated cost or credit, and
`order_checks` alerts. Nothing is placed and no money moves.

Surface every alert **verbatim**. The detail strings carry the actual buying-power
figures, contract counts, and cut-off times — paraphrasing them destroys the specific
number the user needs to act on.

Then hand off. Present the full order spec and let the user place it themselves in the
app. Do not call the `place_*` tools or `exercise_option`.

## Equity order types and sessions

Four types: `market`, `limit`, `stop_market`, `stop_limit`. If the user hasn't said which,
ask rather than assuming — the difference between a market and a limit order is the
difference between a guaranteed fill and a guaranteed price, and only the user knows which
one they actually want.

Sessions are set with `market_hours`:

| Session | Value | What executes |
|---|---|---|
| Regular, 9:30–16:00 ET | `regular_hours` (default) | everything |
| Pre/post-market | `extended_hours` | limit orders only |
| Overnight / 24 Hour Market | `all_day_hours` | limit orders only |

`market`, `stop_market`, and `stop_limit` are regular-hours only. Tagged to another
session they're rejected; left as `regular_hours` after the close they queue for the next
open. When someone wants an immediate fill outside regular hours, the answer is a
**marketable limit** — a limit at the current ask — tagged to that session, not a market
order.

That advice holds during regular hours too: a marketable limit at the ask fills about as
readily as a market order but caps what a sudden move can cost.

`time_in_force` is `gfd` (good for day, default) or `gtc` (good till cancelled).
`limit_price` is required for limit and stop_limit; `stop_price` for stop_market and
stop_limit.

## Quantity, notional, and fractional shares

Pass exactly one of `quantity` or `dollar_amount`.

- `dollar_amount` requires `type=market`; the server derives share count from
  `last_trade_price`.
- Fractional shares need `type=market` **and** `market_hours=regular_hours`, up to six
  decimal places, on eligible accounts. No fractional short sells.
- Both fractional and dollar-based orders are rejected outside regular hours.

## Selling specific tax lots

By default sells use FIFO cost basis. To sell chosen lots, call `get_equity_tax_lots`
first with the account number and symbol (one symbol per call, paginated via `cursor`),
then pass `tax_lots` as `{open_lot_id, quantity}` pairs whose quantities sum exactly to
the order quantity.

Limits: sell side only, at most 30 lots, US accounts only. Not allowed alongside
`dollar_amount`, stop orders, `all_day_hours`, or fractional limit orders.

This matters more than it looks — choosing lots is choosing a tax bill. When the user is
selling part of a position they've built over time, mention that specified-lot selection
exists and show the holding-period split, then let them decide.

## Options

Check the account's options level before touching these tools. `review_option_order`
requires `option_level_2` or `option_level_3`; if the level is empty or `option_level_0`,
do not call it — call `get_option_level_upgrade_info` with the account number and pass on
the enrollment path.

- **Level 2** covers single-leg strategies: long calls and puts, covered calls,
  cash-secured puts. **Level 3** adds multi-leg spreads.
- Legs come from `get_option_instruments` (`option_id`), with `side` and `position_effect`
  (`open`/`close`). To close a long, sell; to close a short, buy.
- Leg layouts: a vertical is two legs, same expiration, different strikes, opposite sides.
  A calendar is same strike, different expirations. An iron condor is four legs — a put
  spread plus a call spread. A roll is a `close` leg on what's held plus an `open` leg on
  the replacement.
- With 2+ legs, `direction` (`debit`/`credit`) is required and only `limit` type is
  available. `price` is the **net premium of the whole strategy**, always positive —
  direction says who pays. Get that net price from the user; don't infer it by adding up
  leg quotes.
- `market` and `stop_market` are GFD and regular-hours only; `stop_market` is
  sell-to-close only with a stop below the current ask. Non-limit types are blocked in any
  extended session.
- Pass `chain_symbol` plus `underlying_type` (`equity`/`index`) whenever known — that's
  what makes fees and collateral appear in the response.
- Multi-leg isn't available on cash or retirement accounts here.

## Crypto

Crypto uses `rhs_account_number`, not `account_number`, and needs an agentic-enabled
crypto account.

**Speak the app's language, not the API's.** `stop_loss` is a "stop order" and
`stop_limit` a "stop limit order" — the raw enum values are inputs only, never words for
the user. A user asking for a "stop loss," "stop order," or "stop market" means
`stop_loss`; only a limit price alongside the trigger makes it `stop_limit`. Ask which
they mean only when the phrasing names no type at all ("a stop," "protect my position").

And never call a crypto quantity "shares" — that's equity vocabulary. Say "0.001542 ETH."

`dollar_amount` works with every crypto type, but how quantity is derived varies, and so
does the worst case:

- **market**: sized at the current market price. A buy's typical debit is ~`dollar_amount`,
  worst case ~1% more (buy collar). A sell's typical credit is ~`dollar_amount`, worst
  case up to ~5% less (sell collar).
- **stop_loss**: sized at `stop_price` when placed, not at the live quote.
- **limit / stop_limit**: sized at `limit_price`. A buy's debit is capped at
  `dollar_amount`; fee rounding can add at most a cent on fee-priced accounts.

That ~5% sell collar is worth saying out loud when a user sizes a market sell by dollars.

`time_in_force` depends on type: market and limit accept `gtc` only (limit orders run 90
days). Stop types accept `gtc`, `gfd`, `gfw`, or `gfm`. `ioc` is never supported.

`symbol` takes either a bare asset (`BTC`) or a pair (`BTC-USD`).

## Idempotency

`place_*` takes a `ref_id` UUID so a retried transport failure can't double-fill. You
aren't placing orders, but when you write up an order for the user or build tooling around
this, that's the mechanism: one fresh UUID per logical order, the same UUID on retries, a
new one only for a genuinely new order.
