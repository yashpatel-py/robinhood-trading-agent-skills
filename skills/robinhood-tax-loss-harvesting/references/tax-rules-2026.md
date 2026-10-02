# Tax rules this skill applies (as of 2026-09-22)

*Unofficial — not affiliated with Robinhood. Not tax advice.* The kit computes rules, dates and dollars from the user's Robinhood data; the user (and their tax professional) decides.

**Dated and re-verified every November.** Written 2026-09-22. Next re-verification: on or after **2026-11-01**, before the tax-season relaunch, and again each November. If today is past the re-verify date and this file has not been updated, say so when you quote any rule below.

## Sources

| Key | Source | Used for |
|---|---|---|
| Pub 550 | IRS Publication 550, *Investment Income and Expenses*, "Wash Sales" and "Holding Period" sections — https://www.irs.gov/publications/p550 | the 61-day window, replacement stock and options, basis and holding-period adjustment, spouse rule, holding period, trade date |
| Topic 409 | IRS Tax Topic No. 409, *Capital Gains and Losses* — https://www.irs.gov/taxtopics/tc409 | the $3,000 ($1,500 married filing separately) net-loss limit and carryover |
| §1091 | 26 U.S.C. §1091 (the wash-sale statute) | "stock or securities", "contract or option to acquire" |
| RR 2008-5 | Rev. Rul. 2008-5, 2008-3 I.R.B. 271 | IRA and Roth purchases disallow the loss, with no basis increase |
| Notice 2014-21 | IRS Notice 2014-21 (virtual currency is property) | why crypto held directly is outside §1091 |
| RR 85-87 | Rev. Rul. 85-87 | a deep-in-the-money put sold in the window can be an option to acquire |
| RR 56-602 | Rev. Rul. 56-602 | shares bought in the same block as the shares sold (treated as "possible"; confirm) |
| RR 66-7 | Rev. Rul. 66-7, 1966-1 C.B. 188 | holding period by calendar months: an asset acquired on the last day of a month is held "more than" N months only from the first day of the (N+1)th following month |
| RH wash | Robinhood Support, "Wash sales" — https://robinhood.com/us/en/support/articles/wash-sales/ | per-account reporting; tracking across accounts is the customer's responsibility |
| RH lots | Robinhood Support, "Tax lots" — https://robinhood.com/us/en/support/articles/tax-lots/ | FIFO default; the app's tax-lot selector for self-directed accounts |
| UN | the maintainers' user-needs research (2026-09-22), citing CoinLedger, coselite, Count On Sheep and CoinTracking | crypto status and the 2026-06-08 bill; 1099-DA basis reporting |

## 1. The wash-sale rule (§1091; Pub 550)

- A loss on a sale of stock or securities is **disallowed** if, within 30 days before or after the sale, you buy (or acquire a contract or option to buy) substantially identical stock or securities.
- **The window is 61 calendar days**: 30 before the sale date, the sale date, 30 after. Weekends and market holidays count like any other day; they never extend the window.
- **Trade dates, not settlement dates.** The sale date and the buy dates are trade dates.
- **A partial wash** disallows only the loss on as many shares as were replaced. Replacement shares are matched in the order they were acquired, earliest first, and lots sold together are matched in the order they were acquired, earliest acquired first (Treas. Reg. §1.1091-1(b)–(c)): a partial wash lands on the oldest loss lot, not the biggest loss. A buy that washed one loss cannot wash another (Treas. Reg. §1.1091-1(e)), so earlier loss sales of the same stock have first claim on the buys in their own window. Only when a sold lot's acquisition date is missing is the order unknown: then the kit reports the most that can be disallowed (largest loss per share first) with the smallest figure next to it.
- **Deferred, not lost (taxable replacements).** The disallowed loss is added to the basis of the replacement shares, and their holding period includes the holding period of the shares sold (Pub 550). The loss comes back when the replacement shares are sold (unless that sale is washed too).
- **Your spouse's purchases count** (Pub 550). The kit cannot see accounts outside the user's Robinhood login, so spouse accounts are always "not evaluated".
- **Substantially identical** has no bright-line test. Stock of one company is generally not substantially identical to stock of another; two funds tracking the same index are an open question. The kit never decides it. It flags only the same ticker, options on the same stock, and tickers the user declared in `[tax] related_tickers`.
- **Options.** Buying a call on the same stock within the window is acquiring an option to buy it, so it can wash the loss. Selling a deep-in-the-money put can be treated the same way (RR 85-87). The kit lists option orders on the same underlying as "possible" and does not evaluate them.
- **Same block.** Shares bought in the same purchase as the shares sold are generally not replacement shares (RR 56-602). The kit shows such leftovers as "possible" rather than clear, so a tax professional can confirm.

## 2. IRA and Roth purchases (RR 2008-5)

- If you sell at a loss in a taxable account and your IRA or Roth IRA buys substantially identical stock within the window, the loss is **disallowed**, and — unlike a taxable replacement — your basis in the IRA is **not** increased. The loss is **permanently** gone.
- Losses realized **inside** an IRA or Roth are not deductible at all, so a sale inside a retirement account is outside the rule, and its gains and losses are not part of the year's taxable totals.
- This is the costliest trap the kit looks for: a recurring IRA contribution buying the same ETF, or an agent buying in the Roth, erases a harvest in a taxable account, and neither 1099 shows it.

