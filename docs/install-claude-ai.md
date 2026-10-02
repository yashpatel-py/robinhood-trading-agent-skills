# Install: claude.ai

*Unofficial. Not affiliated with Robinhood Markets, Inc.* Safety label: **advised only**. claude.ai
has no hook layer for this kit, so "never place orders" is an instruction, not a block
([why](safety-model.md)). Robinhood's limits still apply: only what you fund in the Agentic account
can be traded, and you can disconnect the agent in the app.

> **Before you start**
> - US only. You need a primary individual investing account in good standing.
> - The Agentic account is opened through prompted onboarding when you first connect an agent, from a desktop browser, and it counts toward the 10-account cap.
> - Crypto needs a Robinhood Crypto account and is unavailable in some states, including New York.

**1. Connect Robinhood's connector.** Settings → Connectors → Add custom connector → paste
`https://agent.robinhood.com/mcp/trading` → complete the OAuth sign-in in a desktop browser.

**2. Add the skills.** Download `robinhood-trading.zip`, and any of the other five skills you want,
from the [latest release](https://github.com/yashpatel-py/robinhood-trading-agent-skills/releases/latest).
Upload each one under Settings → Capabilities → Skills. Start with `robinhood-trading.zip`: it holds
the connector rules the others build on, and each zip also works alone.

**3. Put your rules in a project (optional).** claude.ai has no config file. Create a project and
paste your rules into its instructions, starting with the line `# robinhood-skills:config`
(format and templates: [config.md](config.md)). Every financial value ships as `UNSET`; set only the
ones you have decided on.

**4. Check it works.** In a chat with the connector enabled, ask "What's my buying power?" A correct
answer is one line from `get_portfolio` with the account masked as `••••` plus its last 4. Then ask
"Buy $2,000 of PLTR at market." It should simulate the order with `review_equity_order`, show the
ticket and say **Nothing was placed.**, with the note that Robinhood doesn't document placing orders
by hand inside the Agentic account.

**Scheduling.** Scheduled tasks can run a saved prompt such as "Run my protection check". Run it
once by hand first: if it answers `CONNECTOR UNAVAILABLE`, the connector isn't reaching that run
([scheduling.md](scheduling.md)).

**Remove.** Delete the skills under Settings → Capabilities, remove the connector under Settings →
Connectors, and disconnect the agent in the Robinhood app.
