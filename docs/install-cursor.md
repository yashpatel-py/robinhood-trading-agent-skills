# Install: Cursor

*Unofficial. Not affiliated with Robinhood Markets, Inc.* Safety label: **Verify**. The order guard
for Cursor uses its `beforeMCPExecution` hook, and it stays advised until a first-run test passes on
a real Cursor build ([why](safety-model.md)).

> **Before you start**
> - US only. You need a primary individual investing account in good standing.
> - The Agentic account is opened through prompted onboarding when you first connect an agent, from a desktop browser, and it counts toward the 10-account cap.
> - Crypto needs a Robinhood Crypto account and is unavailable in some states, including New York.

**1. Connect Robinhood's connector** (the steps Robinhood documents): give your agent the link
`https://agent.robinhood.com/mcp/trading`, then Settings → Cursor Settings → Tools & MCPs →
Connect, and complete the OAuth sign-in in a desktop browser.

**2. Add the skills**, one of:

```sh
npx skills add yashpatel-py/robinhood-trading-agent-skills -a cursor
```

or copy `skills/<name>` folders into `.cursor/skills/` (one project) or `.agents/skills/`.

The `npx skills` installer is Vercel's, not this project's, and sends anonymous install telemetry
(repository and skill names); run it with `DISABLE_TELEMETRY=1` or `DO_NOT_TRACK=1` to opt out.

**3. Add the order guard (optional, "Verify").** Follow
[`integrations/cursor/README.md`](../integrations/cursor/README.md): merge its `beforeMCPExecution`
entry into `~/.cursor/hooks.json` or `<project>/.cursor/hooks.json` with your clone path, restart
Cursor, and run its first-run test against the sandbox. If it passes, please report it in a
`connector-behavior` issue with your Cursor version. Cancel prompts, the audit log and confirm mode
are Claude Code plugin features and don't exist here.

**4. Check it works.** Ask "What's my buying power?" (one line from `get_portfolio`, account masked
as `••••` plus its last 4), then "Put a take-profit at 180 and a stop at 142 on my AMD." It should
check the OCO rules (whole shares, regular hours, prices at least 0.25% from market), simulate with
`review_advanced_order` where your account allows it, and say **Nothing was placed.**

**Remove.** Delete the skill folders and the hook entry, disconnect the MCP server in Cursor
settings, and disconnect the agent in the Robinhood app.
