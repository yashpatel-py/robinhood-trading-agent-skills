# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Every release has a "Connector changes" subsection:
what changed in Robinhood's agentic-trading MCP connector, or in what the kit knows about it.

*Unofficial. Not affiliated with Robinhood Markets, Inc.*

## [2.0.0] - Unreleased

The single `robinhood-trading` skill becomes "Preflight for Robinhood Agentic Trading": six skills,
a Claude Code plugin with a fail-closed order guard, and an eval suite that runs against a mock of
all 81 connector tools.

### Added
- Six skills under `skills/`, each usable on its own:
  - `robinhood-trading` (core, rewritten): session preflight, portfolio, research, screening,
    alerts and watchlists, and checked order tickets with an honest handoff.
  - `robinhood-tax-loss-harvesting`: a wash-sale check across the Agentic, individual and
    retirement accounts (recurring buys and dividend reinvestment included), safe rebuy dates and
    specific-lot sell plans.
  - `robinhood-exit-guardian`: a protection audit of stops, take-profits and OCO exits, simulated
    exits in the Agentic account, and native price alerts as the backstop for any account.
  - `robinhood-options-monitor`: the user's own exit rules checked against held options, plus
    expiration, early-assignment and auto-exercise cash checks and earnings inside a position's life.
  - `robinhood-agent-report-card`: what the agent did (orders, fills, rejections, slippage against
    its review, guard blocks) and whether anything else traded in the Agentic account.
  - `robinhood-options-screener`: the owner's work-in-progress options screener, made
    config-driven; it runs only when asked and never picks thresholds for the user.
- Claude Code plugin and same-repo marketplace (`yashpatel-py`):
  - a fail-closed PreToolUse order guard (POSIX `sh`) that blocks `place_*`, `exercise_*` and
    `replace_*` on any MCP server, blocks other money verbs on Robinhood servers, and asks about
    unknown Robinhood tools;
  - permission prompts before cancels and permanent alert deletes, saying what protection each
    one removes;
  - a session line that states the active order mode;
  - a local, masked, hash-chained audit log and review ledger (`~/.local/state/robinhood-skills/`).
- Confirm mode, shipped wired but off. The default is simulate-only. A Claude Code user who sets
  `order_mode` to `confirm` (plus a per-order notional cap) may let the agent place an order only
  for the exact ticket they reviewed and approved, and each one still raises a permission prompt.
  `exercise_option` stays blocked in every mode.
- `connector/`: a verbatim snapshot of the 81 tools, a class for each tool (read, simulate,
  cancel, money and so on), 100+ checked parameter facts, and the response field names from the
  Day-1 capture (`FIELDS.md`, names and types only).
- Deterministic helpers as stdlib-only Python 3.9+ scripts with JSON in and out and no network
  access (market clock, order lint, wash-sale dates, lot selection, options math and more), each
  with golden tests and a prose twin in the skill's `references/formulas.md`.
- Evals: generated `claude plugin eval` cases on a mock of all 81 tools (order-placing ones
  included, so "never called" means something), prompt-injection cases, market-clock and
  account-type variants, and a public scorecard that reports the WITH, SKILLS-ONLY and no-kit arms
  separately.
- A no-account sandbox: `sandbox/mock_server.py` serves the same synthetic household over stdio MCP.
- Packaging for Codex and Cursor (hook integrations labeled "verify" until a first-run test
  passes), Gemini CLI (order tools listed in `excludeTools` for the extension's own server entry,
  also labeled "verify" until a first-run test passes), a skills-only deny list for Claude Code
  (for the server names it lists), and per-skill zips and single-file bundles on each release.
- CI: skill validation, shared-file sync, connector drift, version agreement, unit and hook tests
  (including with no Python on the path), generated-eval freshness, a leak scan and a no-network
  scan on every push; weekly drift watch; tag-driven releases; paid evals only when started by hand
  or on an opt-in schedule.

### Changed
- Skills live in `skills/<name>/`. Connector rules, the invariants block and shared scripts are
  edited once in `shared/` and synced into every skill; CI fails on drift.
