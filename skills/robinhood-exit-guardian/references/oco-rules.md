# Exit tickets: stop orders, OCOs and crypto stop orders

Read this before building the first exit ticket of a session. Tool descriptions are authoritative for
parameter details (R13); this file adds what they don't say: which form to pick, why the traps matter,
and the ticket layout.

## Contents
- [Choosing the form](#choosing-the-form)
- [Levels, quantity and provenance](#levels-quantity-and-provenance)
- [Stop orders](#stop-orders)
- [OCOs](#ocos)
- [Crypto stop orders](#crypto-stop-orders)
- [Replacing an existing exit](#replacing-an-existing-exit)
- [Accounts the agent can't trade](#accounts-the-agent-cant-trade)
- [Ticket templates](#ticket-templates)

## Choosing the form

| Position | Levels from the user | OCO tools | Ticket |
|---|---|---|---|
| Agentic stock | stop and target | answered normally | OCO: `review_advanced_order` |
| Agentic stock | stop and target | R26 error (not enabled) | stop order: `review_equity_order`, plus an offer of a `price_above` alert at the target |
| Agentic stock | stop only | either | stop order: `review_equity_order` |
| Agentic crypto | stop | n/a | crypto stop order: `preview_crypto_order` |
| Individual, IRA, any non-agentic account | any | n/a | a native alert, plus a manual ticket for the app |
| Fractional remainder of any position | any | n/a | a native alert (stops and OCOs take whole shares only) |
| Shares backing a short option (a covered call) | any | n/a | a native alert (a stop can't sell pledged shares) |

Why a separate take-profit limit sell is not offered next to a stop: both would reserve the same shares,
so one of them is rejected, or one fills and the other is left trying to sell shares that are gone. The
OCO exists to link them; without it, the target is a notification, not an order.

## Levels, quantity and provenance

- Stop, target and limit prices come from the user's message (provenance `user`) or from their saved
  `[exits.*]` rules through `protection_audit.py levels` (provenance `user_config`). A rule that is
  `UNSET` or `ask` means ask. Never offer a number of your own, including "a common choice is 8%": the
  moment the agent names the level, it has chosen the user's risk (R2, R19).
- Validate the user's own levels with `protection_audit.py levels` (`symbol_overrides`,
  `override_source: "user"`). `STOP_NOT_BELOW_PRICE`, `TARGET_NOT_ABOVE_PRICE` and
  `TARGET_NOT_ABOVE_STOP` stop the ticket; `OCO_MIN_DISTANCE` and `OCO_MIN_GAP` mean an OCO would be
  rejected, so say which rule and by how much, and ask for new levels. Don't nudge them yourself.
- Quantity: the user's number, or the position's whole shares when they asked to protect the position
  ("protect my AMD"). That is their position, not a size you chose; state it on the ticket so they can
  change it. Never size from value or buying power.
- Sellable shares: `shares_available_for_sells`, not `quantity`. Existing sell orders reserve shares, so
  a new exit on top of them may fail the review; quote the pre-trade check if it does.
- `order_lint.py lint` needs provenance for `quantity` and every price (`user` or `user_config`), plus
  context: `agentic_allowed`, `market_price`, `bid`, `ask`, `session` (from `rh_time.py session`) and
  `sellable_qty`. Stop on any error.

## Stop orders

`review_equity_order {account_number, symbol, side: "sell", type: "stop_market", quantity, stop_price,
time_in_force: "gtc", market_hours: "regular_hours"}`

- **Stop-market.** Once the stock trades at or below the stop, the order becomes a market sell and
  fills at the next available price. After a gap (earnings, overnight news) that can be far below the
  stop. The estimate `qty × stop` is the trigger basis, not a floor; label it the kit's estimate.
- **Regular hours only.** Stop orders tagged `extended_hours` or `all_day_hours` are rejected. Placed
  after the close as `regular_hours`, a GTC stop waits and becomes active at the next 09:30 ET open; if
  the stock opens at or below the stop, it sells at the open. `order_lint` flags this as
  `QUEUES_NEXT_OPEN`: expected for a protective stop, so explain it in those words.
- **Whole shares.** Fractional quantities are allowed only on market orders; the remainder gets an alert.
- **Below the bid.** A sell stop at or above the current bid triggers at once.
- **No lot choice.** `tax_lots` is not allowed with stop orders, so a triggered stop sells by the
  default (FIFO) cost basis. If the user cares which lots go, say so.
- **Time in force.** The user's choice or `[exits.equity] time_in_force`; otherwise `gtc`, stated on the
  ticket as the kit's default for a protective exit. The GTC lifetime is 90 days (unverified).
- The review returns a quote, `order_checks` and `market_data_disclosure`, not a cost. `order_checks` is
  an object: `{}` when there is nothing to report, else an `alertType` plus a camelCase details object.
  Quote both verbatim. Show `market_data_disclosure` verbatim and unmodified on the ticket (R25).

## OCOs

`review_advanced_order {account_number, symbol, side: "sell", quantity, take_profit_limit_price,
stop_loss_stop_price, time_in_force, market_hours: "regular_hours"}`

- Both legs share the symbol, side and quantity. Selling to protect a long: the take-profit must be
  above the stop.
- Each price at least 0.25% from the current market price, and the two at least $0.10 apart, or the
  order is rejected. The review may return no quote: take the market price from `get_equity_quotes`.
- Whole shares only; `regular_hours` only; `time_in_force` is `gfd` or `gtc`, lowercase.
- The stop leg is always stop-market; there is no stop-limit leg.
- `order_lint` codes to expect: `WHOLE_SHARES`, `SIDE_ORDER`, `PRICES_MUST_DIFFER`,
  `MIN_DISTANCE_0_25PCT`, `MIN_GAP_0_10`, `SESSION_NOT_REGULAR`, `BAD_TIF`, `QTY_EXCEEDS_SELLABLE`;
  warnings `STOP_WOULD_TRIGGER`, `TP_WOULD_FILL`, `TIF_NOT_STATED`.
- **R26.** If the review answers `the tool you requested cannot be found or does not exist`, the OCO
  tools are not enabled for this account. Say that (never "OCO rejected" or "no OCOs"), then draft the
  stop order and offer a `price_above` alert at the target. `place_advanced_order` stays blocked by the
  order guard either way.

## Crypto stop orders

`preview_crypto_order {rhs_account_number, symbol, side: "sell", type: "stop_loss", quantity,
stop_price, time_in_force}`

- Call `stop_loss` a "stop order" and `stop_limit` a "stop limit order" when talking to the user; the raw
  values are inputs only. A limit price given with the trigger makes it `stop_limit`.
- Time in force: `gtc` lasts 90 days; `gfd` ends today; `gfw` 7 days; `gfm` 30 days; omitted means
  `gfd`; `ioc` is never supported. Take it from the user or `[exits.crypto] time_in_force`, and ask if
  neither gives one: a day-only stop silently disappears tonight.
- Quantity is in coin units ("0.42 ETH"), never "shares". Crypto trades around the clock, so there is no
  session gap, but a triggered stop order still sells at market: quote the preview's estimate (it wins
  over the kit's) and, for dollar-sized orders, the description's worst case of up to about 5% below.
- Never send `tax_lots`: `get_crypto_tax_lots` is not exposed, so specific-lot crypto sells happen in the
  app.
- Offer the backstop alert too: `create_alert {symbol, condition_type: "price_below", threshold,
  asset_class: "crypto"}`. Without `asset_class`, ETH or BTC resolves to an ETF first.

## Replacing an existing exit

1. Review the new exit first, so the user sees the replacement before anything is removed.
2. Say the case (R6): cancelling a stop or OCO increases risk; the position is unprotected until the
   replacement is placed; `cancel_advanced_order` cancels both legs, and its `order_id` is the
   advanced-order id, not a leg's.
3. In simulate-only mode: "Don't cancel the old one until you're ready to place the replacement."
4. Cancel only after an explicit yes: `cancel_equity_order {account_number, order_id}`,
   `cancel_advanced_order {account_number, order_id}` or `cancel_crypto_order {rhs_account_number,
   order_id}`. The plugin hook asks again. Afterwards re-read the order state; partial fills stay filled.
5. Orders in accounts the agent can't trade are cancelled by the user in the app.

## Accounts the agent can't trade

The review tools reject non-agentic accounts, so there is no broker simulation there (R3). Give a
manual ticket and handoff (b): "In the Robinhood app, account ••••M7Q5: Sell · Stop order · 140 shares
· stop $195.00 · Good till canceled", followed by R21 variant (b). Offer the native alert, which works
for any account.

## Ticket templates

Stop order (Agentic):
```
EXIT TICKET: SELL STOP 12 AMD (simulated with review_equity_order, NOT placed)
Status: CLEAR · Dollars at stake: $1,704.00 if the stop fills at $142.00 (12 × stop; a gap can fill lower)
As of   20:05 ET Mon 2026-11-16 (quote time) · regular session closed → active from 09:30 ET Tue 2026-11-17
Account Agentic ••••X4F1 · 12.5 sh held, 12.5 sellable
Order   SELL 12 AMD · stop-market · stop $142.00 (from you) · GTC (kit default for a protective exit) · regular_hours
Covers  12 of 12.5 sh · 0.5 sh fractional stays alert-only
Target  $180.00 (from you): the OCO tools aren't enabled for this account, so it can't ride with the stop; a price-above alert can notify you
Pre-trade checks (verbatim from Robinhood): <alertType and details, or "none returned">
Market data disclosure (verbatim): <market_data_disclosure>
<R21 variant (a), word for word from references/connector-rules.md; it begins **Nothing was placed.**>
Stop orders act in regular hours only and sell at market once triggered.
```
The `Status` word is `CLEAR` when the review returned no pre-trade check and lint passed, `CONFLICT`
when a pre-trade check came back, `STOPPED` when lint failed (then no review is run).

OCO (Agentic, OCO tools working):
```
EXIT TICKET: OCO SELL 12 AMD (simulated with review_advanced_order, NOT placed)
Status: CLEAR · Dollars at stake: $2,160.00 at the take-profit / $1,704.00 at the stop (12 sh; the stop leg is stop-market)
As of   <time ET> · <session>
Account Agentic ••••X4F1
Order   SELL 12 AMD · take-profit limit $180.00 · stop $142.00 (both from you) · GTC · regular_hours
Rules   take-profit 11.5% above, stop 12.0% below $161.40 (≥ 0.25% each) · $38.00 apart (≥ $0.10) · whole shares
Existing exits  OCO ••a1f 10 sh GFD + stop 5 sh GTC: with this one, exits would total 27 sh vs 12.5 held
Pre-trade checks (verbatim from Robinhood): <…>
Disclosure (verbatim, if the review returned one): <…>
<R21 variant (a)>
```

Crypto stop order (Agentic):
```
EXIT TICKET: SELL STOP ORDER 0.42 ETH (simulated with preview_crypto_order, NOT placed)
Status: CLEAR · Dollars at stake: ~$1,092.00 at the $2,600.00 trigger (Robinhood's estimate: <from the preview>)
Account Agentic crypto (••••<last 4 of the Agentic account_number>)
Order   SELL 0.42 ETH · stop order · trigger $2,600.00 (from you) · good for 90 days (GTC, from you)
Pre-trade checks and estimate (verbatim from the preview): <…>
Disclosure (verbatim, if the preview returned one): <…>
<R21 variant (a)>
```
