# Metrics and caveats: the agent report card

*Unofficial — not affiliated with Robinhood.* Read this before writing your first report in a
session. Every number the report shows is defined here, with what it cannot show and how to say it.
The scripts carry the same definitions in their `definitions` output; this file is the human-readable
version and the wording guide.

Contents: 1 Sources · 2 Attribution · 3 Status line · 4 Order counts · 5 Realized P&L · 6 Per-trade
figures · 7 Slippage · 8 Behavior flags · 9 Trade-matched SPY · 10 Guard log · 11 Not measured ·
12 Wording

## 1. Where each fact comes from

| Fact | Source | How far to trust it |
|---|---|---|
| Orders and who placed them | `get_equity_orders` / `get_option_orders`, once with `placed_agent: "agentic"` and once with no filter (every source) | Broker record. `agentic` means "through the MCP connector", not "this agent"; other values (`user`, `recurring`, `drip`, …) mean not an agent |
| Crypto and OCO orders | `get_crypto_orders`, `get_advanced_orders` | Broker record with no source filter; an OCO's legs are equity orders and carry `placed_agent` |
| Realized P&L | `get_realized_pnl` | Robinhood's own figure for the whole Agentic account |
| Per-trade results | `get_pnl_trade_history` | Broker rows with no asset class; attributed only by matching (section 6) |
| What this kit sent, blocked or asked about | the local audit log | Tamper-evident, this machine only, only while the plugin ran with `audit_log` on |
| The quote at review time | review lines in the audit log (and the review ledger) | Recorded by the hook when the review returned |

The broker is the source of truth for what happened. The audit log is the source of truth only for
what this kit saw.

## 2. Attribution (reconcile.py)

