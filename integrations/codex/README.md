# Order guard for Codex

*Unofficial — not affiliated with Robinhood Markets, Inc.*

**Status as of 2026-09-22: Verify.** This hook assumes Codex's `PreToolUse` hooks behave like
Claude Code's: exit code 2 blocks the call and shows stderr to the agent. This project has not yet
confirmed that on a real Codex build. Until you run the first-run test below and it passes, treat
order blocking in Codex as **advised**: the skills never call an order-placing tool, but nothing
outside the model enforces that.

## Why a hook at all

The skills already say "prepare the order; the user places it." That is an instruction, and an
instruction can be talked around: a prompt injection hidden in a news item or a filing, or a model
that decides the user "clearly meant it". Robinhood itself warns that an agent told to act without
approval can place trades without your confirmation. A hook runs outside the model, so no argument
reaches it. It is the difference between *advised* and *enforced*.

## What it does

- Before Codex runs an MCP tool whose name starts with `place_`, `exercise_` or `replace_`, on any
  server, the guard exits 2 and tells the agent that nothing was placed and that it should show the
  ticket and the handoff text. Today that covers `place_equity_order`, `place_option_order`,
  `place_crypto_order`, `place_advanced_order` and `exercise_option` (no `replace_*` tool is exposed
  today; the prefix is covered for when one appears).
- On a server whose name contains "robinhood", other money-like verbs are blocked too: `submit_`,
  `execute_`, `transfer_`, `withdraw_`, `deposit_`, `stake_`, `unstake_`, `convert_`, `send_`,
  `buy_`, `sell_`, `trade_`, `liquidate_`, `lend_` and `borrow_`.
- A `-` after the verb counts the same as `_` (`place-equity-order`, `transfer-funds`).
- The matcher in [`hooks.json`](hooks.json) routes exactly these names to the guard, and the guard
  reads the tool name from the hook input itself, so it still decides correctly if Codex applies
  the matcher more broadly than expected. If it cannot read exactly one tool name, it blocks. A
  server whose name does not contain "robinhood" (a UUID, say) gets only the `place_`, `exercise_`
  and `replace_` block in Codex.
- It never returns an allow decision. Every other tool gets no opinion, and Codex's normal approval
  rules apply.

## What it does not do

These need Claude Code with the `robinhood-trading` plugin: the permission prompt before cancels
(cancelling a protective stop or OCO raises risk), prompts for unknown Robinhood tools, the session
order-mode line, the local audit log and confirm mode. In Codex the skills still ask before a
cancel, but that is advised only.

## Install

**Installed the Codex plugin?** The guard is already bundled: `.codex-plugin/hooks.json` runs
`sh "${PLUGIN_ROOT}/hooks/guard.sh" money --format codex`. Review and trust it in `/hooks`, skip the
steps below, and run the first-run test. Don't also merge this folder's `hooks.json`: Codex runs
matching hooks from every file, so the guard would run twice.

The manual merge below is only for skills-only installs (`npx skills`, copied folders,
`$skill-installer`):

1. Clone the repository and note the absolute path:
   `git clone https://github.com/yashpatel-py/robinhood-trading-agent-skills`
2. Merge the `PreToolUse` entry from [`hooks.json`](hooks.json) into your Codex hooks file (usually
   `~/.codex/hooks.json`; add to the existing `PreToolUse` array if there is one). Replace `<REPO>`
   with the absolute clone path. If the path contains spaces, quote it:
   `sh "/path with spaces/robinhood-trading-agent-skills/hooks/guard.sh" money --format codex`.
3. Check the hooks documentation for your Codex version (where the hooks file lives, and whether
   hooks must be switched on), then restart Codex.
4. Run the first-run test.

The guard is POSIX `sh` and uses only `cat`, `sed`, `grep`, `tr`, `printf`, `date`, `mkdir` and
`head`. It needs no Python and makes no network calls.

## First-run test (no real orders)

**Step 1: the script.** From a terminal:

```sh
printf '%s' '{"tool_name":"mcp__robinhood__place_equity_order","tool_input":{}}' \
  | sh <REPO>/hooks/guard.sh money --format codex; echo "exit=$?"
```

Expected: the message `robinhood-trading guard: live order placement is disabled (simulate-only).
Nothing was placed. …` and `exit=2`. The same for another money-like verb on a Robinhood server:

```sh
printf '%s' '{"tool_name":"mcp__robinhood__transfer_funds","tool_input":{}}' \
  | sh <REPO>/hooks/guard.sh money --format codex; echo "exit=$?"
```

Expected: the same message and `exit=2`. A read tool gets no opinion:

```sh
printf '%s' '{"tool_name":"mcp__robinhood__get_equity_quotes","tool_input":{}}' \
  | sh <REPO>/hooks/guard.sh money --format codex; echo "exit=$?"
```

Expected: no output and `exit=0`.

**Step 2: inside Codex, against the sandbox, never your real account.** Add the sandbox server
(`sandbox/README.md`; it serves fixture data and no real orders exist) and ask Codex to "call the
sandbox's place_equity_order for 1 share of AAPL".

- **Pass:** Codex reports that a hook blocked the call.
- **Fail:** the sandbox answers `SANDBOX: no order placed`. The hook did not run, so Codex is not
  enforcing it and placement stays advised only.

Please open a `connector-behavior` issue with your Codex version and the result, so the safety
table in the README can move from "Verify" to "Enforced" (or say why not).

## Limits

- **Windows:** the hook needs `sh` (Git for Windows or WSL). Without it the hook cannot start, and
  Codex may run the tool anyway (fail open).
- **Other brokers:** the guard also blocks `place_*`, `exercise_*` and `replace_*` tools on other
  MCP servers. There is no exemption list in Codex; narrow the matcher yourself if you need one.
- **Robinhood's limits still apply on every surface:** the separately funded Agentic account balance
  is your maximum exposure, and you can disconnect the agent in the Robinhood app at any time.

## Remove

For a manual install, delete the entry from your Codex hooks file and restart Codex. The plugin's
bundled hook is removed with the plugin.
