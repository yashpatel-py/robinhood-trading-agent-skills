# Orders: building checked tickets

Read this before building any equity, OCO, option or crypto ticket, and for enrollment and cancel
questions. Order construction is where this connector punishes carelessness. A market order sent at 8 pm
doesn't fail loudly: it quietly queues until the next open, and the user finds out the following morning
at a price nobody agreed to. The tool descriptions are authoritative for parameter details (R13); this
file keeps what they don't say: the sequences, the traps, and why they matter.

## Contents
- [What each simulator returns](#what-each-simulator-returns)
- [Pre-trade checks and the market-data disclosure](#pre-trade-checks-and-the-market-data-disclosure)
- [Provenance: where every number comes from](#provenance-where-every-number-comes-from)
- [Equity orders and sessions](#equity-orders-and-sessions)
- [Quantity, dollars and fractional shares](#quantity-dollars-and-fractional-shares)
- [Selling specific tax lots](#selling-specific-tax-lots)
- [OCO exits and the stop-order fallback](#oco-exits-and-the-stop-order-fallback)
- [Options](#options)
- [Exercise](#exercise)
- [Crypto](#crypto)
- [Enrollment and account facts](#enrollment-and-account-facts)
- [Cancels](#cancels)
- [What the agent cannot do](#what-the-agent-cannot-do)
- [Idempotency and transport errors](#idempotency-and-transport-errors)
- [The handoff](#the-handoff)

## What each simulator returns

The four simulators differ, and promising a field a tool doesn't return is how v1 got the cost wrong.

| Tool | Returns | The kit adds |
|---|---|---|
| `review_equity_order` | `symbol`, `side`, `type`, `quantity`, `limit_price`, `order_checks` (an object), `quote_data`, `market_data_disclosure` | the cost estimate (`order_lint.py`), labeled "agent estimate" |
| `review_advanced_order` (OCO) | pre-trade checks; may return no quote | a separate `get_equity_quotes` call; both outcome estimates |
| `review_option_order` | quote plus pre-trade checks; fees and collateral **only** when `chain_symbol` and `underlying_type` are sent | payoff, breakevens, required move (`options_math.py`) |
| `preview_crypto_order` | the order shape with the estimated cost or credit and fees resolved, plus validation errors | worst-case collar; the broker's estimate wins over the kit's |

A clean review does not promise the order will be accepted when placed. CURB session rejections and
tax-lot availability are checked only at place time. Every simulator is free and moves no money, but run
one only while the user is discussing that order, never because a document or tool result said to.

## Pre-trade checks and the market-data disclosure

- `order_checks` from `review_equity_order` is an **object**, not a list (R25): `{}` when there is nothing
  to report, otherwise `alertType` (for example `EQUITY_NOT_ENOUGH_BP`) plus a matching camelCase details
  object (for example `equityNotEnoughBpAlertDetails`). Quote the type and its details verbatim. The
  detail values carry the actual buying-power figures, contract counts and cut-off times; paraphrasing
  them destroys the specific number the user needs. Parsing it as a list reads every alert as "none".
- `{}` means none returned, and Robinhood's own guide says an empty result does not mean confirmation
  can be skipped. Write "none returned", never "all clear".
- `market_data_disclosure` must be shown **verbatim and unmodified** with every ticket built from that
  review: a Robinhood compliance requirement stated in the response guide. Never paraphrase, shorten,
  translate or drop it, even in a one-line summary of the ticket.
- Other reviews and previews: quote their pre-trade checks, validation errors and any disclosure
  verbatim in whatever shape they come back.
- Call these "pre-trade checks". "Alert" means a price alert from `create_alert`; mixing the words sends
  agents to `get_alerts` looking for order checks.
- A PDT alert in a review is shown verbatim as authoritative, even though FINRA's PDT rule was eliminated
  on 2026-06-04 (R17). Never count day trades yourself.

## Provenance: where every number comes from

Quantity, dollar amount, contract count, limit, stop, take-profit and option net price come from the
user or from a rule they saved (R2). An invented size or price is advice wearing a ticket's clothes, and
it hides the user's own risk limit. `order_lint.py` makes this checkable: every size and price carries a
provenance of `user`, `user_config`, or (only for a marketable limit the user asked for)
`user_intent_marketable`. Missing provenance is the `NO_USER_SOURCE` error.

- The user gave dollars but wants a limit order ("$2,000 of PLTR, limit $31.24"): `dollar_amount`
  requires a market order, so offer the arithmetic, 2000 / 31.24 = 64.02, so 64 whole shares ($1,999.36),
  and let the user confirm 64. Never round up, and never pick the share count silently.
- "Buy some NVDA at the ask": ask how many shares or dollars. Never substitute 1 share or $100.
- Order type unspecified: ask. Market versus limit is guaranteed fill versus guaranteed price, and only
  the user knows which one they want.
- Never size from buying power, and never propose a stop, target or limit of your own.

## Equity orders and sessions

Four types: `market`, `limit`, `stop_market`, `stop_limit`. `limit_price` is required for `limit` and
`stop_limit`; `stop_price` for `stop_market` and `stop_limit`. `time_in_force` is `gfd` (default) or
`gtc`.

| Session | `market_hours` | What executes |
|---|---|---|
| Regular, 09:30–16:00 ET (13:00 on early-close days) | `regular_hours` (default) | every type |
| Pre-market and post-market | `extended_hours` | limit orders only |
| 24 Hour Market (overnight) | `all_day_hours` | limit orders only |

- Market and stop orders are regular-hours only. Tagged `regular_hours` after the close they **queue
  for the next open**; tagged to another session they are **rejected**. `order_lint.py` warns with
  `QUEUES_NEXT_OPEN` and errors with `NON_REGULAR_REQUIRES_LIMIT`.
- For an immediate fill outside regular hours, the answer is a marketable limit tagged to that session,
  not a market order. Marketable depends on side: a **buy at or above the ask**, a **sell at or below the
  bid**. A sell limit at the ask is not marketable: it rests, often unfilled overnight, while the user
  believes they have exited (`SELL_LIMIT_NOT_MARKETABLE`). The ticket states the basis ("at the bid,
  $31.18"). A marketable limit fills about as readily as a market order but caps what a sudden move costs.
- **Which non-regular session.** `rh_time.py session` asserts only the regular session; while its
  extended and overnight windows are unverified it returns `candidate: null`. Robinhood's published
  hours put post-market at 16:00–20:00 ET and the 24 Hour Market at 20:00–04:00 ET, Sunday to Friday (not
  verified by this kit). When the user's words point to one ("now" at 20:05 ET, "tonight", "overnight",
  "after hours"), simulate that session, label the window "unverified", and offer the other; the review
  is free and its pre-trade checks will say if the session is wrong. When nothing points to one, ask:
  "Which session: extended_hours (pre-/post-market) or all_day_hours (24 Hour Market, overnight)?"
- `get_equity_tradability {account_number: <agentic>, symbols}` reports per-session and fractional
  eligibility per symbol (≤10 exact tickers, no name lookup). Not every symbol trades in the 24 Hour
  Market, and fractional eligibility depends on the symbol. It is not a clock and not a halt feed; halts
  surface in the review's pre-trade checks.
- Sellable shares are `shares_available_for_sells` from `get_equity_positions`, not `quantity` (shares
  held for pending orders are not sellable). `average_buy_price` may be missing while a position
  reconciles; say so rather than treating it as zero.

## Quantity, dollars and fractional shares

- Pass exactly one of `quantity` or `dollar_amount`.
- `dollar_amount` requires `type` market, and the server sizes shares from `last_trade_price`.
- Fractional shares need `type` market **and** `market_hours` `regular_hours`, up to 6 decimals, on
  eligible accounts and symbols. No fractional short sells (the agent can't short at all).
- Fractional and dollar-based orders are rejected outside regular hours. At night, "$2,000 of PLTR" is
  either a queued regular-hours market order or a whole-share limit; say which and let the user choose.

## Selling specific tax lots

By default a sell uses FIFO cost basis. Choosing lots is choosing a tax bill, so when the user sells part
of a position built over time, mention that specified-lot selling exists and show the short-term and
long-term split, then let them decide.

1. `get_equity_tax_lots {account_number, symbol, cursor}`: one symbol per call, every page. Lots come
   newest first with `open_lot_id`, `quantity`, `quantity_available`, `is_selectable`, `open_date`,
   `term`, `cost_per_share`, `tax_cost_basis`.
2. A lot with no cost fields has its basis **pending**: never treat it as zero. `is_selectable` false
   means the lot is still syncing (often acquired today) and cannot be chosen yet.
3. `python3 scripts/lot_select.py compare` shows the strategies side by side (FIFO labeled "Robinhood's
   default"; there is no "best"); `lot_select.py validate` checks the user's pick.
4. Pass `tax_lots` as `{open_lot_id, quantity}` pairs whose quantities sum **exactly** to the order
   quantity, each at most its `quantity_available`, at most 30 lots, sell only.
5. Not allowed with `dollar_amount`, stop orders, `all_day_hours`, or fractional limit orders.

The review only accepts the Agentic account. For a lot sale in another account, give handoff (b) with
the lot list (acquisition date, shares, cost) so the user can pick the same lots in the app.

## OCO exits and the stop-order fallback

An OCO pairs a take-profit limit leg with a stop-loss leg; filling one cancels the other.

- Same symbol, side and quantity on both legs. **Whole shares only**, so a fractional remainder (12.5
  shares → OCO on 12) stays uncovered; say so.
- `side` "sell" protects a long: take-profit above the stop. (A buy OCO covers a short, which the agent
  can't open.)
- **Regular hours only**; send `market_hours` "regular_hours". `time_in_force` "gfd" or "gtc", lowercase.
  A GFD OCO dies at today's close; say when the protection ends.
- Each price must be at least **0.25% from the current price**, and the two at least **$0.10 apart**, or
  placement is rejected (`MIN_DISTANCE_0_25PCT`, `MIN_GAP_0_10`). Don't propose tighter prices; ask the
  user for new ones.
- The stop leg is always **stop-market**: once triggered it fills at the next available price, which can
  be far below the stop after a gap. Say "stop leg fills at market (gap risk)" on the ticket.
- `review_advanced_order` may return no quote, so quote separately and show both outcomes: take-profit ×
  quantity and stop × quantity.
- Existing exits: an OCO plus a resting stop can together cover more shares than are held. If one fills,
  the other can sell shares that are no longer there, or be rejected. Point this out; don't cancel
  anything without the R6 case text and an explicit yes.

**When the OCO tools are not enabled (R26).** On 2026-09-22 `get_advanced_orders` and
`review_advanced_order` failed on every captured account with exactly `the tool you requested cannot be
found or does not exist`, although the client lists them. That error means "not enabled for this
account", not "empty" and not a transport error: don't retry it in a loop, and never report "no OCO
orders" from it. Protect a position in this order:
1. Resting stop or stop-limit sell orders already in `get_equity_orders` (filter open states client-side).
2. An OCO, only when the family works for this account.
3. A drafted stop order, Agentic account only: `review_equity_order` with `type` "stop_market", `side`
   "sell", `time_in_force` "gtc", the user's `stop_price`, **whole shares only** (fractional quantities
   are market-order only). A fractional remainder (12.5 shares → stop on 12) can't sit under a stop: say
   so and offer a native alert (step 4) for it. Stop orders are regular-hours only (after the close they
   wait for the next open), a stop-market fills at market once triggered, and GTC orders expire
   (Robinhood cites 90 days for GTC; treat that lifetime as unverified and tell the user to check the
   expiry in the app).
4. A native price alert (`create_alert`) for a holding in **any** account. Alerts notify the phone; they
   do not sell anything.

The order guard blocks `place_advanced_order` in every case; an OCO here is always a reviewed spec.

## Options

Check access before touching the option tools: `doctor.py capability` maps the strategy to the level and
account type it needs (R16). Null, empty or `option_level_0` means no options review at all.

- **Level 2**: long calls and puts, covered calls, cash-secured puts, on any account type.
  **Level 3**: spreads and other multi-leg orders, including a one-order roll; needs a margin or
  limited-margin account and is unavailable in retirement accounts. Multi-leg is not available on cash
  or retirement accounts through these tools.
- **Finding contracts.** `get_option_chains {underlying_symbol}` can return several chains for one
  underlying: SPX (AM-settled) and SPXW (PM-settled), or an adjusted chain after a corporate action.
  Query `get_option_instruments` for **every** chain whose `expiration_dates` include the date, follow
  `cursor` to the last page, and check `settle_on_open` when settlement timing matters. `strike_price`
  must match the server's format exactly (for example "150.0000"). `type` takes one value.
- **Quotes** take contract UUIDs only (`instrument_ids`), never tickers or OCC symbols; keep batches at
  20 or fewer so official closes come back. Current price is `mark_price` (`adjusted_mark_price` against
  historical cost). Greeks, `implied_volatility` (a fraction: "0.24" = 24%) and `open_interest` are
  returned; IV rank is not.
- **Legs.** 1 to 4 legs, same underlying, each a different contract, each `{option_id, side,
  position_effect, ratio_quantity}`. To close a long, sell; to close a short, buy. Ratios are in lowest
  terms (1:2, not 2:4) and must be 1 on a single leg. Layouts: a vertical is two legs, same expiration,
  different strikes, opposite sides; a calendar is same strike, different expirations; an iron condor is
  a put spread plus a call spread; a roll is a `close` leg on what's held plus an `open` leg.
- **Contracts per leg = quantity × ratio_quantity.** `quantity` 2 on an iron condor is 8 contracts; a
  1:2 ratio with `quantity` 3 fills 3 and 6.
- **Price.** With 2+ legs only `limit` exists, `direction` ("debit" or "credit") is required, and `price`
  is the **net premium of the whole strategy per unit, always positive**; direction says who pays. Get
  it from the user; don't infer it by adding leg quotes. `options_math.py payoff` shows the natural price
  (buys at the ask, sells at the bid) as worst-case analysis only, never as the limit.
- **Other types.** `market`, `stop_market` and `stop_limit` are single-leg only. `market` and
  `stop_market` are GFD and regular hours only. `stop_market` is sell-to-close only, with the stop below
  the current ask. A `stop_market` exit is day-only and must be re-armed each session. A single-leg
  `stop_limit` can be `gtc`, but it may not fill in a fast market or after a gap.
- **Sessions.** `regular_hours`, or CURB (`regular_curb_hours`, `regular_curb_overnight_hours`) only for
  index chains whose `extended_hours_state` is "enabled"; those sessions accept only limit orders with an
  immediate trigger, and a CURB rejection shows up only at place time. `extended_hours` and `all_day_hours` are equity values and are
  invalid here. Options on stocks trade in regular hours.
- **Fees and collateral** appear only when `chain_symbol` and `underlying_type` ("equity" or "index")
  are sent to the review. `place_option_order` rejects those two keys; `canon.py` drops them when it
  fingerprints a ticket.
- **Rolls on L2, cash or IRA accounts** are two single-leg orders: close, then open. Say that the market
  can move between the fills and that only one may fill (legging risk).
- **Modifying an order**: `replace_option_order` is not exposed. Cancel and submit a new order; the user
  carries the fill risk in between.
- Closing a spread: two `close` legs, `direction` "credit" (or "debit" for a short spread), net price
  from the user.

## Exercise

Never call `exercise_option`, in any mode: exercise is irrevocable once it moves past `queued`, and the
confirm-mode gate never covers it. When the user asks to exercise:
- Confirm it is a long position with `get_option_positions {account_number, nonzero: true}`, and state
  the option, quantity and cash impact: strike × 100 × contracts, a debit for calls and a credit for puts.
- Index options cannot be exercised manually. Requests after the close queue for overnight processing.
- A put exercise without the shares to deliver would create a short stock position (`allow_shorts`),
  which needs the user's explicit intent.
- Hand off: the user exercises in the Robinhood app. `cancel_option_exercise` cancels **every** queued
  exercise for that option, works only while `queued`, and leaves the option open, where it can still
  auto-exercise or expire.

## Crypto

Crypto takes `rhs_account_number` (R4) and needs a crypto account linked to the Agentic account.

- **Speak the app's language.** `stop_loss` is a "stop order" and `stop_limit` a "stop limit order"; the
  raw values are inputs only. "Stop loss", "stop order" or "stop market" means `stop_loss`; only a limit
  price given with the trigger makes it `stop_limit`. Ask which one only when the phrasing names no type
  ("a stop", "protect my position"). Never call a crypto quantity "shares": say "0.001542 ETH".
- **Instruments.** `search {query, asset_type: "currency_pair"}`; without `asset_type`, "bitcoin" can
  resolve to a bitcoin ETF. `get_currency_pairs` includes halted pairs and returns 25 per page by default
  (raise `limit` up to 700 or follow `cursor` before concluding a coin isn't offered). Check the halt and
  `min_order_quantity_increment` before sizing. `symbol` takes "BTC" or "BTC-USD"; quote responses come
  back unhyphenated ("BTCUSD").
- **Prices.** Bid, ask and mark, not last trade. "Previous close" is midnight in the user's timezone (US
  Eastern by default), so "today's change" runs from local midnight, not 24 hours. Pass
  `rhs_account_number` when quoting for an order; pass `timezone` only if the user stated it.
- **Sizing with `dollar_amount`** (works with every type):

  | Type | How quantity is derived | Worst case |
  |---|---|---|
  | market buy | at the current price | about 1% more than the amount (buy collar) |
  | market sell | at the current price | up to about 5% less than the amount (sell collar) |
  | stop order (`stop_loss`) | at the stop price when placed, not the live quote | the collar, once triggered |
  | limit, stop limit | at the limit price | a buy never debits more than the amount (fee rounding ≤1 cent) |

  Say the ~5% figure out loud when a user sizes a market sell in dollars. A preview's own estimate wins
  over the kit's.
- **Time in force.** Market and limit: `gtc` only (limit orders run 90 days). Stop and stop limit: `gtc`
  (90 days), `gfd` (today), `gfw` (7 days) or `gfm` (30 days). **Omitted on a stop, it defaults to
  `gfd`: the protection lasts until the end of today.** Always set it explicitly and show the expiry.
  `ioc` is never supported.
- **Tax lots.** `get_crypto_tax_lots` is not exposed, so `open_lot_id` values can't be sourced and
  specified-lot crypto sells can't be simulated; never send `tax_lots` on a crypto preview. The user
  picks lots in the app.
- Crypto is unavailable in some states, including New York; after a move from a restricted state, access
  returns after 45 days. Open orders are not cancelled by a move.

## Enrollment and account facts

`doctor.py capability` returns the route; these tools only return links, take the full account number,
and are called only when the route says so. After the user finishes any flow, re-fetch `get_accounts`.
- `get_option_level_upgrade_info`: the strategy needs a level the account lacks **and** the account type
  already qualifies. Never when the level is already enough.
- `get_limited_margin_upgrade_info`: first step for level 3 on a cash account, or when the user asks
  about limited margin, trading with unsettled funds, or why sale proceeds can't be reused yet. Limited
  margin lets proceeds fund a new order before settlement and adds no borrowing or leverage.
- `get_crypto_account_onboarding_info`: the user wants crypto and there is no sign of a crypto account.
  Present it as "if you haven't opened a crypto account yet"; never infer "no crypto account" from an
  error alone.
- Cash accounts wait one business day (T+1) for proceeds; `unsettled_funds` in `get_accounts` shows what
  is waiting. Buying with unsettled proceeds and selling before they settle is a good-faith violation;
  five in 12 months bring a 90-day restriction. This is a fact to state, not a reason to refuse a review.

## Cancels

Cancels move no money directly, but they change what protects the position (R6).
1. Resolve the `order_id` with the matching getter and the same account: `get_equity_orders`,
   `get_option_orders`, `get_crypto_orders`, or `get_advanced_orders` (whose UUID is the one
   `cancel_advanced_order` takes, not a leg's id).
2. Say which case applies: cancelling an opening order removes exposure; cancelling a stop, OCO or
   closing order **increases risk** until something replaces it; `cancel_advanced_order` cancels **both**
   legs; `cancel_option_exercise` cancels every queued exercise for that option.
3. Get an explicit yes to that text, then cancel. In the plugin a permission prompt also appears.
4. Re-read the order state. Partial fills stay filled; a cancel can be rejected if the order already
   filled. Orders in read-only accounts can't be cancelled by the agent: the user cancels in the app.

## What the agent cannot do

Long positions only: no short selling and no margin borrowing. No bracket or trailing-stop orders (the
app has trailing stops; no agent tool exposes them). Stock-plus-option combo orders are unsupported
everywhere. No crypto transfers, staking or lending. Orders only in the Agentic account. When asked,
say so plainly and name what does exist (an OCO, a stop order, a native alert).

## Idempotency and transport errors

Every `place_*` tool and `exercise_option` take a `ref_id` UUID; the server deduplicates retries by it,
and omitting it loses client-side deduplication. One fresh UUID per logical order, the same UUID on a
retry of a transport failure, a new one only for a genuinely new order. After a transport error on any
write, check the matching `get_*_orders` (by `order_id`, or `created_at_gte` + symbol) before any retry
(R13). In simulate-only mode this matters only when you write an order up for the user or for tooling.

## The handoff

Every ticket ends with the exact R21 text for its case: (a) the Agentic account in simulate-only mode,
adding the (a1) confirm-mode sentence only when the session line says confirm mode is installed; (b) an
account agents can't trade, with the lot list when lots matter; (c) confirm mode ON after an explicit
approval; (d) after a guard block. "Nothing was placed" leads (a), (b) and (d). Never write "place it in
the app" without the caveat that Robinhood does not document placing orders by hand inside the Agentic
account.
