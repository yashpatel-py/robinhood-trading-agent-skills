# Formulas: the report card's scripts, by hand

*Unofficial — not affiliated with Robinhood.* This is the prose twin of `scripts/audit_verify.py`,
`scripts/reconcile.py` and `scripts/scorecard.py`. Use it when Python can't run on this surface, and
label every figure "computed by hand". Money: decimal arithmetic, rounded half-up to cents at the
end. Rates: one decimal. Times: compare instants in UTC, show them in ET.

Worked numbers below come from the kit's fixture week (Agentic ••••X4F1, 2026-11-09 → 2026-11-15 ET);
they are sandbox data, not anyone's account.

## 0. The window

- ET dates → UTC instants: midnight ET is 04:00Z while daylight time is in effect (second Sunday of
  March 02:00 to first Sunday of November 02:00) and 05:00Z otherwise.
- The window is `[start, end)`: start = midnight ET of the first day; end = midnight ET of the day
  after the last day. 2026-11-09 → 2026-11-15 is `[2026-11-09T05:00Z, 2026-11-16T05:00Z)`.
- An order belongs to the window by `created_at`; a trade-history row by `timestamp`.

## 1. audit_verify: the chain and the summaries

Input: `{"dir"?, "window"?, "ledger"?}`. It reads `audit-YYYY-MM.jsonl` files in month order, any
`blocked.jsonl` lines not yet folded, and `ledger.jsonl` next to the audit folder when present.

1. For every line, in file order, across files: `hash = "sha256:" + sha256(the line's exact bytes,
   without the newline)`. The next line's `prev` must equal it. The very first line's `prev` starts
   with `genesis:`; if it doesn't, older files are gone (`truncated_start`), which is not a break.
2. Breaks: a line whose `prev` doesn't match (`prev_mismatch`; the line before it was changed or lines
   were removed), a line that isn't a JSON object (`unreadable_line`), or a `genesis:` line after the
   first (`chain_restarted`). `chain_ok` = no breaks. This cannot be done without sha256 over exact
   bytes: by hand, write "audit chain not verified".
3. Summaries count only lines whose `ts` is in the window:
   - order simulations = `post` lines of `review_equity_order`, `review_option_order`,
     `review_advanced_order` or `preview_crypto_order` whose result is not `ok: false`;
   - places = `post` lines of `place_*` (and `exercise_*`) tools; `matched_review` is true when an
     earlier review line in the same `session` has the same `fingerprint`; `review_age_s` = seconds
     from that (latest) review to the place;
   - blocked = `pre_block` lines (plus unfolded ones), counted by `reason_code`;
   - asks = `pre_ask` lines; "sent" when the next call in that session is a `post` of the same tool.
4. Possible injection: list each session's calls in time order (a `pre_ask` followed by the `post` of
   the same tool is one call). An attempt (a `place_*`, `exercise_*`, `cancel_*`, or `delete_alert`
   with `confirm: true` or a prompt) is flagged when one of the 3 calls before it is `get_equity_news`,
   `get_sec_filing`, `get_alert_log`, `get_scans`, `run_scan`, `get_watchlist_items` or
   `get_politician_trades`, or when its `after_tool` is one of them. `calls_after` = how many calls back.

Fixture: 70 lines, chain intact; in the window 37 simulations, 8 places (household orders e1–e8;
e7 has no matching review), 2 blocks (NO_MATCHING_REVIEW 1 call after `get_equity_news` at 2026-11-12
10:14 ET, the possible injection; SIMULATE_ONLY on `exercise_option`), 1 cancel prompt, sent. In the
tampered copy line 51 was edited, so line 52 fails to chain.

## 2. reconcile: attribution and the status line

Input: the window, the Agentic last 4, `accounts`, `broker_orders`, `reads`, and the audit_verify
output as `audit`.

0. One order, one row. Drop a repeated (account, asset, order id), keeping the copy tagged `agentic`.
   Fold each OCO's legs into the OCO: legs named in its `leg_order_ids`, else equity orders with the
   same account, symbol, side and quantity, created within 120 s of the OCO: one stop leg and one limit
   leg, each price equal to the OCO's when the OCO row carries it. The OCO takes its source from its
   legs (`agentic` if any leg is) and a filled leg's fill.
1. Keep orders created in the window. Agentic-account orders are matched to the log's place calls in
   three passes, each order and each call used once: (a) the order id the call returned (a leg's id
   matches its OCO); (b) `ref_id`, if the broker row has one; (c) same asset, symbol, side and
   quantity, `created_at` within 120 s of the call (the closest wins; 120 s matches, 121 s does not).
2. Unmatched Agentic orders: crypto, or an OCO with no tagged leg → source not distinguishable; an
   equity or option row with no `placed_agent` → untagged (UNKNOWN: fix the mapping); `agentic` → "no
   local audit entry" when the log's first line is at or before the order, else "unattributed"; any
   other value (`user`, `recurring`, `drip`, anything newer, or `not_agentic` for a row only in the
   all-sources read that carried no value) → "marked as not placed by an agent". Those rows exist only
   because equity and option orders are also read with no `placed_agent` filter.
