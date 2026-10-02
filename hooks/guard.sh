#!/bin/sh
# guard.sh - order guard and hook dispatcher for Preflight for Robinhood Agentic Trading.
# Unofficial; not affiliated with Robinhood Markets, Inc.
#
# Why this file exists: the skills tell the agent never to place, exercise or replace an order,
# but an instruction is advice, and a prompt injection or a confused model can talk its way past
# advice. This script runs outside the model, as a Claude Code hook, so for MCP tool calls made in
# this Claude Code session the block holds whatever the model was told. It does not see what a
# shell command does: a nested `claude` run with hooks off, or a direct HTTPS call with the stored
# OAuth token, is outside it (the config mode below is a pattern tripwire, not a boundary).
# It is POSIX sh and uses only cat, sed, grep, tr, printf, date, mkdir, head
# and command, so it still blocks on a machine with no Python, no jq and an unwritable home.
#
# Modes (hooks/hooks.json wires them; the hook event JSON arrives on stdin):
#   money     PreToolUse: place_/exercise_/replace_ tools on any MCP server, and other money-like
#             verbs on servers whose name contains "robinhood" (a "-" after the verb counts too).
#             Blocks with exit 2. The one way through is confirm mode (off by default): a place_*
#             call that hooks/confirm_gate.py binds to a same-session review is turned into a
#             permission prompt (ask).
#   cancel    PreToolUse: cancel_* and delete_alert. Forces a permission prompt that says which
#             protection the cancel removes (cancelling a stop or OCO raises risk).
#   classify  PreToolUse: every MCP tool. Learns which servers are Robinhood's (a server that
#             serves a known Robinhood tool) and prompts for, or blocks, tools this kit has never
#             seen on them. This covers connectors whose server name is a UUID.
#   config    PreToolUse: Write/Edit/MultiEdit/Bash. Prompts before edits that touch the kit's
#             state, config, plugin settings or hook files, and before Bash commands that name a
#             known route around this hook. A heuristic, not a boundary.
#   audit     PostToolUse: every MCP tool. Hands the event to hooks/audit_log.py (local only).
#   session   SessionStart: tells the agent which order mode is in force.
#   money --format codex|cursor   the same placement block for Codex and Cursor hooks
#             (integrations/*/README.md). Those clients' payloads are unverified, so this path
#             reads the tool name itself and fails closed when it cannot.
#
# Claude Code exit codes: 2 blocks the call and shows stderr to the agent; 0 with an "ask" JSON
# forces a permission prompt; 0 with no output is no opinion. Any other non-zero exit would NOT
# block, so in money mode every path that is not a verified ask or an explicit exemption ends in
# exit 2, including shell errors (the EXIT trap below). No path in this file prints an allow.
#
# A hook that is still running at its timeout is killed, and Claude Code then treats the call as
# if the hook had no opinion (it fails open). So a verdict must never wait on the state directory,
# which an agent can tamper with (a FIFO planted where a file is expected blocks any reader) and
# which can sit on a stalled network home. Every verdict is therefore printed first, and the
# logging and server learning that follow run in a background child whose stdin, stdout and
# stderr are all /dev/null: "( ... ) </dev/null >/dev/null 2>&1 &". Claude Code waits until the
# hook's pipes close, so a child that kept them open would hold the hook until its timeout just the
# same; with all three redirected, whatever the child does, or however long it hangs, cannot change
# or delay the verdict already printed. The only state read left before a verdict is servers.txt, in
# classify mode and for a server the user exempted; both read regular files only. The confirm
# gate bounds its own run with a watchdog (hooks/confirm_gate.py).

set -u
umask 077
LC_ALL=C
export LC_ALL

