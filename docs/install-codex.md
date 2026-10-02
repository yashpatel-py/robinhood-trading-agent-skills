# Install: Codex (CLI and app)

*Unofficial. Not affiliated with Robinhood Markets, Inc.* Safety label: **Verify**. The order guard
hook for Codex exists but stays advised until a first-run test passes on a real Codex build
([why](safety-model.md)).

> **Before you start**
> - US only. You need a primary individual investing account in good standing.
> - The Agentic account is opened through prompted onboarding when you first connect an agent, from a desktop browser, and it counts toward the 10-account cap.
> - Crypto needs a Robinhood Crypto account and is unavailable in some states, including New York.

**1. Connect Robinhood's connector** (the steps Robinhood documents):

- Codex CLI: `codex mcp add robinhood-trading --url https://agent.robinhood.com/mcp/trading`, then
  `/mcp` and select it to sign in.
- Codex app: Settings → MCP servers → Streamable HTTP → add `https://agent.robinhood.com/mcp/trading`.

The OAuth sign-in happens in a desktop browser. On first connection Robinhood prompts you to open the
Agentic account.

**2. Add the skills**, one of:

```sh
# the plugin (the repo's .codex-plugin): register the marketplace, then install from it
codex plugin marketplace add yashpatel-py/robinhood-trading-agent-skills
codex plugin add robinhood-trading@yashpatel-py

# or SKILL.md folders only
npx skills add yashpatel-py/robinhood-trading-agent-skills
```

Adding the marketplace only registers it; nothing is installed until the second line (or install
`robinhood-trading` from `/plugins`). Start a new Codex session afterwards. You can also copy
`skills/<name>` folders into `~/.codex/skills/`, or use `$skill-installer` with the repo path.
`AGENTS.md` lists the six skills and when each applies.

`npx skills` is Vercel's installer and sends anonymous telemetry; set `DISABLE_TELEMETRY=1` to opt out.
**3. Turn on the order guard ("Verify").**
- **Plugin install** (`codex plugin add`): the guard is bundled (`.codex-plugin/hooks.json`), but Codex
  skips it until you review and trust it in `/hooks`. Do that, then run the first-run test in
  [`integrations/codex/README.md`](../integrations/codex/README.md) against the sandbox. Don't also
  merge `integrations/codex/hooks.json`: Codex runs matching hooks from every file, so the guard would
  run twice.
- **Skills-only install** (`npx skills`, copied folders, `$skill-installer`): merge the `PreToolUse`
  entry from `integrations/codex/hooks.json` into your Codex hooks file with your clone path, then run
  the same first-run test.

Report a pass in a `connector-behavior` issue with your Codex version, so the table can move from
"Verify" to "Enforced". Cancel prompts, the audit log and confirm mode don't exist in Codex, plugin
install included.

**4. Check it works.** Ask "What's my buying power?" (one line, account masked as `••••` plus its last
4), then "Sell 10 PLTR now" outside market hours: it should simulate a limit order at or below the bid,
explain why a market order would wait for the open, and say **Nothing was placed.**

**Remove.** `codex plugin remove robinhood-trading@yashpatel-py` (or uninstall it in `/plugins`),
optionally `codex plugin marketplace remove yashpatel-py`; or delete the skill folders. For a manual
(skills-only) install, also delete the hook entry you merged; the plugin's bundled hook goes with the
plugin. Then run `codex mcp remove robinhood-trading` (or remove the server in the app), and disconnect
the agent in the Robinhood app.
