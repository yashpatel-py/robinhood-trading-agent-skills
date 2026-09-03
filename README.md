# claude-skills

Open [Agent Skills](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
you can drop into Claude, ChatGPT, or any agent that reads `SKILL.md`.

`SKILL.md` is an agent-neutral open format: YAML frontmatter (`name`, `description`) plus
markdown instructions. Claude Code, Claude Desktop, Cursor, OpenCode, Codex and others
load it natively; ChatGPT can use the same content as custom instructions plus knowledge
files. Setup for each is below.

**License:** MIT — use it, fork it, change it, ship it.

---

## Skills in this repo

### `robinhood-trading`

A working playbook for the [Robinhood Trading MCP](https://robinhood.com/us/en/support/articles/agentic-trading-overview/)
connector, covering market research, stock screening, portfolio and P&L monitoring, and
pre-trade order review.

Robinhood opened its brokerage to AI agents over MCP. The tools are powerful and the
documentation is thin in the places that cost you money, so most of this skill is the
knowledge you'd otherwise acquire by getting it wrong once.

**Two boundaries it holds on purpose:**

- **Simulate, then hand off.** It uses `review_*` / `preview_*`, which cost nothing and
  move no money, then shows you the estimated cost and every pre-trade alert — and leaves
  the actual order placement to you. It does not call the `place_*` tools.
- **Evidence, not verdicts.** It gives you what the data shows, the bear case, and what
  would falsify the thesis. It does not issue buy/sell recommendations, which depend on
  risk tolerance, tax situation, and time horizon that a model doesn't have.

**Sharp edges it encodes**, each of which is a real way to be wrong:

| Trap | What the skill does |
|---|---|
| Two account-number fields (`account_number` vs `rhs_account_number`) that are **identical on some accounts** — so a wrong call still succeeds until it doesn't | Read the parameter description per tool, never pattern-match |
| `get_accounts` looks like it returns buying power. It isn't reliable for that | Route buying-power questions through `get_portfolio` |
| A market order placed at 8pm doesn't error — it silently queues until the next open | Use a marketable limit tagged to the right session |
| Crypto market **sells** sized by dollar amount can come back ~5% light (buys ~1%) | Surface the collar before you commit |
| Updating a saved scan **replaces** its config — anything you don't repeat is dropped | Read current state with `get_scans` first |
| `review_option_order` needs options level 2+; below that it just fails | Check the level, route to the upgrade path |
| Quotes carry two price fields and two "previous close" fields with different meanings | Pick by timestamp, verify freshness, name the right close |

| File | What's in it |
|---|---|
| `SKILL.md` | Principles, account handling, price-freshness rules, the four workflows, report formats |
| `references/order-mechanics.md` | Order types, market sessions, fractional/notional rules, tax lots, options legs, crypto |
| `references/research.md` | Scanner workflow, research ladder, SEC filings, technicals, portfolio, watchlists |
| `references/tools.md` | Full tool map by family with per-tool gotchas |

Only `SKILL.md` loads when the skill triggers; the references are pulled in as needed.

---

## What you need first

This skill is a playbook for a connector — it does nothing on its own. To get value you
need the connector too:

1. **A Robinhood account.** US only. You need a primary individual investing account in
   good standing before you can open the agentic one.
2. **An agentic account.** Created automatically when you first connect an agent. It is
   separate from your main account and separately funded — that balance is the maximum an
   agent can touch, and every other account stays read-only.
3. **A desktop browser.** Robinhood only allows agentic account creation and agent
   authentication on desktop. On mobile, copy the onboarding URL and open it on a computer.
4. **The MCP endpoint:** `https://agent.robinhood.com/mcp/trading`

Crypto needs a Robinhood Crypto account and is unavailable in some states, including
New York.

**You can install and read the skill without any of this** — it's just markdown. It simply
won't have tools to call.

---

## Install

### Claude Code

Skills live in `~/.claude/skills/` (available everywhere) or `.claude/skills/` in a project
(that project only). No API upload, no restart ceremony — it's the filesystem.

**Option A — skills CLI** (easiest):

```bash
npx skills add yashpatel-py/claude-skills
```

**Option B — clone and symlink** (best if you want to pull updates):

```bash
git clone https://github.com/yashpatel-py/claude-skills.git ~/src/claude-skills
mkdir -p ~/.claude/skills
ln -s ~/src/claude-skills/robinhood-trading ~/.claude/skills/robinhood-trading
```

**Option C — just copy it:**

```bash
git clone https://github.com/yashpatel-py/claude-skills.git /tmp/claude-skills
mkdir -p ~/.claude/skills
cp -R /tmp/claude-skills/robinhood-trading ~/.claude/skills/
```

Then connect Robinhood:

```bash
claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading
```

Restart Claude Code and complete the OAuth flow in a desktop browser.

### Claude Desktop

1. **Connector:** Settings → Connectors → Add custom connector → paste
   `https://agent.robinhood.com/mcp/trading` → complete OAuth.
2. **Skill:** Settings → Capabilities → Skills → upload the `robinhood-trading` folder
   (zip it first if the picker wants a single file).

### claude.ai

Skills are managed in Settings → Capabilities. Upload the `robinhood-trading` folder the
same way. Connectors are added under Settings → Connectors with the same MCP URL.

### ChatGPT

ChatGPT has no `SKILL.md` system, so this takes two separate pieces. Both work; neither is
as automatic as Claude Code.

**1. Connect Robinhood via MCP.** Requires Plus, Pro, Business, Enterprise, or Edu —
custom connectors aren't available on Free.

- Settings → Apps → Advanced settings → enable **Developer mode**
- Add a connector with URL `https://agent.robinhood.com/mcp/trading`, transport
  Streamable HTTP, authentication OAuth
- Complete OAuth on desktop

> **Worth knowing:** write actions through MCP are limited on Plus and Pro; full MCP is
> documented for Business, Enterprise, and Edu. For *this* skill that barely matters —
> it's a read-and-simulate playbook by design, and it never places orders anyway.

**2. Load the skill content.** Two ways:

- **Custom GPT** (Pro/Plus): create a GPT, paste the body of `SKILL.md` (everything below
  the `---` frontmatter) into **Instructions**, then upload the three `references/*.md`
  files as **Knowledge**. Attach the Robinhood connector under Actions/Apps.
- **Project**: create a Project, paste the `SKILL.md` body into project instructions, and
  add the reference files to project files.

The frontmatter is Claude-specific plumbing for auto-triggering — ChatGPT doesn't need it,
and it's harmless to omit. The tradeoff is that ChatGPT won't load the skill on its own the
way Claude does; you get the behavior by working inside that GPT or Project.

### Cursor, OpenCode, Codex, and other SKILL.md agents

The format is agent-neutral. Most tools read `SKILL.md` from a skills directory — check
yours for the path, or use a universal loader:

```bash
npx openskills install yashpatel-py/claude-skills
```

---

## Checking it works

Ask something that should pull the skill in:

> what's my buying power and how did the last 90 days go?

A correct response calls `get_portfolio` (not `get_accounts`) for buying power, uses
`get_realized_pnl` for the window, masks account numbers to the last four digits, and
separates realized from unrealized. Then try:

> I want to put $2k into PLTR — what would that look like?

It should run `review_equity_order`, show you the estimated cost and every pre-trade
alert, and then stop and hand the placement to you. If it tries to place the order, the
skill didn't load.

---

## What it deliberately won't do

- **Place orders.** `place_equity_order`, `place_option_order`, `place_crypto_order`, and
  `exercise_option` are off-limits. You place them yourself, after seeing the review.
- **Give investment advice.** No "buy this," no position sizing, no entry calls.
- **Cancel without asking.** Cancels only remove exposure, but they're irreversible, so it
  confirms first.

If you want different boundaries, fork it — the reasoning behind each one is written out
in `SKILL.md`, so you can see what you'd be trading away.

---

## Adapting it

Most of the value transfers. `references/research.md` (the research ladder, SEC filing
workflow, scanner discipline) is largely broker-agnostic. `references/order-mechanics.md`
is Robinhood-specific. If you're wiring up a different brokerage MCP, that's the file to
rewrite and the rest largely stands.

To change how it reports, edit the templates in the `## Reporting` section of `SKILL.md` —
that's the highest-leverage edit for making output feel like yours.

---

## Disclaimer

This is a workflow playbook: markdown instructions for an AI agent. It contains no
strategy, no signals, no credentials, and no trading logic. It cannot access your accounts
— you authorize the Robinhood connector yourself, and you can revoke it in the Robinhood
app at any time.

Nothing here is investment, financial, legal, or tax advice. Trading involves risk of loss
including your entire investment. AI agents make mistakes, misread instructions, and act on
stale data. Robinhood does not control, supervise, monitor, recommend, or audit third-party
agents, and your data leaves Robinhood's environment once it reaches an AI provider. Fund
the agentic account only with what you can afford to lose, and read every review before you
place anything.

Not affiliated with Robinhood, Anthropic, or OpenAI.

---

## Contributing

Issues and PRs welcome — especially corrections. If you hit connector behavior this skill
gets wrong, that's the most valuable thing you can report.
