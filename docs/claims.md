# What we could not find elsewhere, and how we looked

*Unofficial. Not affiliated with Robinhood Markets, Inc.*

The README makes six "we found none as of 2026-09-22" claims. This page lists, for each one, what was
searched and what came back, so you can re-run the search yourself. The searches were run by the
maintainers on 2026-09-21 and 2026-09-22.

Read "none found" narrowly. GitHub code search covers default branches only, lags recent pushes,
can't see private repositories, and returned intermittent errors during the searches (failed queries
were re-run). A zero means none found in the index that day, not proof that nothing exists. If you
know of a project that does one of these things, open an issue and we will correct the claim.

<a id="h1"></a>

## H1: A wash-sale check your agent runs across all your Robinhood accounts

**Claim.** An agent skill that runs inside your AI agent, over Robinhood's MCP connector: before the
agent sells at a loss or buys back, it checks every Robinhood account you hold (Agentic, individual,
IRA, Roth), including recurring and dividend-reinvestment buys, flags IRA purchases that permanently
disallow a loss, and gives the exact dates. "None found" applies to agent skills and agent-side
tools only. Standalone tax software does household checks, as listed below.

**What we found.**
- [TaxHarvest](https://taxharvest.ai/blog/tax-loss-harvesting-software-robinhood) (commercial)
  offers tax-loss harvesting across a household for Robinhood users, with cross-account wash-sale
  checks including recurring buys. Our first look also noted IRA wash sales, which its Robinhood
  page doesn't spell out. It works outside the agent, as its own product: it is not an agent skill or
  an MCP tool, and it doesn't say how it connects to Robinhood.
- [doublehq/oracle](https://github.com/doublehq/oracle) (Double Finance) does direct indexing and
  tax-loss harvesting and has been used through the Agentic account, but its README says it cannot
  optimize across multiple accounts in a household.
- GitHub code search over `SKILL.md` files for Robinhood skills mentioning "tax loss" returned 0 hits;
  one indexed `SKILL.md` (0 stars) mentions `get_equity_tax_lots`.
- A GitHub repository search for "tax loss harvesting claude" returned 0 repositories.
- Robinhood's wash-sale article says wash sales apply across all of your investing accounts and that
  tracking them is "the customer's sole responsibility"
  ([Robinhood: wash sales](https://robinhood.com/us/en/support/articles/wash-sales/)). Its 1099s
  report wash sales per account, and the app gives no wash-sale warning at trade time.
- The agent can read every account and trade in one ([Robinhood: Agentic Trading
  overview](https://robinhood.com/us/en/support/articles/agentic-trading-overview/)), which is the setup
  that produces a cross-account wash sale.

**What ships.** `robinhood-tax-loss-harvesting` and `shared/scripts/wash_sale.py`
([example](examples/harvest-check.md)).

<a id="h2"></a>

## H2: An exit or a backstop for every position

**Claim.** Audit stops and OCOs, simulate exits in the Agentic account, and set native Robinhood
price alerts that notify your phone for holdings in any account.

**What we found.** GitHub code search returned 0 `SKILL.md` files referencing `create_alert` or
`place_advanced_order`. The alert tools take no account number, so an alert covers a holding in any
account (`connector/schemas/watchlists-alerts.md`). Users of other Robinhood agent setups report that
monitoring stops when the laptop does.

**Honest limits.** In simulate-only mode the only protection the kit creates is a native alert, and
alerts notify; they do not sell. An OCO is a reviewed spec: placing it takes confirm mode or your own
action. The OCO tools may not be enabled for your account at all.

**What ships.** `robinhood-exit-guardian` ([example](examples/protection-audit.md)).

<a id="h3"></a>

## H3: Order placement blocked by code, and a with/without eval suite

**Claim.** In Claude Code, a fail-closed hook blocks order-placing tool calls, and a with/without
eval suite on a mock of all 81 tools, including prompt-injection cases, measures how the skills
behave. Its results go on [the scorecard page](eval-scorecard.md); until the first run is recorded
there, that page says "NOT YET RUN" and this claim is about the suite, not about results.

**What we found.** No repository that ships a one-command installable guard together with a Robinhood
playbook and a with/without eval suite. Order guards we did find:
- [Oft3r/agentic-trading-desk](https://github.com/Oft3r/agentic-trading-desk) uses a
  project-specific order-guard script.
- [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) (33.8k stars) has had a Robinhood
  Agentic connector since 2026-05-29: read-only by default, with a committed mandate, an order guard
  and an instant halt, inside its own app.
- [Robin Trade](https://robinaiagent.com/) (commercial) routes supervised proposals through
  approvals.

None of these ships as a Claude Code hook with a Robinhood playbook and a with/without eval suite.
The only Robinhood connector mock we found covers 8 tools
([naga-k/mock-rh-mcp](https://github.com/naga-k/mock-rh-mcp)); no Robinhood skill we found ships
evals.

**What ships.** `hooks/`, `evals/`, `sandbox/`, [the scorecard](eval-scorecard.md) and
[the safety model](safety-model.md).

<a id="h4"></a>

## H4: What your agent actually did

**Claim.** List every order placed through the connector, flag orders that came from another agent,
app or machine, and keep a tamper-evident local log of blocked attempts.

**What we found.** No indexed project with any traction that attributes or benchmarks agent activity
on Robinhood; the closest (Vibe-Trading's "Shadow Account") works from CSV exports. Activity logs do
exist:
- [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)'s Robinhood connector keeps an audit
  ledger inside its own app.
- [Robin Trade](https://robinaiagent.com/) (commercial) keeps audit logs.
- [YizhiSong/FriesTrader](https://github.com/YizhiSong/FriesTrader) keeps an append-only log.
- One Robinhood Claude plugin's log (masterledgerlive's "MACHINE LOG") is written by the model on
  instruction.

We found none that uses the connector's `placed_agent` filter to flag orders that came from another
agent, app or machine, and none that keeps a hash-chained, tamper-evident local log of blocked
attempts written by a Claude Code hook.

**What ships.** `robinhood-agent-report-card` and `hooks/audit_log.py`
([example](examples/report-card.md)).

<a id="h5"></a>

## H5: Options expiration, assignment and auto-exercise cash needs, before they land

**Claim.** For the options you hold: your own exit rules, expiration, early-assignment risk before an
ex-dividend date, and the cash an in-the-money long option would need at auto-exercise.

**What we found.** `gh search repos "robinhood wheel"` returned 0. Apart from this project, one 0-star
skill mentions `exercise_option`.

**What ships.** `robinhood-options-monitor` ([example](examples/options-radar.md)).

<a id="h6"></a>

## H6: Verified line by line against the live connector

**Claim.** Every tool name and parameter in the skills was checked against the 81 tools the live
connector exposed on 2026-09-21, response fields against a live capture on 2026-09-22, and changes
are logged publicly.

**What we found.** Robinhood's support article lists 57 tools; the server exposed 81. We found no
official changelog ([Robinhood: trading with your
agent](https://robinhood.com/us/en/support/articles/trading-with-your-agent/)). A third-party
connector broke when tools were renamed.

**What ships.** `connector/tools.snapshot.json`, `connector/FIELDS.md`, `connector/CHANGES.md` and
`tools/check_drift.py`, which fails CI when a skill names a tool or parameter the snapshot doesn't have.
