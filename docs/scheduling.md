# Scheduling checks

*Unofficial. Not affiliated with Robinhood Markets, Inc. Not investment or tax advice.*

Four of the six skills are built to run on a schedule. Each run is **one stateless pass**: it reads
your accounts fresh, computes, and reports. It keeps no memory between runs, because the broker is
the source of truth and a cached position is how a check goes stale.

## What a scheduled pass does and never does

- **Line 1 is the status and the dollars at stake**, so the notification alone tells you whether to
  open it: `ACTION NEEDED: …`, `EXIT SIGNAL: …`, `CONFLICT: …`, `NO ACTION: …`.
- **If no Robinhood tool is visible**, line 1 is `CONNECTOR UNAVAILABLE: no Robinhood tools in this
  session` and the run stops. A silent empty report would read as "all clear".
- **It never places an order, in any mode**, including confirm mode. It also never cancels, never
  creates alerts or scans, never writes config, and (except where noted) never simulates tickets.
  Anything that needs a decision waits for you to ask interactively.
- **It states its call count first** when a sweep needs more than 40 calls. An operator measured
  about 4 calls a second sustained, with the first rate limit at 8 a second (not published by
  Robinhood). A rate-limited sweep returns data that looks complete, so the skills report `UNKNOWN`
  rather than a clean result when a call fails.

## Which skills, how often

| Skill | Suggested cadence | A scheduled pass reports |
|---|---|---|
| `robinhood-exit-guardian` | every 30 min in market hours, or once a day | positions with no exit or alert, exits that expire today, alerts that fired since the last read (it marks as read only the events it relayed) |
| `robinhood-options-monitor` | every 30 min to daily, plus 15:00 ET on expiration days | your exit rules that fired, expiration, assignment and auto-exercise cash needs in the radar window |
| `robinhood-agent-report-card` | weekly, for example Friday 16:30 ET | what the agent did, orders from another agent, app or machine, guard blocks and possible injections |
| `robinhood-tax-loss-harvesting` (harvest scan) | weekly, Nov 2 to Dec 31 | harvest candidates with wash-sale status and clean-sale dates across the accounts you allow |
| `robinhood-options-screener` | your choice; tickets need the regular session | candidates against your saved criteria; outside the regular session it reports only |

The core skill answers questions and prepares tickets; it has nothing to schedule.

Read access: a scheduled run has no one to ask, so `[policy] read_scope` decides (connector rule R3;
[config.md](config.md#which-accounts-are-read-policy-read_scope)). With no config, the scheduled prompt
is the request: it reads the accounts the prompt names, or every account when it names none. `all`
reads every account; `agentic_only` reads the Agentic account only; `ask` reads the Agentic account
plus the accounts the prompt names and reports the rest as "not read (read_scope = ask)". For the
agent report card, a scheduled `ask` run therefore reports the read-only accounts as "not verified"
and the status as UNKNOWN (or ACTION NEEDED if something else needs you): set `read_scope = "all"`, or
name the accounts in the prompt, to verify them on a schedule. Accounts a run could not confirm are
reported as "not read" or "not in scope", never as clear.

## Claude Code

`/loop` repeats a prompt or a slash command at an interval while the session stays open. Plugin skills
are invoked as `/robinhood-trading:<skill>`:

```text
/loop 30m /robinhood-trading:robinhood-exit-guardian
/loop 1h /robinhood-trading:robinhood-options-monitor
```

Check `/help` in your Claude Code version for the exact `/loop` syntax. A loop runs only while Claude
Code runs: close the laptop and the checks stop. For protection that survives a closed laptop, set
native Robinhood price alerts (the exit guardian offers them). Alerts notify your phone; they do not
sell.

## Claude Desktop and other desktop schedulers

Desktop scheduled tasks run a saved prompt at a time you choose, for example "Run my options check"
at 15:00 ET on expiration days, or "What did my agent do this week?" on Fridays. The machine and the
app have to be running, and the Robinhood connector has to be connected in that app.

## Cloud routines: check the connector first

Cloud-scheduled agents (for example Claude Code routines) run somewhere other than your machine and
may not see a custom connector such as Robinhood's. Users reported routines where the Robinhood
connector did not appear in the routine's connector list, and runs that hung at start
([anthropics/claude-code#89411](https://github.com/anthropics/claude-code/issues/89411),
[#84585](https://github.com/anthropics/claude-code/issues/84585),
[#72864](https://github.com/anthropics/claude-code/issues/72864)). Run the routine once by hand
before you rely on it. If it prints `CONNECTOR UNAVAILABLE`, the connector is not reaching the cloud
environment.

## claude.ai and ChatGPT

Scheduled tasks there run the saved prompt in a project. Paste your config into the project
instructions under a `# robinhood-skills:config` line so the run has your rules. The rules on these
surfaces are advised only (see [safety-model.md](safety-model.md)), which is one more reason a
scheduled pass never places, cancels or writes anything.
