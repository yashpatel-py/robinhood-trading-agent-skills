# Awesome-list entries

<!-- golden-figures: -->
<!-- allow-figures: -->

Unofficial; not affiliated with Robinhood Markets, Inc. One resource per submission, factual one-line
descriptions, no emojis, no superlatives, no vote requests. Follow each list's CONTRIBUTING file as it
is on the day you submit; the formats below were read on 2026-09-22.

Repository URL: `https://github.com/yashpatel-py/robinhood-trading-agent-skills`

## hesreallyhim/awesome-claude-code (Sat Sep 26)

Web issue form only (`/issues/new?template=recommend-resource.yml`), filed by the maintainer by hand;
pull requests and `gh` submissions are not accepted. Eligible because the repository is more than 14
days old and has active development after its first day.

- **Name:** Preflight for Robinhood Agentic Trading
- **Category:** Agent Skills (or Hooks, if the form asks for one category that fits a plugin with hooks)
- **Description:**

```text
Unofficial Claude Code plugin for Robinhood's agent connector: cross-account wash-sale checks, exit and alert audits, options expiration checks and checked order tickets, with a fail-closed PreToolUse hook that blocks order placement and an eval suite on a mock of all 81 tools.
```

## ComposioHQ/awesome-claude-skills (Mon Oct 5)

Pull request per its CONTRIBUTING (a real use case, documentation, examples, "confirm before
destructive operations"). There is no finance category yet; propose one in the PR description.

```text
- [Preflight for Robinhood Agentic Trading](https://github.com/yashpatel-py/robinhood-trading-agent-skills) - Unofficial skills for Robinhood's agent connector: wash-sale checks across accounts, exits and price alerts, checked order tickets; prepares orders without placing them and asks before any cancel.
```

PR description: "Proposes a Finance section. The entry has worked examples on synthetic data (docs/examples/),
runs without a brokerage account through a local sandbox MCP server, and asks for confirmation before
cancels and deletions."

## travisvn/awesome-claude-skills (Mon Oct 5)

Fork, add under the section closest to finance or integrations, open a pull request.

```text
- [Preflight for Robinhood Agentic Trading](https://github.com/yashpatel-py/robinhood-trading-agent-skills) - Unofficial skills and a Claude Code plugin for Robinhood's agent connector: cross-account wash-sale checks, exit audits, options checks and checked order tickets.
```

## heilcheng/awesome-agent-skills (Mon Oct 5)

Pull request using the list's template. Its rule that a `SKILL.md` stays under 500 lines holds for all
six skills.

```text
- [robinhood-tax-loss-harvesting](https://github.com/yashpatel-py/robinhood-trading-agent-skills/tree/main/skills/robinhood-tax-loss-harvesting) - Unofficial. Wash-sale checks across Robinhood accounts (Agentic, individual, IRA, Roth) before a loss sale or rebuy, with dates and lot choices.
```

## wilsonfreitas/awesome-quant (Tue Oct 6)

Pull request with the list's entry format `[Name](link) - Language - description`:

```text
[Preflight for Robinhood Agentic Trading](https://github.com/yashpatel-py/robinhood-trading-agent-skills) - Python - Unofficial agent skills: cross-account wash-sale checks, exit protection, simulate-only order guard.
```

## VoltAgent/awesome-agent-skills (Mon Nov 2)

It rejects brand-new skills, so submit only once the skills have real installs. Format
`- **[author/skill-name](url)** - description` with at most 10 words:

```text
- **[yashpatel-py/robinhood-tax-loss-harvesting](https://github.com/yashpatel-py/robinhood-trading-agent-skills/tree/main/skills/robinhood-tax-loss-harvesting)** - Wash-sale checks across all Robinhood accounts, including IRAs
```

## After the Codex and Cursor first-run tests pass

spencerpauly/awesome-cursor-skills and hashgraph-online/awesome-codex-plugins, by pull request, only
once `docs/safety-model.md` records a passing first-run test for that client:

```text
- [Preflight for Robinhood Agentic Trading](https://github.com/yashpatel-py/robinhood-trading-agent-skills) - Unofficial skills for Robinhood's agent connector: cross-account wash-sale checks, exit audits and checked order tickets, with an order-placement guard hook.
```

## Listings that need no pull request

- **skills.sh:** appears automatically after `npx skills add` installs; then check its audit row
  (Gen Agent Trust Hub, Socket, Snyk) and fix any flag.
- **Anthropic community marketplace (Sun Sep 27):** the Console form at
  `platform.claude.com/plugins/submit`, only the `robinhood-trading` plugin, category finance, after
  `claude plugin validate . --strict` passes. Never submit the `unofficial-rh-connector` helper.
- **Gemini CLI extension gallery:** crawled daily from the `gemini-cli-extension` topic, the root
  `gemini-extension.json` and a tagged release whose version equals the tag.
- **SkillsMP, Skills Directory, LobeHub, claudemarketplaces:** indexed automatically; claim the
  listing where offered and check any security scan result.
- **ClawHub (optional, October or later):** `clawhub skill publish ./skills/<skill>` per skill.

## Never

awesome-mcp-servers and the MCP registries list MCP servers their authors own. This project owns no
server (the connector is Robinhood's), so it does not belong there.