MODE=${1:-}
FORMAT=claude
case ${2:-} in
  --format) FORMAT=${3:-claude} ;;
  --format=*) FORMAT=${2#--format=} ;;
esac

case $0 in
  */*) SELF_DIR=${0%/*} ;;
  *) SELF_DIR=. ;;
esac
ROOT=${CLAUDE_PLUGIN_ROOT:-$SELF_DIR/..}
STATE=${ROBINHOOD_SKILLS_STATE:-${XDG_STATE_HOME:-${HOME:-}/.local/state}/robinhood-skills}
NL='
'
if [ "${CLAUDE_PLUGIN_OPTION_ORDER_MODE:-}" = confirm ]; then
  ORDER_MODE=confirm
else
  ORDER_MODE=simulate_only
fi

BLOCK_MSG='robinhood-trading guard: live order placement is disabled (simulate-only). Nothing was placed. Show the user the review/preview ticket and the handoff text (connector-rules R21). Do not retry or work around this.'
HANDOFF_TAIL='Nothing was placed. Show the user the review/preview ticket and the handoff text (connector-rules R21). Do not retry or work around this.'
CURSOR_USER_MSG='Blocked by the robinhood-trading order guard: this kit does not place, exercise or replace live orders. Nothing was placed.'

# ---------------------------------------------------------------- fail-closed plumbing (money)

adapter_block() {
  # $1 = message for the agent. Cursor reads a JSON verdict on stdout (both key spellings are
  # printed because the field names are unverified); Codex and Claude Code read exit 2 + stderr.
  trap - EXIT
  if [ "$FORMAT" = cursor ]; then
    printf '{"permission":"deny","userMessage":"%s","agentMessage":"%s","user_message":"%s","agent_message":"%s"}\n' \
      "$CURSOR_USER_MSG" "$1" "$CURSOR_USER_MSG" "$1"
    printf '%s\n' "$1" >&2
    exit 0
  fi
  printf '%s\n' "$1" >&2
  exit 2
}

# In money mode, any exit that did not clear this trap first (an unexpected shell error included)
# blocks, so a bug in this file stops the order instead of letting it through.
if [ "$MODE" = money ]; then
  trap 'adapter_block "$BLOCK_MSG"' EXIT
fi

# ---------------------------------------------------------------- input parsing

INPUT=$(cat 2>/dev/null) || INPUT=
FLAT=$(printf '%s' "$INPUT" | tr -d '\r\n')

# The tool name is read with sed, not a JSON parser (there may be no Python). A tool_input can
# carry its own "tool_name" key, so a name that appears twice is treated as unparsable: in money
# mode that means blocked, never a guess that could match an exempt server.
NAME=
case $FLAT in
  *'"tool_name"'*'"tool_name"'*) ;;
  *'"tool_name"'*)
    NAME=$(printf '%s\n' "$FLAT" | sed -n 's/.*"tool_name"[[:space:]]*:[[:space:]]*"\([A-Za-z0-9_.:-]*\)".*/\1/p' | head -n 1)
    ;;
esac

B=
S=
case $NAME in
  mcp__?*__?*)
    B=${NAME##*__}
    S=${NAME#mcp__}
    S=${S%__"$B"}
    ;;
esac
if [ -z "$B" ] || [ -z "$S" ]; then
  B=
  S=
fi

# ---------------------------------------------------------------- helpers

lower() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

audit_enabled() {
  case $(lower "${CLAUDE_PLUGIN_OPTION_AUDIT_LOG:-true}") in
    false|0|no|off) return 1 ;;
  esac
  return 0
}

appendable() {
  # A state file this script may append to: absent, or a regular file that is not a symlink.
  # Opening a FIFO for writing blocks until someone reads it, and a symlink could point anywhere.
  if [ -L "$1" ]; then
    return 1
  fi
  [ ! -e "$1" ] || [ -f "$1" ]
}

in_servers_file() {
  # [ -f ] is false for a FIFO or a device, so grep never opens one (it would block).
  [ -n "$1" ] && [ -f "$STATE/servers.txt" ] && grep -qxF -- "$1" "$STATE/servers.txt" 2>/dev/null
}

is_rh_server() {
  case $(lower "$1") in
    *robinhood*) return 0 ;;
  esac
  in_servers_file "$1"
}

is_known_tool() {
  [ -n "$1" ] && [ -r "$ROOT/hooks/known-tools.txt" ] && grep -qxF -- "$1" "$ROOT/hooks/known-tools.txt" 2>/dev/null
}

