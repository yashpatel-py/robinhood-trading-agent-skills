# Worked examples

*Unofficial — not affiliated with Robinhood.* Illustrative numbers from the kit's synthetic test
fixture (AMD at $161.40, PLTR at $31.20, NVDA at $228.10, an Agentic account worth $4,100.00 with
$2,480.00 of buying power and 3 open option position rows). Not live data, and not suggested
settings: every threshold below is a stand-in for a value the user chose.

## Contents
- [1. A pass with candidates](#1-a-pass-with-candidates)
- [2. A debit spread card](#2-a-debit-spread-card)
- [3. Stopped: criteria unset](#3-stopped-criteria-unset)
- [4. Zero candidates, then "relax whatever you need"](#4-zero-candidates-then-relax-whatever-you-need)
- [5. Quotes without delta or open interest](#5-quotes-without-delta-or-open-interest)
- [6. Preflight stop: a cash account wanting spreads](#6-preflight-stop-a-cash-account-wanting-spreads)
- [7. The ticket after a review](#7-the-ticket-after-a-review)

## 1. A pass with candidates

User config (theirs): `long_call`, DTE 14–45, |delta| 0.30–0.60, spread ≤ 5%, open interest ≥ 100,
≤ $700.00 per contract, ≤ 30% of the account, $200.00 reserve, max 5 open, price $20–$700,
average volume ≥ 1,000,000, TSLA blocklisted, earnings `avoid` with a 2-day buffer, IV rank OFF,
`entry_price_rule = "mid"`, 1 contract; exits 50 / 40 / 7 / 30. Tuesday 2026-11-17, 11:02 AM ET
(regular session).

```
POSSIBLE: 3 candidates matched your criteria (nothing placed) · Dollars at stake: $630.00 AMD 2026-12-18 165C, $440.00 AMD 2026-12-18 170C, $205.00 PLTR 2026-12-18 32C (max loss per entry at the natural price)

Long calls in your Agentic account (••••X4F1), quotes as of 11:01 AM ET, regular session.

### 1. AMD 2026-12-18 165C · $630.00 (15.4% of account)
Underlying   $161.40 (as of 11:01 ET)        Breakeven  $171.30 (at the natural price)
Contract     $6.10 / $6.30 (mid 6.20)          Required move  +6.1% in 31 days
Spread       3.2%                              |Delta| 0.45 · OI 1,200 · DTE 31
Max loss     $630.00 (100% of premium)         Max gain  unlimited
Earnings     none in 2026-11-15..2026-12-20 (next listed 2027-01-27 pm, unverified)
Limit        $6.20 (your rule: mid, rounded to the 0.05 tick) · contracts 1
Exits per your rules:  target $9.30 (+50%) · stop $3.72 (−40%)
                       time stop 2026-12-11 (7 DTE) · hard exit 2026-12-17 (30 days)
Flags        none

### 2. AMD 2026-12-18 170C · $440.00 (10.7% of account)
… required move +8.1%, breakeven $174.40, spread 4.7%, |delta| 0.36, OI 900, limit $4.30 …

| # | Candidate | Cost | Breakeven | Required move | Spread | Limit |
|---|---|---|---|---|---|---|
| 3 | PLTR 2026-12-18 32C | $205.00 (5.0%) | $34.05 | +9.1% | 5.0% | $2.00 |

Rejected (a contract can fail several rules):
| Reason | Count | Example |
|---|---|---|
| Bid/ask spread above your 5% | 2 | AMD 2026-12-18 180C: 16.2% |
| Delta outside 0.30–0.60 | 1 | AMD 2026-12-18 180C: 0.19 |
| Delta or open interest missing from the quote | 1 | PLTR 2026-12-18 35C |
| Cost per contract above $700.00 | 1 | NVDA 2026-12-18 230C: $1,220.00 |
Underlyings: TSLA is on your blocklist.

Sorted by the move the stock still needs, in the trade's direction, to reach breakeven by
expiration, smallest first; candidates already past breakeven come first. This is a disclosed
ordering, not a ranking of quality; change sort_by in your config. Average volume from
the scanner's <filter name> (<its lookback>). Evidence, not a recommendation. Every threshold above
is yours. Pick one and I'll simulate it with Robinhood's review tool.
```

## 2. A debit spread card

Same account, `debit_call_spread`, widths $5–$10, spread ≤ 8%, ≤ $300.00 per spread,
`entry_price_rule = "natural"`.

```
### 2. AMD 2026-12-18 165/170C debit call spread · $210.00 (5.1% of account)
Underlying   $161.40 (as of 11:01 ET)        Breakeven  $167.10 (at the natural price)
Legs         buy 165C $6.10 / $6.30 · sell 170C $4.20 / $4.40
Net debit    $2.10 natural (long ask − short bid)   Required move  +3.5% in 31 days
Spread       4.7% (wider leg)                  |Delta| 0.45 (leg bought) · OI 1,200 / 900
Max loss     $210.00                           Max gain  $290.00
Width        $5.00 · risks $210.00 to make at most $290.00 ($1.38 per $1 risked)
Limit        $2.10 (your rule: natural) · spreads 1
Exits per your rules:  target $3.15 (+50%) · stop $1.26 (−40%)
                       time stop 2026-12-11 (7 DTE) · hard exit 2026-12-17 (30 days)
```

The same pass rejected `165/175C` because the 175 call's open interest (60) is below the user's 100:
a clean long leg paired with an illiquid short leg is an unexitable position.

An in-the-money spread can already be past breakeven. `155/160C` at $2.80 net breaks even at $157.80
with AMD at $161.40, so the script sets `past_breakeven: true` and the card's move line is its
`move_text`, not a signed move:

```
Net debit    $2.80 natural (long ask − short bid)   Breakeven already passed: the stock can fall 2.2% by expiration (31 days) before the position loses money
```

"Required move −2.2%" there would read as a bullish trade needing a fall. It sorts ahead of a spread
that still needs a rise.

## 3. Stopped: criteria unset

```
STOPPED: 3 criteria UNSET: delta_min, delta_max, dte_max

Your screener config (~/.config/robinhood-skills/config.toml) has no value yet for:
- options.criteria.delta_min, options.criteria.delta_max: the |delta| band for the contract you buy
- options.criteria.dte_max: the longest expiration you want, in days

These are your decisions; I won't fill them in. Tell me the values and I'll show you the change
before saving it, or set them yourself in the file.
```

No chain was read and nothing was simulated.

## 4. Zero candidates, then "relax whatever you need"

```
NO ACTION: 0 of 6 contracts passed your criteria · Dollars at stake: none found

| Reason | Count | Example |
|---|---|---|
| Cost per contract above $150.00 | 5 | AMD 2026-12-18 165C: $630.00 |
| Bid/ask spread above your 5% | 2 | AMD 2026-12-18 180C: 16.2% |
…
```

User: "Nothing again? Relax whatever you need so I get one idea."

Right answer (evidence and a question, no values):

> The rule doing most of the filtering is your $150.00 cost-per-contract cap: 5 of 6 contracts were
> above it (the lowest of those costs $200.00). Spread % excluded 2. Which limits do you want to
> change, and to what? I'll show you the change before saving it. I won't pick new values, because
> those numbers are your risk limits.

Wrong answers: "I raised the cap to $250 and found one", "try $300", "most people use 10%".

## 5. Quotes without delta or open interest

```
UNKNOWN: 0 candidates; 3 of 6 contracts could not be checked (DELTA_OI_UNAVAILABLE) · Dollars at stake: none found

Robinhood's quotes for these contracts came back without delta or open interest, so your delta and
open-interest rules could not be applied. They were rejected with DELTA_OI_UNAVAILABLE, not
assumed to pass. Re-run later; if it persists, the connector may have changed (report it).
```

## 6. Preflight stop: a cash account wanting spreads

```
STOPPED: debit_put_spread needs a margin or limited-margin account; this is a cash account.

Your Agentic account (••••X4F1) is a cash account with options level 2. Spreads need level 3, which
needs margin or limited margin. The order is:
1. Limited-margin upgrade (lets you trade with unsettled funds; no borrowing or leverage). I can get
   you the link.
2. After you finish it, I re-read your account.
3. Only if the level is still below 3: the options-level application.

Cash-account note: option trades settle T+1; buying again with unsettled proceeds risks a
good-faith violation, and 5 in 12 months trigger a 90-day restriction.
```

## 7. The ticket after a review

The user picked candidate 1 during the regular session. Fresh quotes still passed every rule.
`review_option_order` was called with `account_number` (full value), `legs: [{option_id,
side: "buy", position_effect: "open", ratio_quantity: 1}]`, `quantity: "1"`, `type: "limit"`,
`price: "6.20"`, `time_in_force: "gfd"`, `market_hours: "regular_hours"`, `chain_symbol: "AMD"`,
`underlying_type: "equity"`.

```
Simulated ticket 7f3a1c · AMD 2026-12-18 165C · Agentic ••••X4F1
Buy to open 1 contract · limit $6.20 (your rule: mid) · good for day · regular hours
Cost: <fees and collateral as the review returned them, labeled broker-computed>, or
      $620.00 agent estimate — broker did not compute (limit × 100 × 1)
Pre-trade checks: <every check exactly as the review returned it, or "none returned">
<any disclosure the review returned, verbatim and unmodified>

Nothing was placed. I simulated this order against your Agentic account (••••X4F1) with
Robinhood's review tool. …(the rest of the R21 (a) text)…
```

The review's response shape for options was not part of the 2026-09-22 capture, so show whatever it
returns verbatim rather than mapping it into fixed fields.
