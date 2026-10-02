# Configuration

*Unofficial. Not affiliated with Robinhood Markets, Inc. Not investment or tax advice.*

Preflight works with no configuration at all: a skill asks for the values a run needs and offers to
save only what you stated. Configuration is for people who want the same limits and rules applied
every time, or want a scheduled check to run without asking.

There are two separate places, on purpose:

| What | Where | Who can change it |
|---|---|---|
| Your trading rules and limits: exit rules, screener criteria, policy limits, tax settings | one TOML file (below) | You, or the agent after you state a value and approve the diff |
| How the order guard behaves: order mode, audit log, exempt servers | Claude Code plugin settings (`userConfig`) | You, in the plugin settings. The agent never writes these |

The split exists because the file is something an agent may help you edit, and the order mode is the
one setting an agent must never be able to change for itself.

## The config file

**Where the skills look, first match wins:**

1. a path you name in the conversation
2. `$ROBINHOOD_SKILLS_CONFIG`
3. `./.robinhood/config.toml` in your project directory. The skills pass that directory to
   `kitconfig.py` as its `cwd` input, because scripts may run from somewhere else. If a script runs from
   inside a skill folder without `cwd`, it answers `PROJECT_DIR_UNKNOWN` (or adds a warning when it
   found a lower-priority file) instead of a false "no config found".
4. `${XDG_CONFIG_HOME:-~/.config}/robinhood-skills/config.toml`
5. a block starting with the line `# robinhood-skills:config` pasted into the project instructions or
   knowledge (claude.ai and ChatGPT projects, where there is no file system)
6. none: the skill asks

A `config.json` with the same structure is accepted at each location. Keep the file outside the skill
folders, because installs and updates replace those.

**Grammar.** A small, strict subset of TOML so that every Python version reads it the same way:
`#` comments, `[section]` and `[section.sub]` headers, and `key = "string" | integer | true/false |
["A", "B"] | [["A", "B"], ...]`. Write decimals as strings (`"500.00"`). No inline tables, no
multi-line strings, no dates or floats. The loader (`kitconfig.py`) rejects anything else with the
line number.

**Sections and the skill that reads them:**

| Section | Read by | Template |
|---|---|---|
| `[policy]` | core, exit guardian, options screener | `skills/robinhood-trading/assets/policy.example.toml` |
| `[accounts]` | every skill | same file, and `skills/robinhood-tax-loss-harvesting/assets/tax.example.toml` |
| `[tax]` | tax-loss harvesting | `skills/robinhood-tax-loss-harvesting/assets/tax.example.toml` |
| `[exits.equity]`, `[exits.equity.symbols.<SYM>]`, `[exits.crypto]` | exit guardian | `skills/robinhood-exit-guardian/assets/exits.example.toml` |
| `[options.exits]`, `[options.monitor]` | options monitor (and the screener reads `[options.exits]`) | `skills/robinhood-options-monitor/assets/options-exits.example.toml` |
| `[options.criteria]`, `[options.entry]` | options screener | `skills/robinhood-options-screener/assets/options-screener.example.toml` |
| `[report]` | agent report card | `skills/robinhood-agent-report-card/assets/report.example.toml` |

Each template explains every key in comments. Copy the sections you want into one file; a section may
appear only once per file.

## Every financial value ships as "UNSET"

A stop, a target, a delta band or a position limit is a decision about how much you are willing to
lose, and only you can make it. So the templates ship every financial value as `"UNSET"`, and the
skills treat it in one of two ways:

- **Rules that drive an action** (exit rules, screener criteria): `UNSET` never becomes a number.
  The exit guardian asks you for the level, the options monitor skips that one rule and names it,
  and the options screener stops the whole run and lists the missing keys.
- **Limits that only check** (`[policy]`): `UNSET` means "not configured". The check is skipped and
  listed under "not configured" on the ticket.

Optional screener filters also accept `"OFF"`. Display settings such as `radar_days`,
`lookahead_days` and `window_days` are not financial thresholds, so they ship with values.

## How the agent may edit the file

The agent writes a value only when you state it explicitly in the current conversation, and only after
it shows you the change and you say yes. It never infers a value, fills in a "reasonable default",
copies one parameter into another, or loosens a value to make a run return results. Asking the
screener to "relax whatever you need" gets a question back, not a new number.