First, one order is one row: an order returned by both the `agentic` read and the all-sources read is
passed once (the scripts also drop a repeated id, keeping the `agentic` copy), and each OCO's legs
(hydrated by `get_advanced_orders`, and listed by `get_equity_orders` too) fold into the OCO, by
`leg_order_ids`, else by same symbol, side and quantity created within 120 s of the OCO (one stop leg
and one limit leg, each price equal to the OCO's when the OCO row carries it). An OCO takes its source from its legs (`agentic` if any leg is). Its
amount is quantity × the higher leg price, or the filled leg's value, never the two legs added.

Each order created in the window then lands in exactly one category:

| Category | Meaning | Status | How to say it |
|---|---|---|---|
| matched | A place call in this kit's log produced this order (matched by order id; else `ref_id`; else same asset, symbol, side and quantity within 120 s) | – | "sent by this kit (Claude Code on this machine)" |
| broker_only | Tagged `agentic`, created after the log starts, and not in the log | ACTION NEEDED | "no local audit entry: placed by another agent, app or machine" |
| unattributed | Tagged `agentic` but no log covers its time (no plugin, or created before the log starts) | none | "which agent placed it can't be told" |
| source_unknown | Crypto rows, and OCOs whose legs carry no source | none | "source not distinguishable" |
| source_unknown (untagged) | An equity or option row passed with no `placed_agent` (a mapping gap) | UNKNOWN | "source not recorded for this order; the report can't be clear" |
| user_orders_in_agentic | In the Agentic account, any source other than `agentic`: `user`, `recurring`, `drip`, any other value, or `not_agentic` (only in the all-sources read, and the row carried no value) | ACTION NEEDED | "Robinhood marks it as not placed by an agent": name the source (placed by you, a recurring investment, dividend reinvestment, "source value not returned") and ask the user to confirm it was theirs |
| agent_orders_outside_agentic | Tagged `agentic` in a read-only account | ACTION NEEDED | "an agent order in a read-only account: agents shouldn't be able to trade there" |

Two lists sit beside the categories:
- **placed_without_review**: a matched order whose place call had no earlier review in the same
  session with the same order fingerprint. The confirm gate normally refuses such a call, so this
  means the gate was not in the path (an older kit, another session, or edited state). ACTION NEEDED.
- **audit_only**: a logged place call with no broker order in the reads. If the call returned an
  error it is informational; if it reported success, it is ACTION NEEDED ("check the order in the
  app": the window, the account or a later rejection can explain it).

Read-only verification per account: `verified_none` (both agent-filtered reads complete and empty),
`agent_orders_found`, `incomplete` (a page not read, or only one of the two tools), `not_checked`
(no read: for example a scheduled run with no `[policy] read_scope` and no consent in its prompt),
`not_in_scope` (the user declined, or `read_scope = "agentic_only"`; never an account nobody was asked
about). Only `verified_none` may be written as "Verified: no agent orders in ••••M7Q5". For
`not_checked` on a scheduled run, add "set `read_scope` in `[policy]`, or name the accounts in the
scheduled prompt, to verify them on a schedule".

## 3. The status line

Precedence: **ACTION NEEDED** (any row above marked so, a possible injection, a broken chain, or a
place call that reported success with no broker order) > **UNKNOWN** (a read was partial or failed, a
required Agentic read is missing, an equity or option row came with no source, or a read-only account
was not verified) > **CLEAR**. Required Agentic reads: equity and option orders with
`placed_agent: "agentic"` and with no filter (`"filter": "all"`), crypto orders, advanced orders.
Unknown is never clear. Accounts the user excluded (declined, or `read_scope = "agentic_only"`) are
noted, not unknown; an account nobody was asked about is not excluded, it is not verified.

"Dollars at stake" lists the three largest dollar items: the filled value (filled quantity × average
fill price), else the order's intended size (dollar amount, then quantity × limit, then quantity ×
stop), options × 100 per contract. Flags without dollars (injection, chain break) follow. With no
dollar item the line reads "none found". Use reconcile's `status_line` verbatim as line 1.

## 4. Order counts

Counted: orders created in the window in the Agentic account that are tagged `agentic`, plus any this
kit's log shows it sent, each once (an OCO and its legs are one order). States: filled; partially
filled; rejected; failed; cancelled (also `canceled`, voided); cancel pending (options
`pending_cancelled`: a cancel was requested but not confirmed, so the order is still working and can
fill; counted apart and never written as cancelled); open (new, queued, confirmed, unconfirmed, and an
OCO that is `active`). A cancelled order with a partial fill counts as cancelled; its filled part still
counts toward turnover. An OCO whose leg filled counts as filled; the sibling leg's cancellation is not
counted.

## 5. Realized P&L

`get_realized_pnl` over the window's dates, key `account_number` with the **rhs value** (R4).
- Robinhood's window total (`total_returns`) is shown when present; otherwise the sum of buckets. If
  both exist and differ by more than a cent, the script notes it.
- A null bucket is "n/a" (a transfer-only period), never $0. All-null means "no realized P&L
  reported", not zero.
- It is account-level and includes every source: orders the user placed in Agentic, option closes,
  crypto. Say "realized in Agentic (Robinhood, all sources)", never "the agent made".

## 6. Per-trade figures

`get_pnl_trade_history` rows are `{timestamp, symbol, side, quantity, price, realized_gain}` with no
asset class and no term. Option closes appear under the underlying ticker with the premium as price;
crypto under the base asset (BTC); prediction markets and adjustments may have no symbol or side.
So a row counts as the agent's only when it matches a filled agent **sell** order: same symbol, same
quantity, fill time within 120 s. A row matching a non-agent order is "other source"; everything else
is "unclassified" with a reason, and stays out of the per-trade figures.

- Win rate = rows with a gain above $0 ÷ agent-attributed rows, with n. Below n=5 say "small sample".
- Average gain and average loss (a negative number); profit factor = gains ÷ |losses|, null when there
  are no losses (0 when there are no gains).
- Never sum rows by ticker into "the agent's P&L": the sum mixes options, crypto and the user's trades.
- The order reads start at the window (`created_at_gte`), so a GTC order created earlier that filled
  inside the window (a resting stop, say) is not among them; its realized row shows as unclassified.
  To attribute it, read further back (up to the GTC lifetime, R8) and pass those orders too.

## 7. Slippage versus the reviewed quote

For each filled agent order this kit sent with a review in its log, using the latest review with the
same fingerprint no later than 120 s after the order was created:
buy = (fill − review ask) ÷ review ask × 10,000; sell = (review bid − fill) ÷ review bid × 10,000.
Positive means worse than the quote at review time. Equity and crypto only (option reviews carry
per-leg quotes). In simulate-only mode this kit sends nothing, so n is 0: don't compare other agents'
fills with this kit's reviews.

## 8. Behavior flags

Patterns worth a look, not verdicts. Each lists the orders involved.
- **Averaging down**: an agent buy filled below the fill of the agent's previous buy of the same
  symbol in the window, with no agent sell of it in between.
- **New buy within 24 h of a realized loss**: an agent buy order created within 24 hours after an
  agent-attributed realized loss (any symbol).
- **Turnover**: filled agent notional (buys + sells) in the window ÷ the Agentic account's current
  `total_value` from `get_portfolio`, as a percentage. Context only; not annualized.

## 9. Trade-matched SPY comparison

Only when an agent buy and a later agent sell of the same symbol both filled inside the window, with
fill times (FIFO by fill time; one pair per matched slice). Per pair: agent = quantity × (exit fill −
entry fill); SPY = the same entry dollars × (SPY at exit ÷ SPY at entry − 1), using the hourly SPY bar
that contains each fill (or the latest earlier bar within a day). Show it with **n and every pair's
fill times**, and the caveat "small sample; not a performance claim". It ignores fees, dividends,
taxes and positions opened before the window. Write "agent −$19.30 · SPY −$0.08 (n=3)"; never
"beat", "lagged", "outperformed" or a percentage return. It is not an account return; that one is
not measurable here (section 11).

## 10. The guard log (audit_verify.py)

- **Chain.** `chain_ok` true means every line chains to the one before it. A break names the line
  that fails to chain; the damaged or removed line is the one before it. Reasons: `prev_mismatch` (a
  line changed or lines removed), `unreadable_line` (an interrupted write, damage or a hand edit),
  `chain_restarted` (a new genesis mid-log: the state directory was reset or a file replaced).
  `truncated_start` means the oldest file is gone (the 50 MB retention deletes whole old months). The
  log is tamper-evident, not tamper-proof: the newest lines can be dropped, and a whole log rewritten,
  without a break.
- **Coverage.** `covers_window` false means the log starts after the window start; orders before it
  are "unattributed". Lines still in `blocked.jsonl` are reported but not chain-verified.
- **Order simulations**: successful `review_*` and `preview_crypto_order` calls in the window.
- **Blocked attempts**, in words:

| Code | Say |
|---|---|
| SIMULATE_ONLY | blocked: live orders are off (simulate-only); exercise is always blocked |
| NO_MATCHING_REVIEW | blocked: no matching review in this session |
| POLICY_CAP / POLICY_DENY | blocked: outside your own limits |
| LINT_ERROR | blocked: the order failed the kit's checks |
| REF_ID_REUSED / DUPLICATE_ORDER / REVIEW_ALREADY_CONSUMED | blocked: this order was already sent or the key was reused |
| CANNOT_VERIFY_SESSION / GATE_FAILED | blocked: the confirm gate could not verify the call |
| UNKNOWN_MONEY_TOOL | blocked: an unknown Robinhood tool that looks like it moves money |
| UNKNOWN_TOOL (a prompt) | you were asked about an unknown Robinhood tool |

- **Possible prompt injection**: an attempt to place, exercise, cancel or confirm-delete an alert
  within 3 tool calls after a tool that returns outside text (`get_equity_news`, `get_sec_filing`,
  `get_alert_log`, `get_scans`, `run_scan`, `get_watchlist_items`, `get_politician_trades`) in the same
  session, or a guard block whose previous tool was one of them. Outcome: blocked, asked and not sent,
  approved at the prompt and sent, or sent. It is positional: a legitimate order right after reading
  news is flagged too. Say "possible", give the time and the tool, and don't quote the outside text
  unless the user asks.
- **Permission prompts** (asks) and **unknown tools** are listed as they appear.

## 11. Not measured, and why

- **Account return versus an index.** Deposits and withdrawals are not visible to the connector, so
  any account-level return would be wrong. The trade-matched comparison is the only market comparison.
- **Tax impact.** Use `robinhood-tax-loss-harvesting`.
- **Fees.** Realized figures are Robinhood's; slippage and SPY pairs use fill prices only.
- **Per-trade results for options and crypto.** Trade-history rows carry no asset class.
- **Anything outside Robinhood's agent tag or this machine's log**: other brokers, other logins, a
  plugin install on another machine.

## 12. Wording

| Write | Never write |
|---|---|
| "win rate 50.0% (n=2, small sample)" | "the agent wins half its trades" |
| "agent −$19.30 · SPY −$0.08 (n=3; not a performance claim)" | "the agent lagged the market", "beat SPY", "alpha" |
| "no local audit entry: placed by another agent, app or machine" | "hacked", "unauthorized" (the log can't know) |
| "possible prompt injection after get_equity_news" | "the news tried to hijack your agent" |
| "averaging down ×1 (AMD)" | "the agent is chasing losses" |
| facts, n and definitions | grades, scores, rankings, annualized figures, advice on keeping the agent |
