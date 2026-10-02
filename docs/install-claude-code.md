# Install: Claude Code

*Unofficial. Not affiliated with Robinhood Markets, Inc.* Safety label: **Enforced** with the plugin, for the MCP tool calls its hook sees (not for shell commands; see below); advised with skills only ([why](safety-model.md)).

> **Before you start**
> - US only. You need a primary individual investing account in good standing.
> - The Agentic account is opened through prompted onboarding when you first connect an agent, from a desktop browser, and it counts toward the 10-account cap.
> - Crypto needs a Robinhood Crypto account and is unavailable in some states, including New York.

**1. Connect Robinhood's connector** (skip if `/mcp` already lists it):

```sh
claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading
```

In Claude Code, run `/mcp`, select `robinhood-trading` and authenticate in a desktop browser. On
first connection Robinhood prompts you to open the Agentic account. Fund it only with what you are
willing to let an agent trade: that balance is the most any agent can touch.

**2. Install the plugin** (skills, fail-closed order guard, cancel prompts, local audit log):

```text
/plugin marketplace add yashpatel-py/robinhood-trading-agent-skills
/plugin install robinhood-trading@yashpatel-py
```

The same from a shell: `claude plugin marketplace add yashpatel-py/robinhood-trading-agent-skills`,
then `claude plugin install robinhood-trading@yashpatel-py`. Third-party marketplaces don't
auto-update by default; run `/plugin marketplace update yashpatel-py` to pick up new versions.
The marketplace also lists `unofficial-rh-connector`, which adds the same Robinhood endpoint as
step 1. Install it only instead of step 1, never as well: two server entries expose the same tools twice.

**Skills only, no plugin** (no hook, so the rule is advised): `npx skills add
yashpatel-py/robinhood-trading-agent-skills`, or clone the repository and symlink
`skills/<name>` into `~/.claude/skills/`. (The `npx skills` installer is Vercel's, not this project's,
and sends anonymous install telemetry; set `DISABLE_TELEMETRY=1` or `DO_NOT_TRACK=1` to opt out.)
To have Claude Code enforce the block anyway, merge
[`integrations/claude-code/settings.deny.json`](../integrations/claude-code/settings.deny.json) into
`~/.claude/settings.json`. It names the servers `robinhood-trading` and the helper plugin; if `/mcp`
shows another name for yours, add the five lines for it (`mcp__<name>__place_equity_order`, …; no parentheses).

**3. Check it works.** Start a new session. With the plugin, it opens with a line beginning
`Robinhood order mode: SIMULATE-ONLY`. Then ask:

- "What's my buying power and how did the last 90 days go?" A correct answer calls `get_accounts` to
  find the Agentic account, takes buying power from `get_portfolio` (never from `get_accounts`), uses
  `get_realized_pnl` with an explicit `3month` span, and shows account numbers as `••••` plus the last 4.
- "I want to put $2k into PLTR. What would that look like?" It should first ask what kind of order you
  want (a dollar amount can only be a market order, in regular hours), then simulate it with
  `review_equity_order`, show the ticket with Robinhood's pre-trade checks and disclosure, and say
  **Nothing was placed.**
- "Check that the order guard actually works." With the plugin it runs a local self-test that pipes a
  synthetic event into the hook; it never calls a real order tool.

If it tries to place the order, the skill didn't load; with the plugin, the hook blocks it anyway.

**Settings.** Order mode, the audit log and exempt servers are plugin settings: [config.md](config.md).
Confirm mode is off unless you turn it on: [confirm-mode.md](confirm-mode.md). **Windows:** the hook
runs under `sh`; without Git for Windows or WSL it cannot start and fails open, so add the deny
snippet, with the lines for your server name if it isn't one of the two the snippet lists.

**Keep Bash prompts on.** The hook sees the agent's MCP tool calls, not its shell commands. An agent
that can run Bash without asking could start another `claude` with hooks off, or call Robinhood's
endpoint directly with the sign-in token Claude Code stores. In sessions with the Robinhood connector,
don't use `bypassPermissions`, unattended auto mode or a blanket Bash allow rule, and read each Bash
prompt before approving it ([gaps we know about](safety-model.md#gaps-we-know-about)).

**Remove.** `/plugin uninstall robinhood-trading@yashpatel-py`, `claude mcp remove robinhood-trading`,
and disconnect the agent in the Robinhood app.
