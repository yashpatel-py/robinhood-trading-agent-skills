# Security policy

*Unofficial. Not affiliated with Robinhood Markets, Inc.*

This kit sits between an AI agent and a live brokerage account. Its central promise is that, by
default, the agent prepares and simulates orders but never places them, and that cancels and
deletes need an explicit yes. A way around that promise is a security bug, and it is the one we
most want to hear about.

## If an order was placed that you did not approve

1. Protect the account first. Disconnect the agent in the Robinhood app (Robinhood lets you
   disconnect it at any time), and contact Robinhood support about the order itself. We can't see
   or change your account.
2. Then tell us privately, as described below. Say which surface you used (see the safety table in
   the README), the kit version, and what the agent was asked to do.

## How to report

Report privately through GitHub Security Advisories: the **Report a vulnerability** button on the
repository's Security tab, or
<https://github.com/yashpatel-py/robinhood-trading-agent-skills/security/advisories/new>.

If that page shows a 404 or says private reporting is off, use the fallback instead of giving up or
posting details: open an issue with the bug form, title it "Private security contact request", and
write only "private contact requested" in each required field, with **no details** at all. The
maintainer then opens a private draft advisory, adds you to it, and you send the report there.

**Never put security details in a public issue.** Never include real account numbers, tokens,
session identifiers or screenshots that show balances. Mask account numbers to the last 4
characters (••••X4F1). A reproduction against the sandbox (`sandbox/mock_server.py`, synthetic data,
no Robinhood account) is the most useful report there is.

## In scope (high severity)

1. Any path by which a `place_*`, `exercise_*` or `replace_*` call reaches a Robinhood server from a
   surface labeled **Enforced** in the README safety table while the order mode is simulate-only
   (the default). The shell-command route under "Limits, stated plainly" (a nested `claude` with
   hooks off, or a direct call to Robinhood's endpoint, from a Bash command that a bypass mode, auto
   mode or an allowlist ran without a prompt) is a documented limit, not a report. The same result
   in the default permission mode, with no Bash prompt approved by the user, is in scope.
2. A confirm-mode bypass. Confirm mode ships in 2.0.0, off by default. A bypass is a live order
   placed without a matching review from the same session, without a human permission prompt, over
   the configured notional cap, in a non-interactive or bypass permission mode, or through
   `exercise_option`, which stays blocked in every mode.
3. Skill text, templates or examples that lead an agent to place, cancel or delete something
   without the documented confirmation, including by following instructions found in tool output
   (news, filings, alert labels, scan titles and the like).
4. Leakage of full account numbers, tokens or balances through the audit log, the review ledger,
   hook output, eval artifacts, CI logs or anything else the kit writes.
5. Config or hook tampering that turns confirm mode on, raises the cap or disables the guard
   without the user doing it.
6. A mislabeled safety table: a surface claimed as "Enforced" without a recorded test.

## Out of scope

- Behavior on surfaces the safety table labels "advised" or "advised only". Nothing enforces the
  rules there, and the table says so.
- Bugs in Robinhood's MCP server or app. Report those to Robinhood.
- A user deliberately disabling or editing the hooks, or turning confirm mode on for themselves.
- Wrong numbers or missed checks with no safety impact. Please file those as ordinary issues.

## What to expect

- Acknowledgement within 72 hours.
- A fix or mitigation for a guard or confirm-mode bypass within 7 days, sooner when the bypass is
  practical to exploit.
- A release note that names the fixed version, and credit if you want it.

## Supported versions

The latest minor release (currently 2.0.x) gets security fixes. Older versions don't: upgrade the
plugin, or pull the latest skills.

## Limits, stated plainly

- The audit log is tamper-evident, not tamper-proof: it detects edits, but anything with write
  access to your home directory can remove it.
- The Claude Code hook sees the MCP tool calls of the session it runs in, not shell commands. An
  agent that can run Bash without a prompt (`bypassPermissions`, an allowlisted Bash, unattended auto
  mode) can start a nested `claude` with hooks off (`--settings '{"disableAllHooks":true}'`, `--bare`,
  `--setting-sources`, another `CLAUDE_CONFIG_DIR`), or read the MCP sign-in token Claude Code
  stores locally and call `https://agent.robinhood.com/mcp/trading` directly. The plugin's `config`
  hook is a pattern-matching tripwire, not a boundary, and can't be relied on to catch either. Keep
  Bash permission prompts on in sessions with the Robinhood connector; `docs/safety-model.md` ("Gaps
  we know about") lists what else helps.
- On native Windows without `sh`, the Claude Code hook fails open. Add
  `integrations/claude-code/settings.deny.json` there, plus the five `mcp__<name>__…` lines for your
  server name if `/mcp` shows one the snippet doesn't list (it names only `robinhood-trading` and
  the helper plugin's server).
- Robinhood's own limits apply on every surface: the Agentic account holds only what you fund it
  with, and you can disconnect the agent from the app.