is_generic_name() {
  # Robinhood tool names that unrelated MCP servers also use (a notes app's "search", a monitoring
  # server's "get_alerts", a security scanner's "run_scan"). Seeing one proves nothing about the
  # server, so they never teach servers.txt; hooks/audit_log.py keeps the same list.
  case $1 in
    search|get_accounts|get_financials|get_indexes|get_scans|run_scan|create_scan|preview_scan|\
    get_alerts|get_alert_log|create_alert|update_alert|delete_alert|mark_alerts_read|\
    get_watchlists|get_watchlist_items|create_watchlist|update_watchlist|add_to_watchlist|\
    remove_from_watchlist|follow_watchlist|unfollow_watchlist)
      return 0 ;;
  esac
  return 1
}

learn_server() {
  # Runs in a detached child (see the header), after the verdict.
  [ -n "$1" ] || return 0
  mkdir -p "$STATE" || return 0
  in_servers_file "$1" && return 0
  appendable "$STATE/servers.txt" || return 0
  printf '%s\n' "$1" >>"$STATE/servers.txt"
}

log_event() {
  # $1 = pre_block | pre_ask, $2 = reason code or empty. Runs in a detached child (see the header), after the
  # verdict, so a read-only disk or a tampered state directory never changes or delays a decision.
  # audit_log.py folds these lines into the hash chain (and hashes the session id) on its next run.
  audit_enabled || return 0
  mkdir -p "$STATE/audit" || return 0
  appendable "$STATE/audit/blocked.jsonl" || return 0
  LE_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  LE_SID=
  case $FLAT in
    *'"session_id"'*)
      LE_SID=$(printf '%s\n' "$FLAT" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([A-Za-z0-9_.:-]*\)".*/\1/p' | head -n 1)
      ;;
  esac
  LE_AFTER=
  if [ -f "$STATE/last_tool/latest" ]; then
    LE_AFTER=$(head -n 1 "$STATE/last_tool/latest" | tr -cd 'A-Za-z0-9_')
  fi
  if [ -n "$2" ]; then
    LE_RC="\"$2\""
  else
    LE_RC=null
  fi
  printf '{"v":1,"ts":"%s","event":"%s","tool":"%s","server":"%s","reason_code":%s,"session_id":"%s","after_tool":"%s","mode":"%s"}\n' \
    "$LE_TS" "$1" "$B" "$S" "$LE_RC" "$LE_SID" "$LE_AFTER" "$ORDER_MODE" >>"$STATE/audit/blocked.jsonl"
}

emit_ask() {
  # $1 must not contain double quotes or backslashes (every caller passes fixed text).
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"%s"}}\n' "$1"
}

block() {
  # $1 = reason code, $2 = message the agent sees. Exit 2 is the only exit code that blocks. The
  # message goes out before anything touches the state directory; logging runs in a detached child.
  trap - EXIT
  printf '%s\n' "$2" >&2
  if audit_enabled; then
    ( log_event pre_block "$1" ) </dev/null >/dev/null 2>&1 &
  fi
  exit 2
}

# ---------------------------------------------------------------- money

exempt_server() {
  # A server the user named in guard_exempt_servers (another broker they manage themselves) is
  # left alone, unless anything suggests it is Robinhood's: a name containing "robinhood", or an
  # entry in servers.txt. If servers.txt exists but is not a regular file or cannot be read, there
  # is no exemption. The list is checked first, so the state directory is consulted only for a
  # server the user exempted: a call to any other server never waits on it.
  ES_LIST=${CLAUDE_PLUGIN_OPTION_GUARD_EXEMPT_SERVERS:-}
  [ -n "$ES_LIST" ] || return 1
  [ -n "$NAME" ] && [ -n "$B" ] && [ -n "$S" ] || return 1
  case $(lower "$S") in
    *robinhood*) return 1 ;;
  esac
  ES_HIT=1
  ES_OLDIFS=$IFS
  set -f
  IFS=,
  # shellcheck disable=SC2086  # word-splitting the comma-separated list is the point
  for ES_ENTRY in $ES_LIST; do
    ES_ENTRY=$(printf '%s' "$ES_ENTRY" | tr -d ' \t')
    if [ -n "$ES_ENTRY" ] && [ "$ES_ENTRY" = "$S" ]; then
      ES_HIT=0
    fi
  done
  IFS=$ES_OLDIFS
  set +f
  [ "$ES_HIT" -eq 0 ] || return 1
  if [ -e "$STATE/servers.txt" ] || [ -L "$STATE/servers.txt" ]; then
    [ -f "$STATE/servers.txt" ] || return 1
    grep -qxF -- "$S" "$STATE/servers.txt" 2>/dev/null
    [ "$?" -eq 1 ] || return 1
  elif [ -e "$STATE" ] && { [ ! -d "$STATE" ] || [ ! -x "$STATE" ]; }; then
    return 1
  fi
  return 0
}