## 3. How Robinhood reports it

- Robinhood reports wash sales **per account**; each account has its own 1099, and Robinhood states that tracking wash sales across accounts is the customer's responsibility (RH wash).
- So a wash between two Robinhood accounts (or with an IRA) appears on **no** 1099. The taxpayer reports the adjustment on Form 8949 (adjustment code W).
- Self-directed accounts sell first-in, first-out unless a lot is chosen; the app's tax-lot selector chooses specific lots (RH lots). Robinhood's automatic tax-loss harvesting exists only in managed Robinhood Strategies accounts (UN).
- Through the connector, specific lots are named with `tax_lots` on `review_equity_order`, which only accepts the Agentic account. Every other account gets a manual ticket for the app's selector.

## 4. Holding period (Pub 550)

- The holding period starts the **day after** the acquisition trade date and includes the day of sale.
- **Long-term = held more than one year.** A lot is long-term when sold on or after the day after its one-year anniversary: bought 2025-10-03 → long-term from 2026-10-04; bought 2025-12-03 → long-term from 2026-12-04 (a sale on 2026-12-03 is still short-term).
- **Acquired on the last day of a month** (whatever the month's length): long-term from the **first day of the 13th month after it** (RR 66-7). For most months that is the same day as above; at the end of February it is not. Bought 2027-02-28 → long-term from **2028-03-01**, so a sale on the leap day 2028-02-29 is still short-term. Bought 2024-02-29 → long-term from **2025-03-01**.
- Robinhood's own `term` on each lot is shown next to the kit's arithmetic. When they disagree, Robinhood's tax documents govern; say so.
- **Replacement lots.** A lot bought as the replacement in a wash takes the sold shares' holding period and the deferred loss in its basis (§1). Robinhood does not track washes across accounts (§3), so for a replacement bought in a different account from the loss sale, Robinhood's lot shows neither: its term can read short-term for a lot that is long-term, and its loss is understated.

## 5. Using losses (Topic 409; Pub 550)

- Short-term losses offset short-term gains first; long-term losses offset long-term gains first; then the net short and net long results offset each other.
- If total losses exceed total gains, up to **$3,000** of net capital loss (**$1,500** if married filing separately) offsets other income each year; the rest **carries forward** to later years with no expiry.
- The connector does not split realized gains into short and long term (neither `get_pnl_trade_history` nor `get_realized_pnl` carries a term field), so the kit shows the split only for open lots and points to Robinhood's tax center for realized trades.

## 6. Year-end dates for 2026

| Date | What it means |
|---|---|
| **Tue 2026-12-01** | "Look-back" starts: any buy from Dec 1 onward is within 30 days before a Dec 31 sale, so it can wash a year-end loss (2026-12-31 − 30 = 2026-12-01). |
| **Thu 2026-12-31** | Last trading day of 2026 (NYSE open; Jan 1 is a holiday). The **trade date** decides the tax year: a sale on Dec 31 counts in 2026 even though it settles on Mon 2027-01-04. Guides that say a trade must *settle* by Dec 31 are wrong for exchange-traded stock (Pub 550). |
| **Sun 2027-01-31** | First calendar day a buy is clear of a 2026-12-31 loss sale (Dec 31 + 31). The market is closed, so the first trading day to buy back is **Mon 2027-02-01**. |
| **Fri 2027-01-01** | First clear day after a 2026-12-01 loss sale; it is a market holiday, so the first trading day is **Mon 2027-01-04**. |

Settlement is T+1 on the NYSE calendar, skipping bank holidays (Columbus Day and Veterans Day are market days but not settlement days). It never moves the tax year or the wash-sale window.

## 7. Crypto (status as of 2026-09-22; uncertain; re-verify)

- §1091 covers "stock or securities". The IRS treats virtual currency as **property** (Notice 2014-21), so under current law the wash-sale rule **does not apply** to crypto held directly (for example BTC or ETH in the Robinhood crypto account).
- Congress has repeatedly proposed extending §1091 to digital assets. A bill introduced on **2026-06-08** would do so; as of 2026-09-22 it is **not law** (UN). A change could arrive with its own effective date. Re-verify on or after 2026-11-01 and every November; if the law changed, update this section, `wash_sale.py`'s `CRYPTO_NOT_SUBJECT` message and the skill before relying on it.
- Some practitioners caution that general doctrines (economic substance) might be applied to a sale and immediate rebuy with no change in position. That is unsettled; say it is unsettled.
- Crypto **gains and losses still count** toward the year's capital gains. 2026 is the first year brokers report crypto **cost basis** to the IRS on Form 1099-DA, for "covered" lots bought on or after 2026-01-01 at the same broker (UN).
- Specific-lot crypto sells are not possible through the connector: `get_crypto_tax_lots` is not exposed. The user chooses crypto lots in the app, if at all.
- **Crypto ETFs and ETPs** (listed shares) are securities for this kit's purposes and are swept like any stock; some are grantor trusts taxed as if the coin were held, which makes their status arguable. The kit treats them as subject (conservative) and says so.

## 8. What the kit never does

- File returns, fill in forms, or give a tax opinion.
- Decide whether two different securities are substantially identical.
- Say "you should harvest", "sell this lot" or "this is the best choice". It shows the arithmetic side by side and leaves the decision to the user.
- Treat an unread account, an unread page or an unconfirmed trade-history row as clear.
