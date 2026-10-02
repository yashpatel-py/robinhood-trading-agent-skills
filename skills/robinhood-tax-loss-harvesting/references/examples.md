# Worked examples

*Unofficial — not affiliated with Robinhood.* Sandbox household, not a real account: Agentic ••••X4F1 (taxable), Individual ••••M7Q5 (taxable), Roth IRA ••••P0Z9 (retirement). "Today" is Monday 2026-11-16, 8:05 PM ET (outside the regular session). Every figure below is script output.

## 1. "Harvest my TSLA loss." — a permanent IRA wash found on page 2

Calls: `get_accounts {}` → consent → `wash_sale.py window` (window 2026-10-17 → 2026-12-16; `created_at_gte` 2026-07-19T04:00:00Z) → for each account `get_equity_orders {account_number, symbol: "TSLA", created_at_gte}`. The Roth's page 1 holds three unfilled orders; its `next` URL leads to page 2 with the filled buy of 5 TSLA on 2026-11-06. Then positions, `get_equity_tax_lots` for ••••M7Q5 and ••••P0Z9, option chains and orders (none), `get_equity_quotes {symbols: ["TSLA"]}` → 262.00.

Lots in ••••M7Q5: L1 2025-03-10 20 @ 280.00 (long-term), L7 2026-06-02 20 @ 340.00 (short-term). The user wants to harvest the short-term lot.

`wash_sale.py run` (sale, L7, price 262.00) returns:

```
CONFLICT: Dollars at stake: $390.00 of TSLA loss permanently disallowed if sold before 2026-12-07
WASH-SALE CHECK: sell 20 TSLA (lot 2026-06-02) in Individual ••••M7Q5, planned 2026-11-16 · quotes as of 20:05 ET (regular session closed)
Accounts read: ••••X4F1 Agentic (taxable) · ••••M7Q5 Individual (taxable) · ••••P0Z9 Roth IRA (retirement)   Not read: none
Window: 2026-10-17 → 2026-12-16
CONFLICT: Roth IRA ••••P0Z9 bought 5 TSLA on 2026-11-06 → 5 of 20 shares washed → $390.00 disallowed, PERMANENTLY (IRA purchase)
Clean: 15 shares ($1,170.00 of loss) · earliest clean sale date 2026-12-07 (if no new buys) · don't buy back in any account until 2026-12-17
Possible: none · Not evaluated: other brokers, spouse accounts, future recurring/DRIP settings beyond history, different tickers you consider equivalent, basis and holding-period adjustments on replacement lots from earlier washes
Lot choice (you decide): FIFO sells the 2025-03-10 lot (long-term, −$360.00) · highest cost sells the 2026-06-02 lot (short-term, −$1,560.00)
Ticket: Individual accounts can't be simulated by agents. Manual ticket → in the app's tax-lot selector choose: acquired 2026-06-02, 20 sh @ $340.00
```

Then handoff R21(b) and the closing line. The arithmetic: loss per share 340.00 − 262.00 = 78.00; 5 × 78.00 = 390.00; 15 × 78.00 = 1,170.00; 2026-11-06 + 31 days = 2026-12-07; 2026-11-16 + 31 = 2026-12-17.

Note what the reply does **not** say: "you should sell", "wait until December 7", or which lot is better. It gives the dates and dollars; the user chooses.

## 2. "Buy 3 AMD in my agentic account, limit $160." — a rebuy that washes

••••M7Q5 sold 10 AMD on 2026-11-03. The trade history also shows a −$75.00 AMD row on 2026-11-09 at a price of 3.30: that is an option close listed under the underlying, and no filled equity sell matches it.

`wash_sale.py run` (planned_buy, 3 shares, with the orders and trade-history rows) returns:

```
CONFLICT: Dollars at stake: $123.60 of AMD loss deferred into replacement basis if bought before 2026-12-04
Loss sale: ••••M7Q5 sold 10 AMD on 2026-11-03 (−$412.00, $41.20/share). This buy would wash 3 shares → $123.60 disallowed (deferred into the new shares' basis, not lost)
Unknown: an AMD loss row on 2026-11-09 (−$75.00) does not match any filled stock sale (it looks like an option close); if it were a share sale, the clean date would be 2026-12-10
Clean buy date: 2026-12-04 (a Friday). This buy also opens its own window: an AMD loss sale in any taxable account through 2026-12-16 would be washed by it.
```

