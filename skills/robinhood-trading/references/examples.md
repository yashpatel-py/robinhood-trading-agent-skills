# Worked examples

Read this before writing a ticket or a one-line answer for the first time in a session. The three pairs
show the level of detail expected. All values are sandbox data from the kit's eval fixture (three accounts:
Agentic ••••X4F1, Individual ••••M7Q5, Roth IRA ••••P0Z9), not a real account. `<Agentic account_number>`
stands for the full value from `get_accounts`, which goes to tools and never into the reply.

## 1. A one-line answer

**User** (Monday 2026-11-16, 8:05 PM ET): "What's my buying power?"

**Calls**
1. `get_accounts {}` → the `agentic_allowed` account ends in X4F1.
2. `get_portfolio {account_number: "<Agentic account_number>"}` → `buying_power.buying_power` "2480.00".

**Reply**
```
Buying power, Agentic ••••X4F1: $2,480.00 (as of 20:05 ET Mon 2026-11-16, market closed; get_portfolio).
```
Why it looks like this: one question, one line. The account is named and masked, the time and session
are stated, and the source is `get_portfolio`, never the `get_accounts` payload.

## 2. An overnight sell ticket

**User** (Monday 2026-11-16, 8:05 PM ET): "Sell 10 PLTR now."

**Calls and reasoning**
1. `get_equity_quotes {symbols: ["PLTR"]}` → last 31.20, bid 31.18, ask 31.24.
2. `rh_time.py session` with the stated time → outside the regular session (`candidate: null`: the kit
   has not verified Robinhood's extended and overnight windows). "Now" at 20:05 ET points to the 24 Hour
   Market, which Robinhood lists from 20:00 ET, so simulate `all_day_hours`, label the window unverified,
   and offer `extended_hours`. A market order would only wait for tomorrow's open.
3. `get_equity_tradability {account_number: "<Agentic account_number>", symbols: ["PLTR"]}` → eligible
   in the 24 Hour Market.
4. `get_portfolio` → $2,480.00. `get_equity_positions` → PLTR 30 shares, all 30 sellable.
   `get_equity_tax_lots {account_number: "<Agentic account_number>", symbol: "PLTR"}` → the oldest lot
   (sold first under FIFO) cost $26.31/share, so this is a sale at a gain and no wash check is needed.
5. "Now" means an immediate fill, and outside regular hours only limits execute, so the price is a
   marketable sell limit **at the bid**, $31.18. A sell limit at the ask ($31.24) would likely rest
   unfilled while the user believed they had sold.
6. `order_lint.py lint` with provenance `quantity: user`, `limit_price: user_intent_marketable` → valid;
   estimate $311.80.
7. `review_equity_order {account_number: "<Agentic account_number>", symbol: "PLTR", side: "sell",
   type: "limit", quantity: "10", limit_price: "31.18", market_hours: "all_day_hours",
   time_in_force: "gfd"}` → `order_checks` `{}`, plus the quote and `market_data_disclosure`.

**Reply**
```
ORDER TICKET: SELL 10 PLTR (simulated with review_equity_order, NOT placed)
Status: CLEAR · Dollars at stake: $311.80 order; about $48.69 realized gain (FIFO lot at $26.31/share)
As of   20:05 ET Mon 2026-11-16 (you said so) · session: outside regular hours → all_day_hours (24 Hour Market; window unverified; say "extended" to re-simulate)
Account Agentic ••••X4F1 (limited margin) · buying power $2,480.00 (get_portfolio)
Order   SELL 10 PLTR · LIMIT $31.18 (marketable: at the bid; ask $31.24) · time in force gfd · all_day_hours
Size    10 of 30 sellable shares (from you)
Est.    $311.80 = 10 × limit (agent estimate; the equity review returns a quote and checks, not a cost)
Robinhood pre-trade checks (order_checks, verbatim):
  > none returned (that does not replace your own check)
Checks run by this kit:
  Wash sale — not needed: the FIFO lot sold ($26.31/share) is at a gain
  Household — not configured (no max_symbol_pct_household in [policy])
  Earnings — next report 2027-02-02 (unverified by company)
  Policy — not configured
Market data disclosure (Robinhood, verbatim):
  > [the review's market_data_disclosure text, exactly as returned]
```
Then handoff (a) from R21, word for word, with ••••X4F1. It begins "**Nothing was placed.**"

## 3. A checked buy with a wash-sale conflict

**User** (Monday 2026-11-16, 8:05 PM ET): "Buy 3 AMD in my agentic account, limit $160."

**Calls and reasoning**
1. `get_equity_quotes {symbols: ["AMD"]}` → 161.40, bid 161.35, ask 161.45. A $160.00 buy limit is below
   the ask, so it is not marketable: it rests until the price comes down to it.
2. `rh_time.py session` → outside the regular session. The user named no session, so simulate the
   default `regular_hours` (the order waits for 09:30 ET Tuesday) and say how to get an overnight version.
3. `get_equity_tradability` and `get_portfolio` → eligible; $2,480.00.
4. A buy of a symbol that may have been sold at a loss in the last 30 days needs the wash check across
   every account; the request names no other account, so the check covers all three. Following
   `references/wash-sweep.md`: the Individual account ••••M7Q5 shows a filled sell of 10 AMD on
   2026-11-03 with a realized loss of $412.00 ($41.20/share), cross-checked against `get_equity_orders`.
5. `wash_sale.py run` in planned-buy mode → conflict: 3 of the 10 loss shares washed, 3 × $41.20 =
   $123.60 disallowed; clean buy date 2026-12-04. The buy is in a taxable account, so the loss is
   deferred into the new shares' basis rather than lost.
6. `get_earnings_results {symbol: "AMD"}` → 2027-01-27, unverified. `[policy]` not configured.
7. `order_lint.py lint` → valid; estimate $480.00. `review_equity_order {account_number:
   "<Agentic account_number>", symbol: "AMD", side: "buy", type: "limit", quantity: "3",
   limit_price: "160.00", market_hours: "regular_hours", time_in_force: "gfd"}` → `order_checks` `{}`.

**Reply**
```
ORDER TICKET: BUY 3 AMD (simulated with review_equity_order, NOT placed)
Status: CONFLICT · Dollars at stake: $123.60 of your AMD loss (••••M7Q5) disallowed now if this fills before 2026-12-04 · $480.00 order
As of   20:05 ET Mon 2026-11-16 (you said so) · session: outside regular hours → regular_hours (waits for 09:30 ET Tue; say "overnight" for all_day_hours)
Account Agentic ••••X4F1 (limited margin) · buying power $2,480.00 (get_portfolio)
Order   BUY 3 AMD · LIMIT $160.00 (below the ask $161.45: rests until reached) · time in force gfd · regular_hours
Size    3 shares (from you)
Est.    $480.00 = 3 × limit (agent estimate; the equity review returns a quote and checks, not a cost)
Robinhood pre-trade checks (order_checks, verbatim):
  > none returned (that does not replace your own check)
Checks run by this kit:
  Wash sale — CONFLICT · read ••••X4F1 ••••M7Q5 ••••P0Z9 · ••••M7Q5 sold 10 AMD on 2026-11-03 at a $41.20/share loss.
              Buying 3 shares within 30 days washes 3 × $41.20 = $123.60, deferred into the new shares' basis.
              First clean buy date: 2026-12-04. Not evaluated: other brokers, spouse accounts, substantially identical tickers.
  Household — not configured (no max_symbol_pct_household in [policy])
  Earnings — next report 2027-01-27 (unverified by company)
  Policy — not configured
Market data disclosure (Robinhood, verbatim):
  > [the review's market_data_disclosure text, exactly as returned]
```
Then handoff (a) from R21 with ••••X4F1. What the reply does not do: it doesn't tell the user to wait,
resize or cancel. It states the rule, the dollars and the date; the decision is theirs.