3. Read-only accounts: an `agentic` order there is flagged. An account is "verified" only when both
   agent-filtered reads (`get_equity_orders`, `get_option_orders`) are complete and show none. No read
   at all is "not verified" (UNKNOWN); `not_in_scope` is only for a decline or
   `read_scope = "agentic_only"`.
4. Notional per order: filled quantity × average price if anything filled; else dollar amount; else
   quantity × limit; else quantity × stop; options × 100; an OCO: quantity × the higher of its limit and
   stop (never both legs added). Unknown stays unknown.
5. Status: ACTION NEEDED if any no-local-entry order, agent order outside Agentic, placed-without-
   review order, non-agent order in Agentic, possible injection, broken chain, or a place call that
   reported success without a broker order; else UNKNOWN if a read is partial or failed, a required
   Agentic read is missing (equity and option orders each with `placed_agent: "agentic"` and with no
   filter, crypto orders, advanced orders), an equity or option row came with no source, or a read-only
   account wasn't verified; else CLEAR.
6. Line 1: `STATUS: Dollars at stake: ` + the three largest dollar items, each "n order(s) ($total)
   …", joined with " · ", then the flags; "none found" when there is no dollar item.

Fixture: e9 13.733333 × $30.00 = **$412.00** (no local entry) · e7 4 × $71.40 = **$285.60** (sent after
a review of 3) · u1 2 × $70.00 = **$140.00** (tagged `user`, cancelled) · 1 possible injection →
ACTION NEEDED.

## 3. scorecard: counts, realized, per-trade, slippage, flags, SPY

Agent orders = Agentic orders in the window tagged `agentic`, plus the ones reconcile matched.
Fill = filled quantity (`cumulative_quantity`) × `average_price` at `last_transaction_at`. An OCO
whose leg filled is one share fill (the leg's) for turnover, per-trade matching, averaging down and
the SPY pairs.

- **Counts** by state (section 4 of `metrics.md`), after the same one-row-per-order and OCO-leg folding
  as reconcile. `pending_cancelled` is "cancel pending" (still working), never cancelled. Fixture:
  9 orders, 7 filled, 1 rejected, 1 cancelled; 1 source-not-distinguishable (the ETH limit sell).
- **Realized**: `total_returns` if present, else the sum of non-null `realized_gain` buckets; count the
  null buckets. Fixture: 45.00 + 11.50 + 109.73 + 68.80 + 2.20 = **$237.23**.
- **Per-trade**: a trade-history row in the window counts for the agent only if a filled agent sell
  has the same symbol and quantity and its fill time is within 120 s of the row. Fixture: e2 AMD
  +$45.00, e3 PLTR +$11.50, e4 KO +$68.80, e8 KO +$2.20 → win rate 4 ÷ 4 = **100.0% (n=4)**, average
  gain 127.50 ÷ 4 = **$31.88**, no losses so no profit factor. The ETH row is unclassified.
- **Slippage** (bps): buy (fill − ask) ÷ ask × 10,000; sell (bid − fill) ÷ bid × 10,000, against the
  latest same-fingerprint review no later than 120 s after the order was created. Fixture: e1
  (29.20 − 29.18) ÷ 29.18 = 6.85; e2 (163.05 − 163.00) ÷ 163.05 = 3.07; e3 6.59; e4 2.78; e8 2.78 →
  average **4.4 bps (n=5)**; e7 has no review.
- **Averaging down**: per symbol, walk agent fills in time order; a buy below the previous agent buy's
  fill (with no agent sell in between) counts. Fixture: **0** (e3 sold PLTR between e1 and e9).
- **Buy within 24 h of a loss**: agent buys created in `(loss time, loss time + 24 h]` after an
  agent-attributed realized loss. Fixture: **0** (no losses).
- **Turnover**: Σ filled notional of agent orders (buys + sells) ÷ `total_value` × 100. Fixture:
  $2,788.70 ÷ $18,308.50 = **15.2%**.
- **Trade-matched SPY**: pair agent buys and later agent sells per symbol, first in first out, both
  filled inside the window. Per pair: agent = qty × (exit − entry); SPY = qty × entry × (SPY exit bar
  close ÷ SPY entry bar close − 1), using the hourly bar that starts at or before each fill (within a
  day). Without bars the script returns `spy_request` = SPY, hourly, from the first entry fill minus a
  day to the last exit fill plus an hour. Fixture (n=2, synthetic hourly bars): PLTR e1→e3 +$11.50 vs
  $292.00 × (670.20 ÷ 668.00 − 1) = +$0.96; KO e7→e8 +$2.20 vs $285.60 × (671.20 ÷ 668.30 − 1) = +$1.24
  → agent **+$13.70**, SPY **+$2.20**. AMD (3) and KO (10) sales had no agent buy in the window.

Always print n beside a rate or comparison, and "small sample" below n=5.