- Every order the kit prepares is a simulated ticket from `review_*` or `preview_*`, shown with the
  review's pre-trade checks and market-data disclosure verbatim, and followed by an honest handoff
  ("Nothing was placed.").
- Numbers the user didn't give (quantity, limit, stop, target, option price) are never filled in.

### Fixed
- The 2026-09-21 accuracy audit of v1 found 121 discrepancies against the live tool schemas
  (9 critical, 73 major, 39 minor). `docs/audit-2026-09-21.md` lists each one and the file that
  fixes it. The critical ones:
  - `place_advanced_order` was missing from the list of tools never to call;
  - `get_pnl_trade_history` and `get_realized_pnl` take the rhs account-number value;
  - positions and lots were defaulted to one account instead of the account the user named;
  - `get_option_positions` was read without `nonzero`, so closed positions showed as held;
  - trade-history spans and defaults differ from `get_realized_pnl`;
  - `get_market_hours` does not exist;
  - the earnings gate used the market-wide calendar instead of per-symbol results;
  - the agent could choose an option limit price;
  - cancels were described as only removing exposure (cancelling a stop or OCO increases risk, and
    `cancel_option_exercise` cancels every queued exercise for that option).

### Removed
- Pattern-day-trader counting: FINRA eliminated the PDT rule on 2026-06-04.
- The top-level `robinhood-trading/` directory (its content moved into `skills/`).

### Security
- The order guard is enforced in Claude Code with the plugin, for the MCP tool calls its hook
  sees. It doesn't see shell commands, so an agent allowed to run Bash without a prompt can route
  around it; keep Bash prompts on (`docs/safety-model.md`, "Gaps we know about"). Native Windows
  without `sh` fails open; the skills-only deny list covers the two server names it lists, and you
  add lines for any other. Everywhere else the simulate-only rule is advised, and the README safety
  table says which is which.
- Audit-log entries mask account numbers and never store raw responses or balances.
- CI fails on committed account-number-like values, credentials or Day-1 capture files.

### Connector changes
- Verified against 81 live tools (schemas captured 2026-09-21; response field names captured
  2026-09-22). 24 live tools are missing from Robinhood's published tool list.
- Named in tool descriptions or older docs but not exposed: `replace_option_order`,
  `get_crypto_tax_lots`, `get_market_hours` and `get_quotes`.
- Every response is `{"data": ..., "guide": ...}`. The skills follow the guide text for
  presentation, masking and pagination, and the kit's safety rules win on any conflict.
- `review_equity_order` returns `order_checks` as an object (`{}` when clear), plus a
  `market_data_disclosure` that must be shown verbatim with every ticket.
- The OCO tools (`get_advanced_orders`, `review_advanced_order`) answered "the tool you requested
  cannot be found or does not exist" on both capture accounts, so the skills treat that error as
  "not enabled for this account" and fall back to stop orders and alerts.
- `get_index_quotes` returned an empty `state`; market sessions come from the clock and the
  holiday table, never from that field.
- `get_pnl_trade_history` rows carry no asset class and no holding term; option closes appear
  under the underlying ticker. A row counts as a share sale only when it matches a filled equity
  order.

## [1.0.0] - 2026-09-02

### Added
- The `robinhood-trading` skill for Robinhood's Trading MCP connector: account handling,
  price-freshness rules, research, screening and pre-trade order review, with `references/`
  for tools, order mechanics and research.
- Two boundaries held on purpose: simulate with `review_*`/`preview_*` and hand off to the user
  (no `place_*` calls), and evidence rather than buy or sell verdicts.
- MIT license and install instructions for Claude, ChatGPT and other `SKILL.md` agents.

### Connector changes
- Written from the connector's documentation and tool descriptions, without a tool snapshot. The
  2026-09-21 audit measured it against the live tools (see 2.0.0, Fixed).

[2.0.0]: https://github.com/yashpatel-py/robinhood-trading-agent-skills/compare/25d7e4d...v2.0.0
[1.0.0]: https://github.com/yashpatel-py/robinhood-trading-agent-skills/tree/25d7e4d
