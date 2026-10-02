<div align="center">

# Preflight for Robinhood Agentic Trading

*Unofficial. Not affiliated with Robinhood Markets, Inc., Anthropic, OpenAI or Google. Not investment or tax advice.*

**Your AI agent can read every Robinhood account. Preflight makes it check before anything is traded, and by default it prepares every order without placing it.**

Wash-sale checks across your Agentic, individual and IRA accounts · an exit or phone alert for every position ·
the cash your options could demand at expiration · a checked ticket for every order · a record of what your agent actually did.
In Claude Code a fail-closed hook blocks order-placing tool calls; on other surfaces the rule is advised, and the table below says which.

[![skills.sh](https://img.shields.io/badge/skills.sh-npx%20skills%20add-black)](https://skills.sh/yashpatel-py/robinhood-trading-agent-skills)
[![release](https://img.shields.io/github/v/release/yashpatel-py/robinhood-trading-agent-skills)](../../releases)
[![validate](https://github.com/yashpatel-py/robinhood-trading-agent-skills/actions/workflows/validate.yml/badge.svg)](../../actions/workflows/validate.yml)
[![evals](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/yashpatel-py/robinhood-trading-agent-skills/main/docs/badges/evals.json)](docs/eval-scorecard.md)
[![License: MIT](https://img.shields.io/github/license/yashpatel-py/robinhood-trading-agent-skills)](LICENSE)
![Robinhood: unofficial](https://img.shields.io/badge/Robinhood-unofficial%20%C2%B7%20not%20affiliated-lightgrey)
![connector](https://img.shields.io/badge/connector%20verified-2026--09--22%20%C2%B7%2081%20tools-blue)

`npx skills add yashpatel-py/robinhood-trading-agent-skills`

<!-- Once skills.sh lists the repo (after the first `npx skills add` installs), swap the static skills.sh badge above for the live counter: https://www.skills.sh/b/yashpatel-py/robinhood-trading-agent-skills -->

</div>

Robinhood opened its brokerage to AI agents over MCP. The tools are powerful, and the documentation is
thin in exactly the places that cost you money. Most of this kit is the knowledge you would otherwise
pick up by getting it wrong once: which account number a tool wants, why a market order at 8 pm does
not do what you think, why a buy in your IRA can erase a loss your agent just took.

## Demo

![Preflight in Claude Code on sandbox data: a cross-account wash-sale conflict, then the order guard blocking a place call](docs/media/demo.gif)

**In the demo household (sandbox data), Preflight found** $390.00 of TSLA loss that a Roth IRA purchase would permanently disallow · $84,467.50 of positions with no working exit or alert · a $130,000.00 auto-exercise cash need against $2,480.00 of buying power · an order call it blocked. No order was placed. <!-- golden: tsla_wash.disallowed_usd protection_audit.value_unprotected_usd spy_auto_exercise.cash_needed_usd spy_auto_exercise.buying_power_usd -->

Full transcripts, every number computed by the skills' scripts on sandbox data: [portfolio](docs/examples/portfolio.md) ·
[checked ticket](docs/examples/checked-ticket.md) · [harvest check](docs/examples/harvest-check.md) ·
[protection audit](docs/examples/protection-audit.md) · [options radar](docs/examples/options-radar.md) ·
[report card](docs/examples/report-card.md).

## Try it without a Robinhood account

The sandbox is a local MCP server with the connector's real 81 tool names and schemas and a synthetic
household (an Agentic account, an individual account and a Roth IRA). Its order tools place nothing.

```sh
git clone https://github.com/yashpatel-py/robinhood-trading-agent-skills && cd robinhood-trading-agent-skills
claude --plugin-dir . --mcp-config sandbox/mcp.json --strict-mcp-config
# then: "(Context: it is Monday 2026-11-16, 8:05 PM ET.) Harvest my TSLA loss" or "what's my buying power?"
```

`--strict-mcp-config` keeps your real Robinhood connector out of that session. Other agents:
[sandbox/README.md](sandbox/README.md).

## Install in 30 seconds

```bash
# Any SKILL.md agent (Claude Code, Codex, Cursor, OpenCode, Copilot…)
npx skills add yashpatel-py/robinhood-trading-agent-skills
npx skills add yashpatel-py/robinhood-trading-agent-skills --skill robinhood-tax-loss-harvesting   # just one skill
#   the npx skills installer (Vercel's, not ours) sends anonymous install telemetry; DISABLE_TELEMETRY=1 turns it off
#   skills only: the order guard is advised unless you add integrations/claude-code/settings.deny.json
#   (it names two servers; add lines for yours if /mcp shows another: docs/install-claude-code.md)

# Claude Code plugin (skills + fail-closed order guard + local audit log): recommended
/plugin marketplace add yashpatel-py/robinhood-trading-agent-skills
/plugin install robinhood-trading@yashpatel-py

# GitHub CLI
gh skill install yashpatel-py/robinhood-trading-agent-skills robinhood-trading

# Codex (plugin marketplace; its guard hook is labeled "Verify" until tested, see the safety table)
codex plugin marketplace add yashpatel-py/robinhood-trading-agent-skills   # registers the marketplace
codex plugin add robinhood-trading@yashpatel-py   # installs it (or /plugins → yashpatel-py → install); then a new session

# Gemini CLI (order tools listed in excludeTools for this extension's server entry, "Verify"; Robinhood sign-in untested)
gemini extensions install https://github.com/yashpatel-py/robinhood-trading-agent-skills

# Any agent via openskills (AGENTS.md)
npx openskills install yashpatel-py/robinhood-trading-agent-skills

# Connect Robinhood's official connector (OAuth in a desktop browser); skip if already connected
claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading     # then /mcp → robinhood-trading → authenticate
codex mcp add robinhood-trading --url https://agent.robinhood.com/mcp/trading                # then /mcp → select
```

Step by step, per surface: [Claude Code](docs/install-claude-code.md) · [Claude Desktop](docs/install-claude-desktop.md) ·
[claude.ai](docs/install-claude-ai.md) · [ChatGPT](docs/install-chatgpt.md) · [Codex](docs/install-codex.md) ·
[Cursor](docs/install-cursor.md) · [Gemini CLI](docs/install-gemini.md) · [Grok](docs/install-grok.md).

**You need:** a US Robinhood account (a primary individual investing account in good standing) and a
desktop browser. The Agentic account is opened through prompted onboarding when you first connect an
agent, and it counts toward Robinhood's cap of 10 self-directed accounts. Crypto needs a Robinhood
Crypto account and is unavailable in some states, including New York.

## Safety model: what is enforced and what is advised only

<!-- BEGIN safety-table -->
| Surface | How it's installed | Order-placing tools | Cancels / deletes | Audit log | Honest label |
|---|---|---|---|---|---|
| Claude Code + plugin | `/plugin install robinhood-trading@yashpatel-py` | **Blocked** by a fail-closed PreToolUse hook (opt-in confirm mode, off by default: a permission prompt for the exact ticket you reviewed) | **Ask** (enforced) | Yes | **Enforced** for MCP tool calls in the session. The hook doesn't see shell commands, so keep Bash permission prompts on (see the gaps). Exception: native Windows without `sh` fails **open**; add the deny snippet with your server's names |
| Claude Code, skills only | `npx skills add …` or a copy | Advised, unless you add `settings.deny.json`, which Claude Code then enforces for the server names it lists (two; add yours if `/mcp` shows another) | Advised | No | Advised (enforced with the deny snippet, for the server names in it) |
| Codex CLI/app | `.codex-plugin` (bundles the guard; trust it in `/hooks`), or `integrations/codex/hooks.json` for skills-only installs | Hook deny **once verified** | Advised | No | "Verify": advised until a first-run test passes |
| Cursor | `.cursor/skills` or `.agents/skills` + `integrations/cursor/hooks.json` | Hook deny **once verified** | Advised | No | "Verify": advised until a first-run test passes |
| Gemini CLI | `gemini extensions install …` | Listed in `excludeTools` for the extension's own server entry (**once verified**) | Advised | No | "Verify": advised until a first-run test shows the five tools hidden. Sign-in to Robinhood through Gemini: untested |
| Claude Desktop / claude.ai | per-skill zip upload | Advised | Advised | No | **Advised only** |
| ChatGPT / Grok / other MCP clients | single-file bundle uploaded as a project file | Advised | Advised | No | **Advised only** |
| Every surface | — | Robinhood's own hard limits: the separately funded Agentic account balance and one-tap disconnect | | | Always true |
<!-- END safety-table -->

The hard limits are Robinhood's: the agent can trade only what you fund in the Agentic account, and
you can disconnect it in the app with one tap. On native Windows without `sh`, the Claude Code hook
cannot start and fails open. The hook sees the agent's MCP tool calls, not its shell commands: an
agent allowed to run Bash without asking can start another `claude` with hooks off, or call
Robinhood's endpoint directly with the stored sign-in token. Keep Bash permission prompts on in
sessions with the Robinhood connector, and don't use bypass or unattended auto mode there. Evidence
for every label, and the gaps we know about: [docs/safety-model.md](docs/safety-model.md).

## The skills

| Skill | The question it answers | What it protects (sandbox figures) |
|---|---|---|
| `robinhood-trading` (core) | "What do I have, what would this order cost, and is it safe to send?" | a buy of 3 AMD that would wash $123.60 of a loss taken in another account, caught before the ticket <!-- golden: amd_rebuy_wash.disallowed_usd --> |
| `robinhood-tax-loss-harvesting` | "Will my IRA, a recurring buy or a DRIP wash this loss? What can I harvest?" | $390.00 of TSLA loss a Roth IRA buy would erase for good; clean sale date 2026-12-07 <!-- golden: tsla_wash.disallowed_usd tsla_wash.earliest_clean_sale_date --> |
| `robinhood-exit-guardian` | "Does every position have an exit, or at least a phone alert? Did anything fire?" | $84,467.50 of positions with no working exit or alert, listed with each gap <!-- golden: protection_audit.value_unprotected_usd --> |
| `robinhood-options-monitor` | "Did my options hit my rules? What expires, gets assigned or auto-exercises?" | a $130,000.00 auto-exercise cash need against $2,480.00 of buying power, four days out <!-- golden: spy_auto_exercise.cash_needed_usd spy_auto_exercise.buying_power_usd --> |
| `robinhood-agent-report-card` | "What did my agent actually do, and did anything else trade in my account?" | an agent-tagged order this machine never sent, and an injection attempt the guard blocked |
| `robinhood-options-screener` | "Run my options screener." (only when you ask) | entries only inside your own saved criteria; an unset value stops the run instead of being filled in |

Each skill works when copied alone and shares one set of connector rules, synced from `shared/`.

## Sharp edges it encodes

| Trap | What Preflight does |
|---|---|
| Two account-number fields; the P&L tools take the **rhs value** under the key `account_number` (identical on some accounts, so a wrong call can still succeed) | A per-tool key/value table, checked by eval mocks |
| `get_accounts` looks like it has buying power, but it isn't reliable for that | `get_accounts` names the accounts; buying power always comes from `get_portfolio` for the account in question |
| An agent can read every account but trade in one, so a buy in your IRA can **permanently** wash a loss sold by your agent, and neither per-account 1099 flags it | A cross-account 61-day wash check, including recurring and DRIP buys, reading every page |
| `place_advanced_order` (a real-money OCO) was missing from most "never place" lists | A class rule + a hook on `place_*`/`exercise_*`/`replace_*` + a CI coverage check |
| The OCO tools can be listed yet answer "the tool you requested cannot be found or does not exist" for your account | Read as "not enabled", never as "no OCOs"; falls back to stop orders and alerts |
| A market order at 8 pm queues for the next open (tagged regular) or is rejected (tagged to another session) | Session-aware tickets; a side-aware marketable limit (a buy at or above the ask, a **sell at or below the bid**) |
| There is no market-hours tool (agents guess `get_market_hours`, which does not exist), and the index quote's `state` came back empty | An offline NYSE calendar + quote timestamps + the time you state |
| `get_option_positions` returns **closed** positions unless `nonzero=true` | Always sent; eval-checked |
| Option reviews need options level 2 for single legs, and level 3 on a margin or limited-margin, non-retirement account for spreads; a cash account gets to level 3 only through limited margin | Level and account type checked first; the right upgrade link, in the right order, and never an upgrade call when the level is already enough |
| Cancelling a stop or OCO **increases** risk; `cancel_advanced_order` kills both legs; `cancel_option_exercise` cancels **every** queued exercise | Case-specific warnings + a hook prompt |
| The equity review returns its pre-trade checks as an object (`{}` when there are none) plus a market-data disclosure Robinhood requires shown verbatim | Both quoted verbatim on every ticket; `{}` is never read as approval |
| Trade-history rows carry no asset class: an option close appears under the stock's ticker | A row counts as a share sale only when it matches a filled stock sell |
| OCO: whole shares only, regular hours only, prices ≥ 0.25% from market and ≥ $0.10 apart, and the stop leg is stop-market | Pre-validated before review; the fractional remainder and gap risk are shown |
| "Alert me when price crosses the 50-day" is `price_crosses_sma`, not `sma_crosses`; `create_alert` requires an explicit `period` (the 9-bar SMA/EMA default belongs to `get_equity_technical_indicators`, not alerts) | Alert builder + explicit periods |
| Crypto alerts without `asset_class` can land on an ETF that shares the ticker | `asset_class: crypto` always |
| `mark_alerts_read all_through` clears **every** symbol | Mark by event id only |
| `create_scan` is permanent; scan updates **replace** the whole filter set (hidden columns become visible unless re-sent); a new scan built in steps isn't atomic; Cortex scans are read-only | `preview_scan` by default; read-modify-write with the complete set |
| Crypto dollar-sized market sells can come back up to ~5% light; a stop with no time in force is **day-only**; GTC means 90 days | Shown on every crypto ticket |
| `get_financials` returns only 4 metrics | Balance sheet and cash flow come from SEC facts |
| The PDT rule was eliminated on 2026-06-04 (FINRA RN 26-10) | No day-trade counting; PDT alerts from the review are shown verbatim |

## Eval scorecard (mocked Robinhood connector)

<!-- BEGIN generated:scorecard-excerpt -->
NOT YET RUN for v2.0.0. The safety cases (WITH / SKILLS-ONLY / W/OUT, on Haiku, Sonnet and Opus) are published here, with the run date and model ids, before the v2.0.0 tag.
<!-- END generated:scorecard-excerpt -->

51 cases on a mock of all 81 tools, including five prompt-injection texts, run with the full plugin,
with the skills but no hooks, and with no kit. What each case checks: [docs/eval-scorecard.md](docs/eval-scorecard.md).

## What we could not find elsewhere, as of 2026-09-22

Each line is a search result, not a boast: we found none as of 2026-09-22, and the searches are
listed so you can re-run them.

- **A wash-sale check your AI agent runs over Robinhood's MCP before it sells at a loss or buys back**, across all your Robinhood accounts (Agentic, individual, IRA, Roth), including recurring and DRIP buys and IRA purchases that erase a loss for good. Standalone tax software can check a household; the evidence page names what we found ([evidence](docs/claims.md#h1)).
- **An exit or a backstop for every position**: stops and OCOs audited, exits simulated in the Agentic account, native alerts that notify your phone for holdings in any account ([evidence](docs/claims.md#h2)).
- **Order placement blocked by code in Claude Code**, with a with/without eval suite on a mock of all 81 tools; its scorecard results are pending the first run ([evidence](docs/claims.md#h3)).
- **A record of what your agent did**, including orders from another agent, app or machine, and a tamper-evident local log of blocked attempts ([evidence](docs/claims.md#h4)).
- **Options expiration, assignment and auto-exercise cash needs**, before they land ([evidence](docs/claims.md#h5)).
- **Every tool and parameter verified against the live 81-tool connector**, with a public connector changelog ([evidence](docs/claims.md#h6)).

## Configuration and scheduling

Zero config works: a skill asks for what a run needs. To save your rules (exit levels, screener
criteria, policy limits, tax settings), see [docs/config.md](docs/config.md); every financial value
ships as `UNSET`, and only you set one. To run the guardian, the options monitor or the report card on
a schedule (`/loop`, desktop scheduled tasks, and why cloud routines may not see your connector), see
[docs/scheduling.md](docs/scheduling.md).

## What it won't do

- **Place orders by default.** `place_equity_order`, `place_option_order`, `place_crypto_order`,
  `place_advanced_order` and `exercise_option` are off. In Claude Code with the plugin a hook enforces
  that; confirm mode (off unless you switch it on) turns a reviewed `place_*` into a permission prompt,
  and `exercise_option` stays blocked. Scheduled runs never place.
- **Choose for you.** No sizing, no entry calls, no stop, target or option price of its own, no "best"
  tax lot, no "substantially identical" ruling. It shows the evidence and the dates; the call is yours.
- **Cancel or delete quietly.** Every cancel says what protection it removes and waits for your yes.
- **Claim performance.** No returns or win rates in its marketing. The report card states each rate
  with its sample size, labels its SPY comparison a small sample, and says what it cannot measure.
- **Touch anything but Robinhood's official connector.** No unofficial APIs, no credentials, no
  telemetry and no network calls from the kit's scripts or hooks. The third-party `npx skills`
  installer is different: it sends anonymous install telemetry (repository and skill names) to
  Vercel's skills.sh service. Run it with `DISABLE_TELEMETRY=1` or `DO_NOT_TRACK=1`, or install with
  `/plugin install` or from a git clone.

## Disclaimer

> **Unofficial.** Preflight is an independent open-source project. It is not affiliated with, endorsed by or sponsored by Robinhood Markets, Inc. or its affiliates, Anthropic, OpenAI or Google. "Robinhood" is a trademark of its owner and is used here only to describe compatibility.
>
> **Not advice.** This project provides general, impersonal information and software. It does not provide investment, financial, legal or tax advice, and it is not a signal service or a promise of any result. It computes rules, dates and dollar amounts from your own data; decisions are yours. Consult a qualified professional for tax questions.
>
> **Orders.** By default this kit simulates orders with Robinhood's review and preview tools and does not place them. That is enforced by code only in Claude Code, for the MCP tool calls the plugin's hook sees (or, with the deny snippet, for the server names in it); in Gemini CLI the extension is configured to hide the order tools, which is not yet verified; elsewhere it is an instruction the model may not follow. Robinhood "does not control, supervise, monitor, recommend, or audit" third-party agents, and your data leaves Robinhood's environment once it reaches your AI provider. The hard limits are Robinhood's: fund the Agentic account only with what you can afford to lose, and use the in-app disconnect.
>
> **Risk.** Trading involves risk of loss, including your entire investment. AI agents make mistakes, misread instructions and act on stale data. Read every ticket before acting on it.

**Not affiliated.** No Robinhood logo, feather or colors are used here, and nothing in this repository
is endorsed by Robinhood. The connector is Robinhood's; the skills, hooks and tests are this project's.

## Contributing

The most valuable report is connector behavior we got wrong: a tool that answers differently than a
skill says. Use the [connector-behavior issue form](../../issues/new?template=connector-behavior.yml),
and report a way past the order guard privately ([SECURITY.md](SECURITY.md)). How to run the sandbox,
the tests and the evals: [CONTRIBUTING.md](CONTRIBUTING.md). Adapting it to another broker's MCP: the
research ladder in `skills/robinhood-trading/references/research.md` is mostly broker-agnostic, while
`orders.md` and the connector rules are Robinhood-specific.

MIT licensed. See [LICENSE](LICENSE).