valid_ask() {
  # The only output the guard will relay from the confirm gate: exactly one line, no control
  # characters, the exact ask shape, and a reason of 1-600 characters with no quote or backslash.
  VA_OUT=$1
  [ -n "$VA_OUT" ] || return 1
  case $VA_OUT in
    *"$NL"*) return 1 ;;
  esac
  if printf '%s\n' "$VA_OUT" | grep -q '[[:cntrl:]]'; then
    return 1
  fi
  printf '%s\n' "$VA_OUT" | grep -Eq '^\{"hookSpecificOutput":\{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"[^"\\]+"\}\}$' || return 1
  VA_REASON=${VA_OUT#*'"permissionDecisionReason":"'}
  VA_REASON=${VA_REASON%'"}}'}
  [ "${#VA_REASON}" -ge 1 ] && [ "${#VA_REASON}" -le 600 ]
}

confirm_path() {
  # Reached only for a place_* tool with order_mode = confirm. Returns nothing: it either relays
  # a verified ask (exit 0) or blocks (exit 2).
  CP_GATE=$ROOT/hooks/confirm_gate.py
  if [ ! -f "$CP_GATE" ]; then
    block GATE_FAILED "robinhood-trading guard: confirm mode is selected, but hooks/confirm_gate.py is missing, so live orders stay blocked. $HANDOFF_TAIL"
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    block GATE_FAILED "robinhood-trading guard: confirm mode is selected, but python3 was not found, so the confirm gate cannot run and live orders stay blocked. $HANDOFF_TAIL"
  fi
  # The gate bounds its own run (a watchdog well inside this hook's timeout) and exits 4 with
  # GATE_FAILED when it runs out of time, which blocks below.
  CP_OUT=$(printf '%s' "$INPUT" | ROBINHOOD_SKILLS_STATE=$STATE python3 "$CP_GATE" 2>/dev/null)
  CP_RC=$?
  if [ "$CP_RC" -eq 0 ] && valid_ask "$CP_OUT"; then
    trap - EXIT
    if ! printf '%s\n' "$CP_OUT"; then
      block GATE_FAILED "robinhood-trading guard: the permission prompt could not be written, so the call is blocked. $HANDOFF_TAIL"
    fi
    if audit_enabled; then
      ( log_event pre_ask "" ) </dev/null >/dev/null 2>&1 &
    fi
    exit 0
  fi
  CP_CODE=GATE_FAILED
  if [ "$CP_RC" -ne 0 ] && [ -n "$CP_OUT" ]; then
    CP_TEXT=$(printf '%s' "$CP_OUT" | tr '\n\r\t' '   ' | tr -d '"' | sed 's/^\(.\{150\}.\{150\}\).*$/\1/')
    CP_LEAD=$(printf '%s\n' "$CP_TEXT" | sed -n 's/^\([A-Z][A-Z_]\{2,39\}\).*$/\1/p' | head -n 1)
    if [ -n "$CP_LEAD" ]; then
      CP_CODE=$CP_LEAD
    fi
  elif [ "$CP_RC" -ne 0 ]; then
    CP_TEXT="GATE_FAILED (the gate exited $CP_RC without a reason)"
  else
    CP_TEXT="GATE_FAILED (the gate did not return exactly one valid ask line)"
  fi
  block "$CP_CODE" "robinhood-trading guard: confirm gate refused: $CP_TEXT. Nothing was placed. Do not retry or work around this; if the order changed, review it again and get a fresh approval, otherwise show the ticket and the handoff text (connector-rules R21)."
}

