# Example: a harvest check that reads page 2 of a Roth IRA

> **Sandbox data.** Every account, price and order on this page is synthetic. It comes from the
> fixture household in `evalkit/fixtures/household.json`, served by `sandbox/mock_server.py`
> (variant `base`), the same data the evals use. Unofficial; not affiliated with Robinhood
> Markets, Inc. Not investment or tax advice.

**Skill:** `robinhood-tax-loss-harvesting` · **Stated time:** Mon 2026-11-16, 8:05 PM ET (after the close) · **How it was
made:** the tool calls below were sent to the sandbox over MCP, and the numbers were computed by
running the skill's bundled scripts on those responses. Only the wording of the answer was written
by hand, following the skill's output template. Account numbers are masked the way the skill masks
them (`••••` plus the last 4); the tools received the full values.

## The request

> Harvest my TSLA loss in my individual account: the 20 shares I bought in June.

No config file, so the tax request itself is the read consent for every account (wash-sweep step 0),
and the answer says which accounts were read and that the data went to the AI provider.

## What the agent called

Script first: `wash_sale.py window {"date": "2026-11-16", "gtc_lookback_days": "90"}` → window
**2026-10-17 → 2026-12-16**, `created_at_gte` **2026-07-19T04:00:00Z** (midnight ET, 90 days before the window starts, in UTC),
`drip_created_at_gte` 2025-10-12T04:00:00Z.

| # | Call | What came back (abridged) |
|---|---|---|
| 1 | `get_accounts {}` | 3 accounts: Agentic ••••X4F1 (individual, taxable), ••••M7Q5 (individual, taxable), Roth IRA ••••P0Z9 (`brokerage_account_type` "ira_roth": retirement) |
| 2 | `get_equity_orders {account_number: ••••X4F1, symbol: "TSLA", created_at_gte: "2026-07-19T04:00:00Z"}` | 0 TSLA orders over 1 page |
| 3 | `get_equity_orders {account_number: ••••M7Q5, symbol: "TSLA", created_at_gte: "2026-07-19T04:00:00Z"}` | 0 TSLA orders over 1 page |
| 4 | `get_equity_orders {account_number: ••••P0Z9, symbol: "TSLA", created_at_gte: "2026-07-19T04:00:00Z", cursor}` | 4 TSLA orders over 2 pages: page 1 = 2026-11-12 cancelled (0 filled), 2026-11-10 rejected (0 filled), 2026-11-09 cancelled (0 filled); page 2 = the filled buy of 5 TSLA, last transaction 2026-11-06T15:05:00Z, `placed_agent` "user" |
| 5 | `get_equity_positions {account_number}` × 3 | TSLA held in ••••M7Q5 (40 sh) and ••••P0Z9 (5 sh) |
| 6 | `get_equity_tax_lots {account_number: ••••M7Q5, symbol: "TSLA"}` | 2026-06-02: 20 sh @ 340.00, short_term; 2025-03-10: 20 sh @ 280.00, long_term |
| 7 | `get_equity_tax_lots {account_number: ••••P0Z9, symbol: "TSLA"}` | 2026-11-06: 5 sh @ 270.00, short_term |
| 8 | `get_equity_orders {account_number, symbol: "TSLA", placed_agent: "drip", created_at_gte: "2025-10-12T04:00:00Z"}` × 3 | no dividend-reinvestment buys (0 rows); no recurring TSLA buys in the step-2 rows |
| 9 | `get_option_chains {underlying_symbol: "TSLA"}` | 1 chain |
| 10 | `get_option_orders {account_number: ••••X4F1, chain_ids, created_at_gte}` | 2 TSLA option orders: a 250P bought to open on 2026-09-28 and sold to close on 2026-10-15, both before the window |
| 11 | `get_option_orders {account_number: ••••M7Q5, chain_ids, created_at_gte}` | 0 TSLA option orders since 2026-07-19 |
| 12 | `get_equity_quotes {symbols: ["TSLA"]}` | last 262.0000 · bid 261.9500 · ask 262.0500 (20:04 ET) |

Then `wash_sale.py run` (mode `sale`: 20 sh of the 2026-06-02 lot at $340.00, sold at $262.00 on
2026-11-16 in ••••M7Q5), with every raw order page, lot and option row above, and
`lot_select.py compare` on the two ••••M7Q5 lots (sell 20 at $262.00).

