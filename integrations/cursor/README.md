# Order guard for Cursor

*Unofficial — not affiliated with Robinhood Markets, Inc.*

**Status as of 2026-09-22: Verify.** This hook uses Cursor's `beforeMCPExecution` hook, whose input
and output fields this project has not yet confirmed on a real Cursor build. Until you run the
first-run test below and it passes, treat order blocking in Cursor as **advised**: the skills never
call an order-placing tool, but nothing outside the model enforces that.

## Why a hook at all

The skills already say "prepare the order; the user places it." That is an instruction, and an
instruction can be talked around: a prompt injection hidden in a news item or a filing, or a model
that decides the user "clearly meant it". Robinhood itself warns that an agent told to act without
approval can place trades without your confirmation. A hook runs outside the model, so no argument
reaches it. It is the difference between *advised* and *enforced*.

## What it does

`beforeMCPExecution` fires before **every** MCP call, so the guard decides from the tool name:

- A tool whose name starts with `place_`, `exercise_` or `replace_`, on any server, is denied. Today
  that covers `place_equity_order`, `place_option_order`, `place_crypto_order`,
  `place_advanced_order` and `exercise_option` (no `replace_*` tool is exposed today; the prefix is
  covered for when one appears).
- On a Robinhood server (the tool name or the server `url` contains "robinhood"), other money-like
  verbs (`submit_`, `execute_`, `transfer_`, `withdraw_`, `convert_`, `send_`, `buy_`, `sell_`,
  `trade_` and a few more) are denied too.
- A `-` after the verb counts the same as `_` (`place-equity-order`, `transfer-funds`).
- A denial is one JSON line on stdout with `"permission":"deny"` and a message for you and one for
  the agent. The message is printed under both `userMessage`/`agentMessage` and
  `user_message`/`agent_message`, because the field spelling is unverified. The guard exits 0,
  because Cursor reads the JSON verdict from a hook that succeeded; how Cursor treats a hook that
  exits non-zero is unverified.
- Every other tool gets no output and exit 0, so Cursor's normal approval rules apply. The guard
  never returns an allow verdict. If your Cursor version turns out to require a verdict for every
  call, this integration cannot be used as is, and placement stays advised.
- If the guard cannot read exactly one `tool_name` from the hook input, it denies. When Cursor
  changes its payload, you find out at the first MCP call instead of at the first live order.

## What it does not do

These need Claude Code with the `robinhood-trading` plugin: the permission prompt before cancels
(cancelling a protective stop or OCO raises risk), prompts for unknown Robinhood tools, the session
order-mode line, the local audit log and confirm mode. In Cursor the skills still ask before a
cancel, but that is advised only.

## Install

1. Clone the repository and note the absolute path:
   `git clone https://github.com/yashpatel-py/robinhood-trading-agent-skills`
2. Merge the `beforeMCPExecution` entry from [`hooks.json`](hooks.json) into `~/.cursor/hooks.json`
   (every project) or `<project>/.cursor/hooks.json` (one project). Keep `"version": 1`. Replace
   `<REPO>` with the absolute clone path. If the path contains spaces, quote it:
   `sh \"/path with spaces/robinhood-trading-agent-skills/hooks/guard.sh\" money --format cursor`
   (the inner quotes are escaped because the command sits inside a JSON string).
3. Restart Cursor, then check its hooks output panel for errors.
4. Run the first-run test.

The guard is POSIX `sh` and uses only `cat`, `sed`, `grep`, `tr`, `printf`, `date`, `mkdir` and
`head`. It needs no Python and makes no network calls.

## First-run test (no real orders)

**Step 1: the script.** From a terminal:

```sh
printf '%s' '{"hook_event_name":"beforeMCPExecution","tool_name":"place_equity_order","tool_input":"{}","url":"https://agent.robinhood.com/mcp/trading"}' \
  | sh <REPO>/hooks/guard.sh money --format cursor; echo "exit=$?"
```

Expected: one JSON line containing `"permission":"deny"`, then `exit=0`. A read tool gets no
opinion:

```sh
printf '%s' '{"hook_event_name":"beforeMCPExecution","tool_name":"get_equity_quotes","tool_input":"{}","url":"https://agent.robinhood.com/mcp/trading"}' \
  | sh <REPO>/hooks/guard.sh money --format cursor; echo "exit=$?"
```

Expected: no output and `exit=0`.

**Step 2: inside Cursor, against the sandbox, never your real account.** Add the sandbox server
(`sandbox/README.md`; it serves fixture data and no real orders exist) and ask the agent to "call
the sandbox's place_equity_order for 1 share of AAPL".

- **Pass:** Cursor reports that a hook denied the call.
- **Fail:** the sandbox answers `SANDBOX: no order placed`. The verdict was not applied, so Cursor
  is not enforcing it and placement stays advised only.
- **Every MCP call is denied** with "could not read exactly one tool_name": your Cursor build sends
  a different payload. Remove the hook and report it.

Please open a `connector-behavior` issue with your Cursor version and the result, so the safety
table in the README can move from "Verify" to "Enforced" (or say why not).

## Limits

- **Windows:** the hook needs `sh` (Git for Windows or WSL). Without it the hook cannot start, and
  what Cursor then does is unverified.
- **Other brokers:** the guard also denies `place_*`, `exercise_*` and `replace_*` tools on other MCP
  servers. There is no exemption list in Cursor; remove the hook if that gets in your way.
- **Robinhood's limits still apply on every surface:** the separately funded Agentic account balance
  is your maximum exposure, and you can disconnect the agent in the Robinhood app at any time.

## Remove

Delete the entry from your Cursor hooks file and restart Cursor.