adapter_money() {
  # Codex and Cursor: the matcher semantics and payloads are unverified, so decide from the tool
  # name here. Unreadable input blocks; a tool that is not money-moving gets no opinion.
  AM_NAME=
  case $FLAT in
    *'"tool_name"'*'"tool_name"'*) ;;
    *'"tool_name"'*)
      AM_NAME=$(printf '%s\n' "$FLAT" | sed -n 's/.*"tool_name"[[:space:]]*:[[:space:]]*"\([A-Za-z0-9_.:/@-]*\)".*/\1/p' | head -n 1)
      ;;
  esac
  if [ -z "$AM_NAME" ]; then
    adapter_block "robinhood-trading guard: could not read exactly one tool_name from the hook input, so the guard fails closed. Nothing was placed. See integrations/$FORMAT/README.md."
  fi
  AM_B=${AM_NAME##*__}
  AM_B=${AM_B##*.}
  AM_B=${AM_B##*/}
  AM_B=${AM_B##*:}
  case $AM_B in
    place[_-]*|exercise[_-]*|replace[_-]*) adapter_block "$BLOCK_MSG" ;;
  esac
  case $AM_B in
    submit[_-]*|execute[_-]*|transfer[_-]*|withdraw[_-]*|deposit[_-]*|stake[_-]*|unstake[_-]*|convert[_-]*|\
    send[_-]*|buy[_-]*|sell[_-]*|trade[_-]*|liquidate[_-]*|lend[_-]*|borrow[_-]*)
      AM_URL=
      case $FLAT in
        *'"url"'*'"url"'*) ;;
        *'"url"'*)
          AM_URL=$(printf '%s\n' "$FLAT" | sed -n 's/.*"url"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)
          ;;
      esac
      case $(lower "$AM_NAME $AM_URL") in
        *robinhood*) adapter_block "$BLOCK_MSG" ;;
      esac
      ;;
  esac
  trap - EXIT
  exit 0
}

mode_money() {
  case $FORMAT in
    codex|cursor) adapter_money ;;
  esac
  if exempt_server; then
    trap - EXIT
    exit 0
  fi
  if [ "$ORDER_MODE" = confirm ]; then
    case $B in
      place_?*) confirm_path ;;
      '') block GATE_FAILED "robinhood-trading guard: could not read exactly one tool name from the hook input, so the confirm gate was not consulted and the call is blocked. $HANDOFF_TAIL" ;;
    esac
    block NOT_CONFIRMABLE "robinhood-trading guard: $B is blocked in every order mode; confirm mode covers reviewed place_* orders only. $HANDOFF_TAIL"
  fi
  block SIMULATE_ONLY "$BLOCK_MSG"
}

# ---------------------------------------------------------------- cancel

delete_confirmed() {
  # delete_alert without confirm (or with confirm false) only previews; anything else deletes.
  case $FLAT in
    *'"confirm"'*) ;;
    *) return 1 ;;
  esac
  DC_REST=$(printf '%s\n' "$FLAT" | sed \
    -e 's/"confirm"[[:space:]]*:[[:space:]]*false//g' \
    -e 's/"confirm"[[:space:]]*:[[:space:]]*null//g' \
    -e 's/"confirm"[[:space:]]*:[[:space:]]*"false"//g')
  case $DC_REST in
    *'"confirm"'*) return 0 ;;
  esac
  return 1
}

mode_cancel() {
  MC_TEXT='This cancels or deletes something on your Robinhood account. Check what protection it removes before approving.'
  case $B in
    cancel_equity_order)
      MC_TEXT="If this order is a stop or exit for a position, cancelling it removes the position's downside protection (risk goes up). If it is an opening order, exposure goes down." ;;
    cancel_advanced_order)
      MC_TEXT='This cancels BOTH legs of the OCO (take-profit and stop). The position is unprotected until a replacement is placed.' ;;
    cancel_option_order)
      MC_TEXT='If this order closes or protects a position, cancelling it leaves that position open and unprotected.' ;;
    cancel_crypto_order)
      MC_TEXT='If this is a stop order protecting a holding, cancelling it removes that protection.' ;;
    cancel_option_exercise)
      MC_TEXT='This cancels ALL queued exercise requests for this option, not just one. The long option stays open and can still auto-exercise or expire.' ;;
    delete_alert)
      if delete_confirmed; then
        MC_TEXT='Permanently deletes this Robinhood alert (no undo). To pause instead, disable it.'
      else
        exit 0
      fi ;;
  esac
  emit_ask "$MC_TEXT"
  if audit_enabled; then
    ( log_cancel_ask ) </dev/null >/dev/null 2>&1 &
  fi
  exit 0
}