The review still runs (the user asked for the ticket), and the ticket shows this check under "Checks run by this kit", followed by "Nothing was placed" and handoff R21(a).

## 3. "Can I harvest VOO?" — a recurring buy

••••M7Q5 holds a VOO lot 2026-07-15 5 @ 560.00 (loss $20.00/share at 540.00) and buys 1 VOO on a recurring schedule (2026-09-01, 2026-10-01, 2026-11-02; the Nov 1 buy moved to Monday Nov 2).

```
CONFLICT: Dollars at stake: $20.00 of VOO loss deferred into replacement basis; no clean sale date while the recurring buy continues
CONFLICT: ••••M7Q5 recurring buy of 1 VOO on 2026-11-02 → 1 of 5 shares washed → $20.00 disallowed (deferred)
Possible: recurring buy expected about 2026-12-01 (inferred from history; the connector cannot see your schedule) → up to $20.00 more
Earliest clean sale date: none while the monthly buy continues (it puts a buy within 30 days of every date); with no new buys it would be 2026-12-03. Pausing it is your decision, in the app.
```

## 4. "If I sold my NVDA lots now, what's short vs long term?"

`holding_period.py run` on ••••M7Q5's lots at 228.10:

```
NO ACTION: Dollars at stake: $1,864.00 of short-term gain in 1 lot(s) turns long-term within 45 days (next: NVDA on 2026-12-04, 18 days)
2025-03-10 · 100 sh @ 118.00 · long-term since 2026-03-11 · +$11,010.00
2025-12-03 · 40 sh @ 181.50 · short-term until 2026-12-04 (a Friday; 18 days) · +$1,864.00
```

A sale on 2026-12-03 is still short-term: long-term means more than one year, counted from the day after the purchase.

## 5. "If I harvest a loss on Dec 31, 2026, when can I buy it back?"

`wash_sale.py window {"date": "2026-12-31"}`: window 2026-12-01 → 2027-01-30; do not buy until 2027-01-31, a Sunday, so the first trading day is **Monday 2027-02-01**. The trade date decides the tax year: the Dec 31 sale counts in 2026 although it settles Monday 2027-01-04. From Dec 1 onward, a buy of the same stock in any account already washes a Dec 31 loss.

## 6. "What are my realized gains this year?"

`get_pnl_trade_history {account_number: <rhs VALUE>, span: "ytd", cursor}` and `get_realized_pnl` per account, then `realized_summary.py run`:

```
NO ACTION: Dollars at stake: +$8,412.33 realized year to date in taxable accounts (••••P0Z9 +$1,204.00 kept separate)
••••M7Q5 Individual: +$7,950.10 (MU +$8,362.10 · AMD −$412.00) · reconciles with get_realized_pnl
••••X4F1 Agentic: +$462.23
Roth IRA ••••P0Z9: +$1,204.00, not taxable this year, excluded
Short/long-term split: not provided by the connector; see Robinhood's tax center
These figures are before any wash-sale adjustment between accounts, which no 1099 shows.
```

## 7. "I sold ETH at a loss yesterday. Can I rebuy today without a wash sale?"

No sweep. The answer: under current law as of 2026-09-22 the wash-sale rule applies to stock and securities, and crypto held directly is treated as property, so it does not apply to ETH; a bill introduced 2026-06-08 would change that but is not law, and the kit re-verifies this every November; some practitioners caution that an immediate rebuy with no change in position could still be challenged under general doctrines, which is unsettled. The loss still counts toward this year's capital gains. An ETH ETF would be a different case (a listed security). Whether to rebuy is the user's decision; the order itself would be a core-skill preview.

## 8. Traps the scripts catch

- **UTC after midnight.** A buy with `last_transaction_at` 2026-12-17T00:30:00Z traded at 19:30 ET on **2026-12-16**, inside a window ending 2026-12-16. Read as a UTC date it would look one day outside, a false clear.
- **The overnight session.** A fill at 21:30 ET on 2026-12-16 may carry the next trading day's trade date (2026-12-17). One candidate is inside the window and one outside, so it is "possible", not clear.
- **A GTC created before the window.** A buy created 2026-09-30 and filled 2026-10-20 is inside the window; the orders call finds it only because `created_at_gte` looks back 90 days before the window start.
- **A cancelled order with a partial fill.** `state: cancelled`, `cumulative_quantity: "2"` is a buy of 2 shares.
- **A lot that is the sale itself.** Selling the lot bought 2026-11-10 at a loss on 2026-11-16: its own purchase is not a replacement.
