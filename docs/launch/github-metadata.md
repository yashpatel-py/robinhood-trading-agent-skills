# GitHub metadata

<!-- golden-figures: -->
<!-- allow-figures: -->

**When:** Thu Sep 24, 2026, with the public push. The maintainer runs these by hand, once, from an
authenticated `gh` session. Nothing here runs in CI. Check the result on the repository page after.

## As it was on 2026-09-22

- Description: "An open Agent Skill (SKILL.md) for the Robinhood Trading MCP connector: market
  research, screening, portfolio and P&L monitoring, and pre-trade order review. Works in Claude
  Code, Claude Desktop, ChatGPT, Cursor, and other SKILL.md agents."
- Homepage: empty.
- Topics (10): `agent-skills`, `algorithmic-trading`, `anthropic`, `chatgpt`, `claude-code`,
  `claude-skills`, `mcp`, `model-context-protocol`, `robinhood`, `skill-md`.

## Private vulnerability reporting (first, before SECURITY.md goes public)

`SECURITY.md` and the issue-template contact link send every guard bypass to
`/security/advisories/new`. That page works for outside reporters only when private vulnerability
reporting is on, and it was still off when checked on 2026-09-27. Turn it on, then check it:

```sh
gh api -X PUT repos/yashpatel-py/robinhood-trading-agent-skills/private-vulnerability-reporting
gh api repos/yashpatel-py/robinhood-trading-agent-skills/private-vulnerability-reporting --jq .enabled   # expect: true
```

Re-run the check after any repository transfer, re-creation or fork you publish instead. While it
is off, the only private route is the fallback in `SECURITY.md` (a detail-free "Private security
contact request" issue, answered with a draft advisory), so watch for those issues.

## Description (158 characters; value first, "Unofficial" first word)

```sh
gh repo edit yashpatel-py/robinhood-trading-agent-skills \
  --description "Unofficial skills for Robinhood Agentic Trading: wash-sale checks across accounts, exits and phone alerts, checked order tickets. Places no orders by default."
```

"Places no orders by default" rather than "never places": confirm mode exists (off unless the user
turns it on), so "never" would overstate it.

## Topics (20, GitHub's maximum)

Add the 11 discovery topics and drop `anthropic`: it is the one topic that could read as an
affiliation, and dropping it keeps the count at 20. `gemini-cli-extension` is also what the Gemini CLI
extension gallery crawls for.

```sh
gh repo edit yashpatel-py/robinhood-trading-agent-skills \
  --remove-topic anthropic \
  --add-topic claude-code-plugin,claude-plugin,codex-skills,skills-sh,gemini-cli-extension,cursor-skills,options-trading,tax-loss-harvesting,agentic-trading,trading-agent,ai-trading
```

Resulting set: `agent-skills`, `agentic-trading`, `ai-trading`, `algorithmic-trading`, `chatgpt`,
`claude-code`, `claude-code-plugin`, `claude-plugin`, `claude-skills`, `codex-skills`,
`cursor-skills`, `gemini-cli-extension`, `mcp`, `model-context-protocol`, `options-trading`,
`robinhood`, `skill-md`, `skills-sh`, `tax-loss-harvesting`, `trading-agent`.

## Discussions and the pinned changelog issue

```sh
gh repo edit yashpatel-py/robinhood-trading-agent-skills --enable-discussions

gh issue create --repo yashpatel-py/robinhood-trading-agent-skills \
  --title "Connector changelog" \
  --label connector-behavior \
  --body "What changed in Robinhood's Trading MCP connector, as this project observes it. Each entry links the dated diff in connector/CHANGES.md. Report behavior the skills get wrong with the connector-behavior issue form; report a way past the order guard privately (SECURITY.md)."

gh issue pin <number printed by the command above> --repo yashpatel-py/robinhood-trading-agent-skills
```

The `connector-behavior` label comes from the issue templates in `.github/`; create it first
(`gh label create connector-behavior --repo yashpatel-py/robinhood-trading-agent-skills`) if the
command above says it doesn't exist.

## Homepage (later)

When the skills.sh page exists (it appears after the first `npx skills add` installs):

```sh
gh repo edit yashpatel-py/robinhood-trading-agent-skills \
  --homepage "https://skills.sh/yashpatel-py/robinhood-trading-agent-skills"
```

## Not settable from gh

- **Social preview:** Settings → General → Social preview → upload a 1280×640 PNG under 1 MB reading
  "Preflight · Unofficial". No Robinhood logo, feather or colors.
- **Release immutability and tag protection:** Settings → Rules, if you want tags to stay put after
  `gh skill publish` pins them.