log_cancel_ask() {
  # Detached: only asks on Robinhood's tools or servers go in the audit log.
  if is_known_tool "$B" || is_rh_server "$S"; then
    log_event pre_ask ""
  fi
}

# ---------------------------------------------------------------- classify

mode_classify() {
  [ -n "$B" ] || exit 0
  [ -r "$ROOT/hooks/known-tools.txt" ] || exit 0
  if is_known_tool "$B"; then
    if ! is_generic_name "$B"; then
      ( learn_server "$S" ) </dev/null >/dev/null 2>&1 &
    fi
    exit 0
  fi
  # The one state read before a verdict: is this server Robinhood's? servers.txt is read only
  # when it is a regular file (in_servers_file).
  is_rh_server "$S" || exit 0
  case $B in
    submit[_-]*|execute[_-]*|transfer[_-]*|withdraw[_-]*|deposit[_-]*|stake[_-]*|unstake[_-]*|convert[_-]*|\
    send[_-]*|buy[_-]*|sell[_-]*|trade[_-]*|liquidate[_-]*|lend[_-]*|borrow[_-]*|\
    place[_-]*|exercise[_-]*|replace[_-]*)
      block UNKNOWN_MONEY_TOOL "robinhood-trading guard: '$B' is not a Robinhood tool this kit knows (tools verified 2026-09-22), and its name says it may move money or place orders, so it is blocked. Nothing was placed. Tell the user the kit may be out of date (connector-rules R1), and do not look for another tool that does the same thing." ;;
  esac
  emit_ask "Unknown Robinhood tool '$B': this kit (tools verified 2026-09-22) cannot tell whether it moves money. Approve only if you know what it does."
  if audit_enabled; then
    ( log_event pre_ask UNKNOWN_TOOL ) </dev/null >/dev/null 2>&1 &
  fi
  exit 0
}

# ---------------------------------------------------------------- config

