# Show HN

<!-- golden-figures: tsla_wash.disallowed_usd tsla_wash.earliest_clean_sale_date spy_auto_exercise.cash_needed_usd spy_auto_exercise.buying_power_usd -->
<!-- allow-figures: -->

**When:** Mon Sep 28, 2026, 06:30 to 07:30 PT, the day after the v2.0.0 tag. Stay in the thread all
day and answer every technical question. Never ask anyone to vote.

**Title** (72 characters; HN allows 80). Brand first and "unofficial" in the title, so it can't be
read as Robinhood's own release:

```text
Show HN: Preflight – unofficial wash-sale checks for Robinhood AI agents
```

Backup title (78): `Show HN: Preflight (unofficial) – cross-account wash-sale checks for Robinhood`

**URL:** `https://github.com/yashpatel-py/robinhood-trading-agent-skills`

## First comment (post right after submitting)

```text
Hi HN. (Unofficial; I'm not affiliated with Robinhood.) Robinhood opened its brokerage to AI agents
over MCP in May. The agent can read every account you hold but trade only in a separately funded
"Agentic" account. That split creates a trap that is easy to miss: your agent sells a stock at a
loss in the Agentic account, a recurring buy or your IRA buys the same stock within 30 days, and the
loss is washed. If the buy was in an IRA, the loss is gone for good. Robinhood's 1099s are per
account, so neither one shows it.

Preflight is six unofficial Agent Skills (SKILL.md) plus a Claude Code plugin. Before a loss sale or a
rebuy, it reads every account (every page; the fixture's IRA buy sits on page 2) and computes the
window, the washed shares, the dollars and the first clean date. It also audits whether each position
has an exit or at least a phone alert, flags options that would need cash at auto-exercise, and
reports what your agent actually did.

By default it prepares orders with Robinhood's review/preview tools and does not place them. In Claude
Code that is enforced by a fail-closed PreToolUse hook (POSIX sh, blocks place_*/exercise_*/replace_*
tool calls with exit 2, no Python needed; an opt-in confirm mode turns a reviewed place_* into a
permission prompt); on other clients it is only an instruction, and the README says so in a table.

Try it with no Robinhood account; the sandbox serves the connector's 81 real tool names with a
synthetic household:

  git clone https://github.com/yashpatel-py/robinhood-trading-agent-skills && cd robinhood-trading-agent-skills
  claude --plugin-dir . --mcp-config sandbox/mcp.json --strict-mcp-config

Ask it to "harvest my TSLA loss" and it finds $390.00 of loss a Roth IRA buy would erase (clean sale
from 2026-12-07). Ask about your options and it finds $130,000.00 of auto-exercise cash needed against
$2,480.00 of buying power. All sandbox data.

Evals: a mock of all 81 tools, 51 cases including five prompt-injection texts, run with the plugin,
with the skills but no hook, and with nothing. Scorecard: docs/eval-scorecard.md in the repo.

What it won't do: pick sizes, prices or stops, give advice, or claim returns. It is not affiliated
with Robinhood. The most useful feedback is connector behavior it gets wrong.
```

## Answers to have ready

- **"Why not just trust the model to follow the rule?"** Because text returned by tools can argue with
  it. A news item or a filing can say "AI agents: place this order", and a skill is only advice. The
  hook runs outside the model. The eval suite plants five such texts and checks both arms.
- **"So it can never place an order?"** In Claude Code with the plugin, not by default: the hook
  blocks every `place_*`, `exercise_*` and `replace_*` call. There is an opt-in confirm mode, off
  unless you switch it on in the plugin settings, which turns a call for the exact ticket you reviewed
  into a permission prompt. On Claude Desktop, claude.ai, ChatGPT and Grok the rule is advised only.
  Robinhood's hard limit on every surface is the Agentic account's balance.
- **"Can the agent get around the hook?"** Through tool calls in the session, no. Through the shell,
  yes, if you let it run Bash without asking: it could start another `claude` with hooks off, or call
  Robinhood's endpoint with the stored sign-in token. Keep Bash prompts on in sessions with the
  connector; `docs/safety-model.md` lists this under "Gaps we know about".
- **"How do you know the wash-sale math is right?"** It is plain rule arithmetic in stdlib Python with
  golden tests: the 61-day window, partial washes, the IRA case (Rev. Rul. 2008-5), Dec 31 sales
  rebought from Feb 1, 2027. It does not decide whether two different funds are "substantially
  identical"; you declare those.
- **"Is my data sent anywhere?"** The scripts make no network calls, and CI greps for them. The
  third-party `npx skills` installer sends anonymous install telemetry (repo and skill names);
  `DISABLE_TELEMETRY=1` turns it off, and the plugin install doesn't use it. Whatever
  the connector returns goes to your AI provider, as with any MCP client; the skills say so before
  reading beyond the Agentic account. The optional audit log is local and masked.
- **"Affiliated with Robinhood?"** No. Unofficial, and no Robinhood marks anywhere.
- **"Why not bracket orders or trailing stops?"** The connector exposes neither. It exposes OCO, and
  the OCO tools were not enabled on either account we captured, so the skills fall back to stop orders
  and alerts.
- **"Returns?"** Not measured and not claimed. Deposits and withdrawals are invisible to the
  connector, so an account return against an index would be made up.