| Script output | Value |
|---|---|
| `status` | `conflict` |
| loss on the lot | 20 sh × $78.00 = $1,560.00 |
| conflict | ••••P0Z9 (retirement) bought 5 sh on 2026-11-06, `placed_agent` "user", order `730cf8…` → 5 sh washed |
| `disallowed_total_usd` / `permanent_total_usd` | $390.00 / $390.00 |
| `clean_shares` / `allowed_loss_usd` | 15 / $1,170.00 |
| `earliest_clean_sale_date` | 2026-12-07 (assumes no new buys; recurring/DRIP may re-wash) |
| `do_not_buy_until` | 2026-12-17 |
| `possible`, `unknowns` | none, none |

## The answer

```text
CONFLICT: Dollars at stake: $390.00 of TSLA loss permanently disallowed if sold before 2026-12-07
WASH-SALE CHECK: sell 20 TSLA (lot 2026-06-02) in Individual ••••M7Q5, planned 2026-11-16 · quotes as of 20:04 ET
Accounts read (this data went to your AI provider): ••••X4F1 Agentic (taxable) · ••••M7Q5 Individual (taxable) · ••••P0Z9 Roth IRA (retirement)   Not read: none
Window: 2026-10-17 → 2026-12-16
CONFLICT: Roth IRA ••••P0Z9 bought 5 TSLA on 2026-11-06 → 5 of 20 shares washed → $390.00 disallowed, PERMANENTLY (IRA purchase)
Clean: 15 shares ($1,170.00 of loss) · earliest clean sale date 2026-12-07 (if no new buys) · don't buy back in any account until 2026-12-17
Possible: none · Not evaluated: other brokers, spouse accounts, future recurring/DRIP settings beyond history, different tickers you consider equivalent, basis and holding-period adjustments on replacement lots from earlier washes
Lot choice (you decide; a display order, not a recommendation):
  FIFO, Robinhood's default when no lot is chosen: sells the 2025-03-10 lot (long-term, −$360.00)
  Highest cost / losses first: sells the 2026-06-02 lot (short-term, −$1,560.00)
  Selling 20 shares without choosing a lot sells the 2025-03-10 lot, not the June lot.
Ticket: Individual accounts can't be simulated by agents.
Nothing was placed. Agents can't place or simulate orders in your Individual account (••••M7Q5); it is
read-only to them. To act on this, enter the order in the Robinhood app for that account. In its tax-lot
selector, choose: acquired 2026-06-02, 20 sh @ $340.00.
What this is: rule arithmetic on your Robinhood data. What it isn't: tax advice or a "substantially identical" judgment.
```

## Why it went this way

- **The Roth IRA's buy was on page 2.** Its first page held three TSLA orders that never filled (two
  cancelled, one rejected). A sweep that stopped at page 1 would have reported "clear". The skill reads
  every page until `next` is null, with no `state` filter: a filter such as `state: "filled"` would also
  drop partially filled buys, which count too.
- **A buy in an IRA makes the loss disappear.** A wash sale normally defers the loss into the replacement
  shares' cost basis. When the replacement is bought in an IRA or Roth, the loss is not added to the IRA's
  basis (Rev. Rul. 2008-5), so the $390.00 is gone for good. Robinhood's per-account 1099s would not show
  it: the sale is in one account and the buy in another.
- **The earliest clean sale date comes from the buy, not the sale.** Selling on or after 2026-12-07 (the
  Roth buy on 2026-11-06 + 31 days) avoids this conflict, as long as nothing else buys TSLA in the
  meantime. Buying TSLA back in any account before 2026-12-17 would wash the loss again.
- **The lot matters as much as the date.** Robinhood sells first-in, first-out unless you pick lots. Here
  FIFO would sell the March 2025 lot (a $360.00 long-term loss) instead of the June lot (a $1,560.00
  short-term loss). The skill lists the choices side by side and does not pick one.
- **No ticket was simulated.** `review_equity_order` works only in the Agentic account; the lots are in
  the Individual account, so the handoff is a manual ticket with the exact lot to choose.
- **Crypto, if you asked:** the wash-sale rule does not apply to crypto held directly under current law
  (as of 2026-09-22); `wash_sale.py` refuses crypto symbols with `CRYPTO_NOT_SUBJECT`.

## Reproduce it

```sh
git clone https://github.com/yashpatel-py/robinhood-trading-agent-skills && cd robinhood-trading-agent-skills
claude --plugin-dir . --mcp-config sandbox/mcp.json --strict-mcp-config
```

`--plugin-dir .` loads the skills and hooks from the clone, and `--strict-mcp-config` makes the
sandbox the only MCP server in the session. Start the request with "(Context: it is Monday
2026-11-16, 8:05 PM ET.)" so the skill uses the fixture's clock. The model's wording will differ from
run to run; the figures will not, because they come from the fixture and the scripts.