mode_config() {
  # The kit's state and config live in folders named exactly robinhood-skills
  # (~/.local/state/robinhood-skills, ~/.config/robinhood-skills). The session's own paths (cwd,
  # transcript) are removed first, and a folder such as robinhood-skills-fork does not match, so
  # a project that merely has a similar name does not prompt on every edit.
  #
  # An installed plugin lives under ~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/, and
  # every skill runs its scripts from there, so the cache as a whole must not prompt. Only the
  # files the hooks run or read do: hooks/ (guard.sh, hooks.json, confirm_gate.py, audit_log.py,
  # hooks/lib, known-tools.txt, tool-classes.json), .claude-plugin/, shared/scripts/ (kitconfig.py,
  # which the gate loads) and connector/tool-classes.json. A glob aimed at the state folder
  # (~/.local/state/robinhood-skill*) or $XDG_STATE_HOME prompts too. This is a tripwire for
  # honest mistakes, not a boundary: an agent with an unprompted shell can always find a spelling
  # it does not catch.
  CF_TEXT=$(printf '%s\n' "$FLAT" | sed \
    -e 's/"cwd"[[:space:]]*:[[:space:]]*"[^"]*"//g' \
    -e 's/"transcript_path"[[:space:]]*:[[:space:]]*"[^"]*"//g')
  case $CF_TEXT in
    */robinhood-skills[!A-Za-z0-9_.-]*|*.claude/settings*|*/hooks/guard.sh*|*confirm_gate*|\
    *plugins/cache/*/hooks/*|*plugins/cache/*/.claude-plugin/*|*plugins/cache/*/shared/scripts/*|\
    *plugins/cache/*/connector/tool-classes*|*.local/state/*[*?[]*|*XDG_STATE_HOME*|\
    *ledger.jsonl*|*refids.json*|*ROBINHOOD_SKILLS_*|*CLAUDE_PLUGIN_OPTION_*|*.claude.json*)
      emit_ask 'Editing Robinhood kit config, plugin settings or hook files (order mode, caps, guard).'
      exit 0
      ;;
  esac
  # Shell commands that could route around this hook: a nested claude run with hooks or permissions
  # off, another config dir, the stored MCP sign-in token, or a direct call to Robinhood's endpoint.
  # Still a text-pattern tripwire (docs/safety-model.md, "Gaps we know about"), not a boundary.
  if [ "$NAME" = Bash ]; then
    case $CF_TEXT in
      *agent.robinhood.com*|*disableAllHooks*|*--setting-sources*|*CLAUDE_CONFIG_DIR*|*--bare*|\
      *--dangerously-skip-permissions*|*'Claude Code-credentials'*|*.credentials.json*|*mcpOAuth*)
        emit_ask 'This shell command could reach Robinhood or the Claude Code sign-in token outside the order guard, which sees MCP tool calls only. Approve only if you started it.'
        ;;
    esac
  fi
  exit 0
}

# ---------------------------------------------------------------- audit

mode_audit() {
  audit_enabled || exit 0
  command -v python3 >/dev/null 2>&1 || exit 0
  [ -f "$ROOT/hooks/audit_log.py" ] || exit 0
  printf '%s' "$INPUT" | ROBINHOOD_SKILLS_STATE=$STATE python3 "$ROOT/hooks/audit_log.py" >/dev/null 2>&1 || :
  exit 0
}

# ---------------------------------------------------------------- session

cap_is_set() {
  CS_CAP=${CLAUDE_PLUGIN_OPTION_MAX_ORDER_NOTIONAL_USD:-}
  printf '%s\n' "$CS_CAP" | grep -Eq '^[0-9]+(\.[0-9]+)?$' || return 1
  printf '%s\n' "$CS_CAP" | grep -q '[1-9]'
}

mode_session() {
  SM_BASE='Robinhood order mode: SIMULATE-ONLY (enforced by the robinhood-trading plugin hook in this Claude Code session). place_*/exercise_*/replace_* calls will be blocked; prepare a ticket and use the handoff text.'
  if [ ! -f "$ROOT/hooks/confirm_gate.py" ]; then
    SM_LINE="$SM_BASE CONFIRM MODE: NOT INSTALLED."
  elif [ "$ORDER_MODE" != confirm ]; then
    SM_LINE="$SM_BASE CONFIRM MODE: INSTALLED, OFF."
  else
    SM_WHY=
    if ! command -v python3 >/dev/null 2>&1; then
      SM_WHY='python3 was not found'
    elif ! audit_enabled; then
      SM_WHY='the local audit log is off, and the gate needs its review ledger'
    elif ! cap_is_set; then
      SM_WHY='max_order_notional_usd is not set to an amount above zero'
    fi
    if [ -n "$SM_WHY" ]; then
      SM_LINE="$SM_BASE CONFIRM MODE: SELECTED BUT UNAVAILABLE ($SM_WHY), so every place_* call will be blocked; say so if the user asks to place an order."
    else
      SM_LINE='Robinhood order mode: CONFIRM MODE: ON. You may call a place_* tool only for the exact ticket the user explicitly approved in their latest message, with a fresh ref_id; a permission prompt will appear; never in scheduled or unattended runs; exercise_option stays blocked. Follow references/confirm-mode.md in the robinhood-trading skill.'
    fi
  fi
  printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"%s"}}\n' "$SM_LINE"
  if audit_enabled && [ -f "$ROOT/hooks/audit_log.py" ] && command -v python3 >/dev/null 2>&1; then
    printf '%s' "$INPUT" | ROBINHOOD_SKILLS_STATE=$STATE python3 "$ROOT/hooks/audit_log.py" --session-start >/dev/null 2>&1 || :
  fi
  exit 0
}

# ---------------------------------------------------------------- dispatch

case $MODE in
  money) mode_money ;;
  cancel) mode_cancel ;;
  classify) mode_classify ;;
  config) mode_config ;;
  audit) mode_audit ;;
  session) mode_session ;;
  *)
    printf "robinhood-trading guard: unknown mode '%s' (expected money, cancel, classify, config, audit or session).\n" "$MODE" >&2
    exit 2
    ;;
esac
