# Install: Claude Desktop

*Unofficial. Not affiliated with Robinhood Markets, Inc.* Safety label: **advised only**. Claude
Desktop has no hook layer for this kit, so "never place orders" is an instruction the model follows,
not a block ([why](safety-model.md)). Robinhood's own limits still apply: the agent can trade only
what you fund in the Agentic account, and you can disconnect it in the app.

> **Before you start**
> - US only. You need a primary individual investing account in good standing.
> - The Agentic account is opened through prompted onboarding when you first connect an agent, from a desktop browser, and it counts toward the 10-account cap.
> - Crypto needs a Robinhood Crypto account and is unavailable in some states, including New York.

**1. Connect Robinhood's connector.** Settings → Connectors → Add custom connector → paste
`https://agent.robinhood.com/mcp/trading` → complete the OAuth sign-in. On first connection
Robinhood prompts you to open the Agentic account.

**2. Add the skills.** From the [latest release](https://github.com/yashpatel-py/robinhood-trading-agent-skills/releases/latest),
download `robinhood-trading.zip` and any of the others you want:

| Zip | What it adds |
|---|---|
| `robinhood-trading.zip` | the core skill: portfolio, research, checked tickets (install this one first) |
| `robinhood-tax-loss-harvesting.zip` | wash-sale checks across accounts, lots, harvest scan |
| `robinhood-exit-guardian.zip` | protection audit, exits, native price alerts |
| `robinhood-options-monitor.zip` | your options against your rules; expiration and assignment radar |
| `robinhood-agent-report-card.zip` | what the agent did (the audit-log parts need Claude Code) |
| `robinhood-options-screener.zip` | your config-driven screener (only when you ask for it) |

Then Settings → Capabilities → Skills → upload each zip. Check `SHA256SUMS` in the release if you
want to verify the files.

**3. Save your rules (optional).** Desktop skills can't read a config file from your home directory
unless a file-access tool is enabled. The dependable way is to paste a block starting with
`# robinhood-skills:config` into a project's instructions ([config.md](config.md)).

**4. Check it works.** Ask "What's my buying power?" You should get one line from `get_portfolio`
with the account shown as `••••` plus its last 4. Then ask "I want to put $2k into PLTR. What would
that look like?" It should simulate the order and say **Nothing was placed.**

**Scheduling.** Desktop scheduled tasks can run the guardian, the options monitor or the report card
on a timer while the app is running ([scheduling.md](scheduling.md)).

**Remove.** Delete the skills under Settings → Capabilities, remove the connector under Settings →
Connectors, and disconnect the agent in the Robinhood app.