In Claude Code with the plugin, edits that touch the config, the state directory or the hook files
also trigger a permission prompt (the `config` hook). That hook is a heuristic, not a boundary.

A minimal file, for example:

```toml
# robinhood-skills:config
[policy]
read_scope = "ask"             # ask | agentic_only | all (see "Which accounts are read" below)
max_order_usd = "500.00"       # soft limit, checked before each simulated order
max_symbol_pct_household = "UNSET"

[options.exits]
profit_target_pct = "50"
stop_loss_pct = "40"
time_stop_dte = "7"
max_hold_days = "UNSET"
exit_price_rule = "bid"        # bid | mid, used only when you ask for a simulated close
```

The numbers above illustrate the format. They are not suggestions. `options.exits.stop_loss_pct` has
no upper bound: a short option can lose more than its premium, so a stop above 100% is meaningful.

### Which accounts are read (`[policy] read_scope`)

The same rule applies in every skill (connector rule R3):

| Value | Interactive | Scheduled run |
|---|---|---|
| no config, or the key removed | A request about "my" holdings, P&L, taxes or a wash check that names no account covers every account; the agent asks only when the request points at one account without naming it | reads the accounts the prompt names, or every account when it names none |
| `all` | reads every account, no question | reads every account |
| `ask` | asks once per session before reading beyond the Agentic account ("Agentic only, or all accounts?") | cannot ask: reads the Agentic account plus accounts the prompt names, and reports the rest as "not read (read_scope = ask)" |
| `agentic_only` | reads the Agentic account, plus an account you name in the request | reads the Agentic account only |

Every answer says which accounts were read. An unread account turns a cross-account result, such as a
wash-sale check, into "in the accounts read", never "clear".

## Plugin settings (Claude Code only)

| Key | Default | What it does |
|---|---|---|
| `order_mode` | `simulate_only` | `simulate_only` blocks every `place_*` call. `confirm` lets the agent call `place_*` only for the exact order you reviewed and approved in chat, with a permission prompt each time. `exercise_option` stays blocked in both modes. See [confirm-mode.md](confirm-mode.md) |
| `max_order_notional_usd` | empty | Required for confirm mode: live orders above this, or whose notional can't be computed, are denied |
| `review_ttl_seconds` | `300` | Confirm mode only: a `place_*` call must match a review from the same session no older than this (60 to 900) |
| `audit_log` | `true` | Appends each Robinhood tool call, masked, to a local hash-chained log. Turning it off also makes confirm mode unavailable |
| `guard_exempt_servers` | empty | MCP servers whose `place_*` tools the guard should ignore, for example another broker's server you manage yourself. A server that has served a known Robinhood tool is never exempt |

Set them when you install (`claude plugin install robinhood-trading@yashpatel-py --config audit_log=true`)
or later from the `/plugin` menu. Claude Code passes them to the hooks as
`CLAUDE_PLUGIN_OPTION_<KEY>`. The first line of every session states the active order mode.

## Local state

The hooks write, and the skills only read, under
`${ROBINHOOD_SKILLS_STATE:-${XDG_STATE_HOME:-~/.local/state}/robinhood-skills}` (directories 0700,
files 0600):

| Path | What |
|---|---|
| `audit/audit-YYYY-MM.jsonl` | the masked, hash-chained audit log (the report card reads it) |
| `audit/blocked.jsonl` | blocked calls, folded into the chain on the next audit run |
| `ledger.jsonl` | fingerprints of successful reviews (slippage in the report card; binding in confirm mode). Each entry carries `server`, the salted 8-hex hash of the MCP server that answered the review: confirm mode binds a place call only to a review from the same server |
| `servers.txt`, `known_accounts.json`, `salt`, `last_tool/` | which MCP servers are Robinhood's, salted hashes of your account numbers for masking, and the last Robinhood tool called |

Nothing is uploaded. Deleting the directory loses nothing that matters to trading: the broker is
always the source of truth, and the skills re-read it before acting. You lose the audit history.

## Environment variables

| Variable | Used for |
|---|---|
| `ROBINHOOD_SKILLS_CONFIG` | an explicit config file path |
| `ROBINHOOD_SKILLS_STATE` | an explicit state directory |
| `XDG_CONFIG_HOME`, `XDG_STATE_HOME` | the default config and state roots |
