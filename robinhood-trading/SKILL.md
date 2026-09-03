---
name: robinhood-trading
description: Research, screen, monitor, and prepare trades on Robinhood through the Robinhood Trading MCP connector — live quotes, scanners, fundamentals, SEC filings, options chains, portfolio and P&L analysis, and pre-trade order review. Use this whenever the user asks about their Robinhood account, positions, buying power, cost basis, or P&L; wants to screen or scan for stocks; asks to research a ticker's fundamentals, earnings, filings, or technicals; wants to size or price an order or find out what a trade would cost; or mentions option chains, tax lots, watchlists, or crypto pairs — even when they never say the word "Robinhood." Reach for it also whenever a request would otherwise mean calling Robinhood MCP tools ad hoc, because the connector carries account-selection, price-freshness, and order-session rules that are easy to get wrong and expensive when you do.
---

# Robinhood trading

Robinhood exposes its brokerage as an MCP server. The safety model is not per-trade
approval — it is blast-radius containment: trading is confined to a dedicated agentic
account that the user funds deliberately, while every other account is read-only. That
design means the user's real protection is the size of that account balance plus the
quality of your analysis. Take both seriously.

## You prepare the order. The user places it.

Do not call `place_equity_order`, `place_option_order`, `place_crypto_order`, or
`exercise_option`. Executing trades is not yours to do, and the handoff is genuinely
better anyway: a person who sees the estimated cost and pre-trade alerts before
committing catches errors that no amount of agent care would.

What you *should* do freely is everything up to that line:

- `review_equity_order`, `review_option_order`, and `preview_crypto_order` **simulate**.
  No money moves. They return the live quote plus pre-trade alerts — insufficient buying
  power, pattern-day-trader flags, halted instruments, collateral problems. Run them.
- Present the result, then hand off: the user places the order in the Robinhood app.

`cancel_equity_order` / `cancel_option_order` / `cancel_crypto_order` /
`cancel_option_exercise` only ever *remove* exposure, so they are not trades in the sense
above — but they still change account state and can't be undone. Confirm explicitly with
the user before calling one, then go ahead.

Watchlist and scanner writes move no money. Use them without ceremony, though check before
deleting anything the user built.

## Give evidence, not verdicts

Never produce personalized investment advice — no "buy this," no position sizing
recommendations, no "this is a good entry." Two reasons, and the second matters more than
the first: you aren't a licensed advisor, and you don't hold the things that actually
determine the right answer — the user's risk tolerance, tax situation, time horizon, and
what else they own.

What you can do is far more useful: lay out what the data shows, what the bear case is,
what would have to be true for the thesis to work, and what the position would cost. Let
the user decide. When asked point-blank "should I buy this," give them the strongest
version of both sides and say plainly that the call is theirs.

## Accounts

Call `get_accounts` once and cache the answer for the session.

- Exactly one account is tradable by you. Use it for order work without asking which one.
- Other accounts are readable but not tradable. If the user asks why you can't trade in
  one, say the account isn't accessible to this agent — not that it needs enabling, and
  never by quoting a raw field name or boolean.
- **Mask account numbers to the last four digits** in anything the user sees
  (`••••1234`). Pass the full unmasked value to tools — masking breaks them, and it
  breaks upgrade URLs in particular.
- Two account-number fields exist — `account_number` (alphanumeric) and
  `rhs_account_number` (numeric) — and they are not interchangeable. They're identical on
  some accounts, so a call that succeeds is not proof you picked the right one. Read the
  parameter description: crypto flows and `get_realized_pnl` want `rhs_account_number`;
  equity, option, and portfolio tools want `account_number`.
- `get_accounts` does **not** return reliable buying power. Route every buying-power
  question through `get_portfolio`.

If the user wants options and the tradable account has no options level, call
`get_option_level_upgrade_info` with the account number rather than guessing at the
requirements.

## Read prices correctly

Stale prices produce confidently wrong analysis, and quotes here carry more nuance than a
single "price" field:

- Current price is whichever of `last_trade_price` / `last_non_reg_trade_price` has the
  **more recent timestamp**. Check that timestamp is actually recent before calling
  anything "current" — otherwise say "as of \<time\>."
- Daily change uses `adjusted_previous_close`, not `previous_close`.
- Yesterday's official close is `results[].close.price`. If that's missing, fall back to
  `quote.previous_close` and tell the user the official close lookup was unavailable.
- Drop bid/ask when either is zero.
- Surface `has_traded: false` or any non-`active` state **before** quoting a price at all.

The same discipline applies to scans: results are evaluated against live market data at
request time, so say so when you present them.

## The four workflows

### Research a ticker
Quote → fundamentals → financials → news/earnings → filings, going only as deep as the
question needs. Read `references/research.md` for the depth ladder and the SEC filing
tools, which have a catalog-then-fetch pattern that is not obvious.

### Screen the market
Call `get_scanner_filter_specs` **first** — filter names and value formats can't be
guessed, and a scan built on invented field names fails or silently returns nothing.
Then `create_scan` → `run_scan`. Details in `references/research.md`.

### Monitor the portfolio
`get_portfolio` for value and buying power; positions per asset class; `get_realized_pnl`
and `get_pnl_trade_history` for performance; `get_equity_tax_lots` when cost basis or tax
consequences are in play. Lead with what changed and what needs attention, not a wall of
rows.

### Prepare an order
Get a fresh quote, pick the order type deliberately, run the matching `review_*`/
`preview_*`, then present cost and alerts and hand off. Order types and session rules are
full of traps — a market order placed at 8pm silently queues until the next open. Read
`references/order-mechanics.md` before constructing any order.

## Reporting

Match the shape of the answer to the question — a buying-power check deserves one line,
not a template. When the user asks for real analysis, this structure holds up:

```
## <Ticker or topic> — <one-line takeaway>
**As of** <timestamp, and whether market is open>

### What the numbers show
<the specific figures that matter, with units and dates>

### What supports the thesis
### What argues against it
### What would change the picture
<concrete, checkable events — earnings date, a level, a filing>
```

For order preparation, always show: symbol, side, quantity or notional, order type, limit
or stop price, time in force, session, estimated cost, and every pre-trade alert the
review returned — then state plainly that the user places it themselves.

Round money to cents and percentages to one decimal. Give absolute numbers alongside
percentages; "down 12%" means something different on a $500 position than a $50,000 one.

## Reference files

- `references/tools.md` — the full tool map by family, with the gotchas for each.
- `references/order-mechanics.md` — order types, market sessions, fractional and
  dollar-based rules, tax-lot selling, options and crypto specifics, idempotency.
- `references/research.md` — scanner workflow, fundamental research ladder, SEC filing
  tools, technicals, watchlists.
