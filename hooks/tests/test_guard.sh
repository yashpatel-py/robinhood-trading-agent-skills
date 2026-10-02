#!/bin/sh
# test_guard.sh - tests for hooks/guard.sh: spec E.5 H1-H9, H11 (through the real pipeline), H13,
# H14, H15 (a tampered state dir never delays a verdict), the guard side of confirm mode (the gate
# is stubbed; the real gate has its own tests), the "confirm mode off means every place_* is
# denied" check, and the Codex/Cursor adapters.
# Unofficial; not affiliated with Robinhood Markets, Inc.
#
# Usage:
#   sh hooks/tests/test_guard.sh [--no-python] [-v]
#     --no-python  run every guard invocation with PATH reduced to a directory that holds only
#                  sh, cat, sed, grep, tr, printf, date, mkdir and head (no python3, no jq). The
#                  guard must still block, and the confirm path must fail closed.
#     -v           print passing checks too.
#   GUARD_SH=/bin/dash sh hooks/tests/test_guard.sh    run the guard under another shell.
# The harness itself may use jq or python3, when present, to check that outputs are valid JSON.
# Exit status: 0 when every check passed, 1 otherwise.

set -u

NO_PY=0
VERBOSE=0
for arg in "$@"; do
  case $arg in
    --no-python) NO_PY=1 ;;
    -v) VERBOSE=1 ;;
    *) printf 'usage: sh test_guard.sh [--no-python] [-v]\n' >&2; exit 2 ;;
  esac
done

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
GUARD=$REPO/hooks/guard.sh
EVENTS=$HERE/events
GUARD_SH=${GUARD_SH:-/bin/sh}
TMP=$(mktemp -d "${TMPDIR:-/tmp}/rh-guard-test.XXXXXX") || exit 1
trap 'chmod -R u+rwx "$TMP" 2>/dev/null; rm -rf "$TMP"' EXIT
trap 'exit 1' INT TERM

IS_ROOT=0
[ "$(id -u)" = 0 ] && IS_ROOT=1
HAVE_JQ=0
command -v jq >/dev/null 2>&1 && HAVE_JQ=1
HAVE_PY=0
command -v python3 >/dev/null 2>&1 && HAVE_PY=1

BLOCK_MSG='robinhood-trading guard: live order placement is disabled (simulate-only). Nothing was placed. Show the user the review/preview ticket and the handoff text (connector-rules R21). Do not retry or work around this.'
NL='
'
# Built from pieces so this file never contains the literal allow-decision text that CI greps for.
ALLOW_QUOTED=$(printf '"%s%s"' al low)
ALLOW_DECISION=$(printf '"permissionDecision":"%s%s"' al low)

# ---------------------------------------------------------------- reporting

PASS=0
FAIL=0
SKIP=0
RC=
OUT=
ERR=

pass() {
  PASS=$((PASS + 1))
  if [ "$VERBOSE" = 1 ]; then printf 'ok    %s\n' "$1"; fi
}

fail() {
  FAIL=$((FAIL + 1))
  printf 'FAIL  %s: %s\n      exit=%s\n      stdout=[%s]\n      stderr=[%s]\n' "$1" "$2" "$RC" "$OUT" "$ERR"
}

skip() {
  SKIP=$((SKIP + 1))
  printf 'skip  %s: %s\n' "$1" "$2"
}

contains() {
  case $1 in
    *"$2"*) return 0 ;;
  esac
  return 1
}

json_valid() {
  if [ "$HAVE_JQ" = 1 ]; then
    jq -e . "$1" >/dev/null 2>&1
    return
  fi
  if [ "$HAVE_PY" = 1 ]; then
    python3 -c 'import json, sys; json.load(open(sys.argv[1]))' "$1" >/dev/null 2>&1
    return
  fi
  return 0
}

# ---------------------------------------------------------------- fixtures

KNOWN=$TMP/known-tools.txt
if [ -f "$REPO/hooks/known-tools.txt" ]; then
  cp "$REPO/hooks/known-tools.txt" "$KNOWN"
elif [ "$HAVE_JQ" = 1 ]; then
  jq -r '.tools[].name' "$REPO/connector/tool-classes.json" | sort >"$KNOWN"
elif [ "$HAVE_PY" = 1 ]; then
  python3 -c 'import json, sys; print("\n".join(sorted(t["name"] for t in json.load(open(sys.argv[1]))["tools"])))' \
    "$REPO/connector/tool-classes.json" >"$KNOWN"
else
  printf 'cannot build known-tools.txt: need hooks/known-tools.txt, jq or python3\n' >&2
  exit 1
fi
if [ "$(grep -c . "$KNOWN")" != 81 ]; then
  printf 'known-tools.txt should list 81 tools\n' >&2
  exit 1
fi

# R_BASE: known tools and audit_log.py, no confirm gate (a build without confirm mode).
R_BASE=$TMP/root-base
mkdir -p "$R_BASE/hooks"
cp "$KNOWN" "$R_BASE/hooks/known-tools.txt"
cp "$REPO/hooks/audit_log.py" "$R_BASE/hooks/audit_log.py"

# R_GATE: the same plus a stub confirm gate. It records each call in $GATE_MARKER, prints
# $STUB_OUT, optionally crashes, and exits with $STUB_RC, so one stub covers every gate outcome.
R_GATE=$TMP/root-gate
mkdir -p "$R_GATE/hooks"
cp "$KNOWN" "$R_GATE/hooks/known-tools.txt"
cp "$REPO/hooks/audit_log.py" "$R_GATE/hooks/audit_log.py"
cat >"$R_GATE/hooks/confirm_gate.py" <<'PYEOF'
import os
import sys

marker = os.environ.get("GATE_MARKER")
if marker:
    with open(marker, "a") as handle:
        handle.write("called\n")
sys.stdin.read()
out = os.environ.get("STUB_OUT", "")
if out:
    sys.stdout.write(out + "\n")
    sys.stdout.flush()
if os.environ.get("STUB_CRASH"):
    raise RuntimeError("stub gate crashed")
sys.exit(int(os.environ.get("STUB_RC", "0") or "0"))
PYEOF

# R_NOKNOWN: no known-tools.txt (a broken install).
R_NOKNOWN=$TMP/root-noknown
mkdir -p "$R_NOKNOWN/hooks"

# NOPY_BIN: only the commands guard.sh may use.
NOPY_BIN=$TMP/nopy-bin
mkdir -p "$NOPY_BIN"
find_exe() {
  FE_OLDIFS=$IFS
  IFS=:
  for FE_DIR in $PATH; do
    if [ -n "$FE_DIR" ] && [ -f "$FE_DIR/$1" ] && [ -x "$FE_DIR/$1" ]; then
      IFS=$FE_OLDIFS
      printf '%s\n' "$FE_DIR/$1"
      return 0
    fi
  done
  IFS=$FE_OLDIFS
  return 1
}
for tool in cat sed grep tr printf date mkdir head; do
  exe=$(find_exe "$tool") || { printf 'cannot find %s on PATH\n' "$tool" >&2; exit 1; }
  ln -s "$exe" "$NOPY_BIN/$tool"
done
ln -s "$GUARD_SH" "$NOPY_BIN/sh"
if [ -e "$NOPY_BIN/python3" ] || [ -e "$NOPY_BIN/jq" ]; then
  printf 'the reduced PATH must not contain python3 or jq\n' >&2
  exit 1
fi

# The claude.ai connector's server id, assembled from pieces so no 8+ character run trips the leak
# scanner (it is a public client id, not a secret).
UUID_SERVER=$(printf '%s%s-%s-%s-%s-%s%s' 25bad 45e 063f 4df8 9a40 a86bf5 ba4f58)
PREFIXES="mcp__robinhood-trading__ mcp__plugin_unofficial-rh-connector_robinhood__ mcp__${UUID_SERVER}__ mcp__rh-sandbox__"
MONEY_TOOLS="place_equity_order place_option_order place_crypto_order place_advanced_order exercise_option replace_option_order"
PLACE_TOOLS="place_equity_order place_option_order place_crypto_order place_advanced_order"

VALID_ASK='{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"LIVE ORDER - place_equity_order: BUY 1 AAPL - LIMIT 1.00 - gfd - regular_hours - est 1.00 USD (cap 500.00). Matches your review (ticket 7f3a1c). Approve only if this is exactly what you intend."}}'

# ---------------------------------------------------------------- running the guard

STATE_N=0
new_state() {
  STATE_N=$((STATE_N + 1))
  T_STATE=$TMP/state-$STATE_N
}

reset() {
  T_ROOT=$R_BASE
  new_state
  T_MODE=
  T_EXEMPT=
  T_AUDIT=
  T_CAP=
  T_STUB_OUT=
  T_STUB_RC=0
  T_STUB_CRASH=
  rm -f "$TMP/gate-called"
}

# g EVENT_FILE ARGS...: run the guard with the T_* settings; sets RC, OUT and ERR.
g() {
  G_EVENT=$1
  shift
  if [ "$NO_PY" = 1 ]; then G_PATH=$NOPY_BIN; else G_PATH=$PATH; fi
  env PATH="$G_PATH" HOME="$TMP/home" \
    CLAUDE_PLUGIN_ROOT="$T_ROOT" ROBINHOOD_SKILLS_STATE="$T_STATE" \
    CLAUDE_PLUGIN_OPTION_ORDER_MODE="$T_MODE" CLAUDE_PLUGIN_OPTION_GUARD_EXEMPT_SERVERS="$T_EXEMPT" \
    CLAUDE_PLUGIN_OPTION_AUDIT_LOG="$T_AUDIT" CLAUDE_PLUGIN_OPTION_MAX_ORDER_NOTIONAL_USD="$T_CAP" \
    GATE_MARKER="$TMP/gate-called" STUB_OUT="$T_STUB_OUT" STUB_RC="$T_STUB_RC" STUB_CRASH="$T_STUB_CRASH" \
    "$GUARD_SH" "$GUARD" "$@" <"$G_EVENT" >"$TMP/out" 2>"$TMP/err"
  RC=$?
  OUT=$(cat "$TMP/out")
  ERR=$(cat "$TMP/err")
  cat "$TMP/out" "$TMP/err" >>"$TMP/all-output"
}

# Same, with PATH reduced even in the default run (spec H3).
g_nopy() {
  G_SAVED=$NO_PY
  NO_PY=1
  g "$@"
  NO_PY=$G_SAVED
}

# g_bounded LIMIT EVENT ARGS...: g, but the guard's stdout and stderr are pipes (as in Claude
# Code, which waits until they close) and the run is bounded. RC=124 when the pipes were still
# open after LIMIT seconds: the guard, or a child it left holding them, was stuck, and Claude Code
# would have killed the hook and treated the call as if the hook had no opinion. Needs python3 in
# the harness (never on the guard's PATH); callers skip without it.
if [ "$HAVE_PY" = 1 ]; then
  cat >"$TMP/bounded.py" <<'PYEOF'
import os
import signal
import subprocess
import sys

limit, event, out_path, err_path = float(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
with open(event, "rb") as handle:
    proc = subprocess.Popen(sys.argv[5:], stdin=handle, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
try:
    out, err = proc.communicate(timeout=limit)
    rc = proc.returncode
except subprocess.TimeoutExpired:
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # the guard and anything it left behind
    except OSError:
        pass
    try:
        out, err = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        out, err = b"", b""
    rc = 124
with open(out_path, "wb") as handle:
    handle.write(out)
with open(err_path, "wb") as handle:
    handle.write(err)
sys.exit(rc)
PYEOF
  # waiters.py FIFO...: prints each FIFO that some process (a detached child the guard left
  # behind) is blocked on, and releases it by opening the other end.
  cat >"$TMP/waiters.py" <<'PYEOF'
import os
import sys
import time

time.sleep(0.5)
for path in sys.argv[1:]:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)  # succeeds only when a reader is waiting
    except OSError:
        fd = None
    if fd is not None:
        os.close(fd)
        print(path + " (a reader)")
        continue
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        continue
    time.sleep(0.2)
    try:
        data = os.read(fd, 4096)  # EOF (b"") when no writer is waiting
    except BlockingIOError:
        data = b"?"
    os.close(fd)
    if data:
        print(path + " (a writer)")
PYEOF
fi

g_bounded() {
  GB_LIMIT=$1
  G_EVENT=$2
  shift 2
  if [ "$NO_PY" = 1 ]; then G_PATH=$NOPY_BIN; else G_PATH=$PATH; fi
  python3 "$TMP/bounded.py" "$GB_LIMIT" "$G_EVENT" "$TMP/out" "$TMP/err" \
    "$(command -v env)" PATH="$G_PATH" HOME="$TMP/home" \
    CLAUDE_PLUGIN_ROOT="$T_ROOT" ROBINHOOD_SKILLS_STATE="$T_STATE" \
    CLAUDE_PLUGIN_OPTION_ORDER_MODE="$T_MODE" CLAUDE_PLUGIN_OPTION_GUARD_EXEMPT_SERVERS="$T_EXEMPT" \
    CLAUDE_PLUGIN_OPTION_AUDIT_LOG="$T_AUDIT" CLAUDE_PLUGIN_OPTION_MAX_ORDER_NOTIONAL_USD="$T_CAP" \
    GATE_MARKER="$TMP/gate-called" STUB_OUT="$T_STUB_OUT" STUB_RC="$T_STUB_RC" STUB_CRASH="$T_STUB_CRASH" \
    "$GUARD_SH" "$GUARD" "$@"
  RC=$?
  OUT=$(cat "$TMP/out")
  ERR=$(cat "$TMP/err")
  cat "$TMP/out" "$TMP/err" >>"$TMP/all-output"
}

event_for() {
  sed "s/__TOOL_NAME__/$1/" "$EVENTS/pre_tool_template.json" >"$TMP/event.json"
  printf '%s\n' "$TMP/event.json"
}

# ---------------------------------------------------------------- expectations

expect_block() {
  if [ "$RC" != 2 ]; then fail "$1" "expected exit 2 (blocked)"; return; fi
  if [ -n "$OUT" ]; then fail "$1" "expected no stdout on a block"; return; fi
  if ! contains "$ERR" "Nothing was placed"; then fail "$1" "stderr lacks 'Nothing was placed'"; return; fi
  if [ -n "${2:-}" ] && ! contains "$ERR" "$2"; then fail "$1" "stderr lacks '$2'"; return; fi
  pass "$1"
}

expect_ask() {
  if [ "$RC" != 0 ]; then fail "$1" "expected exit 0 with an ask"; return; fi
  case $OUT in
    '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"'*'"}}') ;;
    *) fail "$1" "stdout is not a single PreToolUse ask"; return ;;
  esac
  if contains "$OUT" "$NL"; then fail "$1" "ask spans more than one line"; return; fi
  if [ -n "${2:-}" ] && ! contains "$OUT" "$2"; then fail "$1" "ask lacks '$2'"; return; fi
  if ! json_valid "$TMP/out"; then fail "$1" "stdout is not valid JSON"; return; fi
  if [ -n "$ERR" ]; then fail "$1" "unexpected stderr"; return; fi
  pass "$1"
}

expect_silent() {
  if [ "$RC" != 0 ]; then fail "$1" "expected exit 0"; return; fi
  if [ -n "$OUT" ] || [ -n "$ERR" ]; then fail "$1" "expected no output"; return; fi
  pass "$1"
}

expect_gate_not_called() {
  if [ -e "$TMP/gate-called" ]; then fail "$1" "the confirm gate was consulted"; return; fi
  pass "$1"
}

expect_gate_called() {
  if [ ! -e "$TMP/gate-called" ]; then fail "$1" "the confirm gate was not consulted"; return; fi
  pass "$1"
}

wait_for_text() {
  # wait_for_text FILE TEXT: the guard prints its verdict first and then writes blocked.jsonl and
  # servers.txt from a detached child, so a check on those files waits up to 5 s for the child.
  WF_N=0
  while [ "$WF_N" -lt 50 ]; do
    if [ -f "$1" ] && grep -qF -- "$2" "$1"; then return 0; fi
    sleep 0.1
    WF_N=$((WF_N + 1))
  done
  return 1
}

expect_file_has() {
  # expect_file_has ID FILE TEXT
  if wait_for_text "$2" "$3"; then pass "$1"; else fail "$1" "$2 lacks '$3'"; fi
}

expect_no_file() {
  if [ -e "$2" ]; then fail "$1" "$2 should not exist"; else pass "$1"; fi
}

# ================================================================ H1: every money tool, every prefix

reset
for tool in $MONEY_TOOLS; do
  for prefix in $PREFIXES; do
    g "$(event_for "$prefix$tool")" money
    expect_block "H1 $prefix$tool"
  done
done

# O3: with order_mode unset or simulate_only, every place_* is denied even when a gate is present
# and would say ask; the gate is never consulted.
for mode in "" simulate_only Confirm "confirm " CONFIRM; do
  reset
  T_ROOT=$R_GATE
  T_MODE=$mode
  T_STUB_OUT=$VALID_ASK
  for tool in $MONEY_TOOLS; do
    for prefix in $PREFIXES; do
      g "$(event_for "$prefix$tool")" money
      expect_block "O3 mode='$mode' $prefix$tool"
    done
  done
  expect_gate_not_called "O3 mode='$mode' gate never consulted"
done

# ================================================================ H2: unreadable input blocks

reset
for ev in pre_empty_object.json pre_garbage.json pre_empty.json; do
  g "$EVENTS/$ev" money
  expect_block "H2 money $ev"
done
printf '%s' '{"tool_name":"mcp__robinhood-trading__place_equity_order"' >"$TMP/truncated.json"
g "$TMP/truncated.json" money
expect_block "H2 money truncated JSON"

# ================================================================ H3: no Python on PATH

reset
g_nopy "$EVENTS/pre_place_equity_order.json" money
expect_block "H3 money with PATH reduced to sh/cat/sed/grep/tr/printf/date/mkdir/head"
reset
T_ROOT=$R_GATE
T_MODE=confirm
T_CAP=500.00
T_STUB_OUT=$VALID_ASK
g_nopy "$EVENTS/pre_place_equity_order.json" money
expect_block "H3 confirm mode without python3 fails closed" "python3 was not found"
expect_gate_not_called "H3 confirm mode without python3 never reaches the gate"

# ================================================================ H4: unwritable state

if [ "$IS_ROOT" = 1 ]; then
  skip "H4" "running as root, so no directory is unwritable"
else
  reset
  mkdir -p "$TMP/readonly"
  chmod 500 "$TMP/readonly"
  for state in "$TMP/readonly" "$TMP/readonly/state"; do
    T_STATE=$state
    g "$EVENTS/pre_place_equity_order.json" money
    expect_block "H4 unwritable state dir ($state)"
    if [ "$ERR" = "$BLOCK_MSG" ]; then pass "H4 stderr is only the block message ($state)"; else fail "H4 stderr is only the block message ($state)" "extra stderr"; fi
  done
  printf 'x' >"$TMP/a-file"
  T_STATE=$TMP/a-file/state
  g "$EVENTS/pre_place_equity_order.json" money
  expect_block "H4 state path under a regular file"
fi

# ================================================================ H5: exempt servers

reset
T_EXEMPT=otherbroker
g "$EVENTS/pre_otherbroker_place.json" money
expect_silent "H5 guard_exempt_servers=otherbroker exempts mcp__otherbroker__"

reset
T_EXEMPT=' other , otherbroker '
g "$EVENTS/pre_otherbroker_place.json" money
expect_silent "H5 exempt list with spaces"

reset
T_EXEMPT=otherbroker
mkdir -p "$T_STATE"
printf 'otherbroker\n' >"$T_STATE/servers.txt"
g "$EVENTS/pre_otherbroker_place.json" money
expect_block "H5b exempt server that has served a Robinhood tool is not exempt"

reset
T_EXEMPT=robinhood-x
g "$EVENTS/pre_robinhood_x_place.json" money
expect_block "H5c a server named robinhood-x is never exempt"

reset
T_EXEMPT=otherbroker
g "$EVENTS/pre_place_nested_tool_name.json" money
expect_block "H5 a tool_input that smuggles a tool_name for an exempt server is blocked"

reset
T_EXEMPT=otherbroker
g "$EVENTS/pre_place_equity_order.json" money
expect_block "H5 exemption for another server does not cover Robinhood"

reset
T_EXEMPT=otherbrok
g "$EVENTS/pre_otherbroker_place.json" money
expect_block "H5 exemption needs an exact server name"

if [ "$IS_ROOT" = 1 ]; then
  skip "H5 unreadable servers.txt" "running as root"
else
  reset
  T_EXEMPT=otherbroker
  mkdir -p "$T_STATE"
  printf 'someserver\n' >"$T_STATE/servers.txt"
  chmod 000 "$T_STATE/servers.txt"
  g "$EVENTS/pre_otherbroker_place.json" money
  expect_block "H5 unreadable servers.txt means no exemption"
fi

# ================================================================ Layer 2 and exercise

reset
g "$EVENTS/pre_layer2_submit.json" money
expect_block "Layer 2 submit_order on a robinhood-named server"
reset
g "$EVENTS/pre_exercise_option.json" money
expect_block "exercise_option blocked"

# ================================================================ confirm path (stub gate)

confirm_setup() {
  reset
  T_ROOT=$R_GATE
  T_MODE=confirm
  T_CAP=500.00
}

confirm_setup
T_STUB_OUT=$VALID_ASK
g "$EVENTS/pre_place_equity_order.json" money
if [ "$NO_PY" = 1 ]; then
  expect_block "C1 confirm + valid ask, no python: blocked"
  expect_gate_not_called "C1 no python: gate not consulted"
else
  expect_ask "C1 confirm + gate ask is relayed" "Approve only if this is exactly what you intend"
  if [ "$OUT" = "$VALID_ASK" ]; then pass "C1 relayed byte for byte"; else fail "C1 relayed byte for byte" "stdout differs from the gate's line"; fi
  expect_gate_called "C1 gate consulted"
  expect_file_has "C1 ask logged as pre_ask" "$T_STATE/audit/blocked.jsonl" '"event":"pre_ask"'
fi

for tool in $PLACE_TOOLS; do
  confirm_setup
  T_STUB_OUT=$VALID_ASK
  g "$(event_for "mcp__${UUID_SERVER}__$tool")" money
  if [ "$NO_PY" = 1 ]; then expect_block "C1 $tool no python"; else expect_ask "C1 $tool on a UUID server"; fi
done

for tool in exercise_option replace_option_order; do
  confirm_setup
  T_STUB_OUT=$VALID_ASK
  g "$(event_for "mcp__robinhood-trading__$tool")" money
  expect_block "K10 $tool in confirm mode" "blocked in every order mode"
  expect_gate_not_called "K10 $tool never reaches the gate"
done

confirm_setup
T_STUB_OUT=$VALID_ASK
g "$EVENTS/pre_layer2_submit.json" money
expect_block "K10 Layer-2 verb in confirm mode"
expect_gate_not_called "K10 Layer-2 verb never reaches the gate"

for ev in pre_garbage.json pre_empty_object.json pre_place_nested_tool_name.json; do
  confirm_setup
  T_STUB_OUT=$VALID_ASK
  g "$EVENTS/$ev" money
  expect_block "K10 unreadable or ambiguous input in confirm mode ($ev)"
  expect_gate_not_called "K10 $ev never reaches the gate"
done

gate_refusal() {
  # gate_refusal ID STUB_OUT STUB_RC [CRASH]
  confirm_setup
  T_STUB_OUT=$2
  T_STUB_RC=$3
  T_STUB_CRASH=${4:-}
  g "$EVENTS/pre_place_equity_order.json" money
  expect_block "$1"
  if contains "$OUT$ERR" "$ALLOW_QUOTED"; then fail "$1 (no allow text)" "allow text leaked"; else pass "$1 (no allow text)"; fi
}

gate_refusal "K11 gate prints an allow decision" \
  "$(printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"%s%s"}}' al low)" 0
gate_refusal "K11 gate prints an allow decision with a reason" \
  "$(printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"%s%s","permissionDecisionReason":"ok"}}' al low)" 0
gate_refusal "K11 gate prints two ask lines" "$VALID_ASK$NL$VALID_ASK" 0
gate_refusal "K11 gate prints malformed JSON" '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask"' 0
gate_refusal "K11 gate prints nothing and exits 0" "" 0
gate_refusal "K11 gate crashes" "" 0 1
gate_refusal "K11 gate prints a valid ask but exits 3" "$VALID_ASK" 3
gate_refusal "K11 gate prints a valid ask but exits 1" "$VALID_ASK" 1
gate_refusal "K11 ask for the wrong hook event" \
  '{"hookSpecificOutput":{"hookEventName":"PostToolUse","permissionDecision":"ask","permissionDecisionReason":"x"}}' 0
gate_refusal "K11 ask reason with an escaped quote" \
  '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"a \"b\" c"}}' 0
gate_refusal "K11 ask reason with a tab" \
  "$(printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"a\tb"}}')" 0
gate_refusal "K11 ask with trailing text" "$VALID_ASK extra" 0
gate_refusal "K11 deny decision" \
  '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"x"}}' 0

REASON_600=$(printf '%0600d' 0 | tr 0 r)
REASON_601=$(printf '%0601d' 0 | tr 0 r)
confirm_setup
T_STUB_OUT="{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"ask\",\"permissionDecisionReason\":\"$REASON_601\"}}"
g "$EVENTS/pre_place_equity_order.json" money
expect_block "K11 ask reason of 601 characters"
confirm_setup
T_STUB_OUT="{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"ask\",\"permissionDecisionReason\":\"$REASON_600\"}}"
g "$EVENTS/pre_place_equity_order.json" money
if [ "$NO_PY" = 1 ]; then expect_block "K11 ask reason of 600 characters (no python)"; else expect_ask "K11 ask reason of 600 characters is accepted"; fi

confirm_setup
T_STUB_OUT='NO_MATCHING_REVIEW: no review of this exact order in this session; closest review differs in quantity'
T_STUB_RC=3
g "$EVENTS/pre_place_equity_order.json" money
if [ "$NO_PY" = 1 ]; then
  expect_block "K1 gate deny (no python)"
else
  expect_block "K1 gate deny is relayed as a block" "confirm gate refused: NO_MATCHING_REVIEW"
  expect_file_has "K1 gate reason code logged" "$T_STATE/audit/blocked.jsonl" '"reason_code":"NO_MATCHING_REVIEW"'
fi

confirm_setup
T_ROOT=$R_BASE
g "$EVENTS/pre_place_equity_order.json" money
if [ "$NO_PY" = 1 ]; then expect_block "confirm mode, gate missing (no python)"; else expect_block "confirm mode, gate missing" "confirm_gate.py is missing"; fi

confirm_setup
T_EXEMPT=otherbroker
g "$EVENTS/pre_otherbroker_place.json" money
expect_silent "confirm mode keeps the exempt-server rule"

# The real gate, when this build has it: with no review in the ledger it must refuse (K1).
if [ -f "$REPO/hooks/confirm_gate.py" ]; then
  reset
  T_ROOT=$REPO
  T_MODE=confirm
  T_CAP=500.00
  g "$EVENTS/pre_place_equity_order.json" money
  expect_block "K1 real confirm gate with an empty ledger refuses"
else
  skip "K1 real confirm gate" "hooks/confirm_gate.py is not in this build yet"
fi

# ================================================================ H15: the state dir never delays a verdict
# Claude Code kills a hook at its timeout and then treats the call as if the hook had no opinion,
# so in bypass or auto mode a stuck guard lets the order through. A FIFO planted where the guard
# expects a state file blocks any reader or writer that opens it. Each run below must reach its
# verdict, and close its pipes, within 5 s (the hooks' timeouts are 10-15 s).

expect_prompt() {
  # expect_prompt ID: the run finished (pipes closed) well inside the hook timeout.
  if [ "$RC" = 124 ]; then fail "$1" "the guard or a child it left behind still held the hook's pipes after 5 s"; else pass "$1"; fi
}

fifo_state() {
  # fifo_state PATH...: a fresh state dir with a FIFO at each PATH (relative to the state dir).
  reset
  mkdir -p "$T_STATE/last_tool" "$T_STATE/audit"
  FS_FIFOS=
  for FS_PATH in "$@"; do
    mkfifo "$T_STATE/$FS_PATH"
    FS_FIFOS="$FS_FIFOS $T_STATE/$FS_PATH"
  done
}

expect_no_waiters() {
  # expect_no_waiters ID: nothing the guard started is left blocked on a planted FIFO (a detached
  # child stuck forever is a leaked process, and a sign the FIFO checks regressed).
  # shellcheck disable=SC2086  # FS_FIFOS is a space-separated list of paths without spaces
  EW_HITS=$(python3 "$TMP/waiters.py" $FS_FIFOS)
  if [ -z "$EW_HITS" ]; then pass "$1"; else fail "$1" "left blocked on: $EW_HITS"; fi
}

if [ "$HAVE_PY" = 0 ]; then
  skip "H15" "the bounded runner needs python3 in the test harness"
elif ! command -v mkfifo >/dev/null 2>&1; then
  skip "H15" "mkfifo is not available"
else
  for fifo in last_tool/latest audit/blocked.jsonl servers.txt; do
    fifo_state "$fifo"
    g_bounded 5 "$EVENTS/pre_place_equity_order.json" money
    expect_prompt "H15 money verdict is prompt with a FIFO at $fifo"
    expect_no_waiters "H15 money verdict is prompt with a FIFO at $fifo: nothing left blocked"
    expect_block "H15 money still blocks with a FIFO at $fifo"
    fifo_state "$fifo"
    g_bounded 5 "$(event_for "mcp__${UUID_SERVER}__place_equity_order")" money
    expect_prompt "H15 money on the UUID server is prompt with a FIFO at $fifo"
    expect_no_waiters "H15 money on the UUID server is prompt with a FIFO at $fifo: nothing left blocked"
    expect_block "H15 money on the UUID server blocks with a FIFO at $fifo"
  done

  fifo_state last_tool/latest
  g_bounded 5 "$EVENTS/pre_place_equity_order.json" money
  expect_file_has "H15 the block is still logged when last_tool/latest is a FIFO" \
    "$T_STATE/audit/blocked.jsonl" '"reason_code":"SIMULATE_ONLY","session_id":"test-session-1","after_tool":""'

  fifo_state servers.txt
  T_EXEMPT=otherbroker
  g_bounded 5 "$EVENTS/pre_otherbroker_place.json" money
  expect_prompt "H15 exempt server with a FIFO at servers.txt is prompt"
  expect_no_waiters "H15 exempt server with a FIFO at servers.txt is prompt: nothing left blocked"
  expect_block "H15 a servers.txt that is not a regular file means no exemption"

  fifo_state last_tool/latest audit/blocked.jsonl servers.txt
  T_EXEMPT=someone-else
  g_bounded 5 "$EVENTS/pre_otherbroker_place.json" money
  expect_prompt "H15 a server not on the exempt list never reads servers.txt"
  expect_no_waiters "H15 a server not on the exempt list never reads servers.txt: nothing left blocked"
  expect_block "H15 a server not on the exempt list is blocked"

  fifo_state last_tool/latest audit/blocked.jsonl servers.txt
  g_bounded 5 "$EVENTS/pre_cancel_advanced_order.json" cancel
  expect_prompt "H15 cancel ask is prompt with FIFOs in the state dir"
  expect_no_waiters "H15 cancel ask is prompt with FIFOs in the state dir: nothing left blocked"
  expect_ask "H15 cancel still asks with FIFOs in the state dir" "BOTH legs"

  fifo_state last_tool/latest audit/blocked.jsonl servers.txt
  g_bounded 5 "$(event_for "mcp__robinhood-x__transfer_funds")" classify
  expect_prompt "H15 classify is prompt with FIFOs in the state dir"
  expect_no_waiters "H15 classify is prompt with FIFOs in the state dir: nothing left blocked"
  if [ "$RC" = 2 ] && contains "$ERR" "is not a Robinhood tool this kit knows"; then
    pass "H15 classify still blocks UNKNOWN_MONEY_TOOL with FIFOs in the state dir"
  else
    fail "H15 classify still blocks UNKNOWN_MONEY_TOOL with FIFOs in the state dir" "expected exit 2"
  fi
  fifo_state servers.txt
  g_bounded 5 "$EVENTS/pre_classify_known.json" classify
  expect_prompt "H15 learning a server is prompt with a FIFO at servers.txt"
  expect_no_waiters "H15 learning a server is prompt with a FIFO at servers.txt: nothing left blocked"
  expect_silent "H15 learning a server with a FIFO at servers.txt: no opinion"

  fifo_state last_tool/latest audit/blocked.jsonl
  T_ROOT=$R_GATE
  T_MODE=confirm
  T_CAP=500.00
  T_STUB_OUT=$VALID_ASK
  g_bounded 5 "$EVENTS/pre_place_equity_order.json" money
  expect_prompt "H15 confirm ask is prompt with FIFOs in the state dir"
  expect_no_waiters "H15 confirm ask is prompt with FIFOs in the state dir: nothing left blocked"
  if [ "$NO_PY" = 1 ]; then
    expect_block "H15 confirm with FIFOs in the state dir (no python)"
  else
    expect_ask "H15 confirm ask is still relayed with FIFOs in the state dir"
  fi

  if [ -f "$REPO/hooks/confirm_gate.py" ]; then
    # A robinhood-named server with a valid ref_id reaches the salt and ledger reads; the UUID
    # server reads servers.txt first.
    for fifo in salt ledger.jsonl refids.json .lock servers.txt; do
      fifo_state
      T_ROOT=$REPO
      T_MODE=confirm
      T_CAP=500.00
      GF_SERVER=robinhood-trading
      if [ "$fifo" = servers.txt ]; then GF_SERVER=$UUID_SERVER; fi
      printf '%s' 'test-salt-16byte' >"$T_STATE/salt"
      : >"$T_STATE/ledger.jsonl"
      rm -f "$T_STATE/$fifo"
      mkfifo "$T_STATE/$fifo"
      FS_FIFOS=$T_STATE/$fifo
      printf '{"session_id":"test-session-1","cwd":"%s","permission_mode":"default","hook_event_name":"PreToolUse","tool_name":"mcp__%s__place_equity_order","tool_input":{"symbol":"AAPL","side":"buy","type":"limit","quantity":"1","limit_price":"1.00","ref_id":"0f3c2a8e-5b7d-4c1e-9a2b-3d4e5f6a7b8c"}}\n' \
        "$TMP" "$GF_SERVER" >"$TMP/gate-event.json"
      g_bounded 12 "$TMP/gate-event.json" money
      expect_prompt "H15 real confirm gate is prompt with a FIFO at $fifo"
      expect_no_waiters "H15 real confirm gate is prompt with a FIFO at $fifo: nothing left blocked"
      expect_block "H15 real confirm gate refuses with a FIFO at $fifo"
    done
  fi

  # A symlinked blocked.jsonl is never written through (it could point at any file).
  reset
  mkdir -p "$T_STATE/audit"
  : >"$TMP/victim"
  ln -s "$TMP/victim" "$T_STATE/audit/blocked.jsonl"
  g_bounded 5 "$EVENTS/pre_place_equity_order.json" money
  expect_block "H15 money blocks with a symlinked blocked.jsonl"
  sleep 0.5
  if [ -s "$TMP/victim" ]; then fail "H15 no write through a symlinked blocked.jsonl" "the symlink target was written"; else pass "H15 no write through a symlinked blocked.jsonl"; fi
fi

# ================================================================ H6: cancels and deletes

reset
g "$EVENTS/pre_cancel_advanced_order.json" cancel
expect_ask "H6 cancel_advanced_order" "BOTH legs"
g "$EVENTS/pre_cancel_option_exercise.json" cancel
expect_ask "H6b cancel_option_exercise" "ALL queued"
g "$EVENTS/pre_cancel_equity_order.json" cancel
expect_ask "H6 cancel_equity_order" "downside protection (risk goes up)"
g "$EVENTS/pre_cancel_option_order.json" cancel
expect_ask "H6 cancel_option_order" "open and unprotected"
g "$EVENTS/pre_cancel_crypto_order.json" cancel
expect_ask "H6 cancel_crypto_order" "removes that protection"
g "$EVENTS/pre_delete_alert_preview.json" cancel
expect_silent "H6c delete_alert without confirm is a preview"
g "$EVENTS/pre_delete_alert_confirm_false.json" cancel
expect_silent "H6c delete_alert with confirm false is a preview"
g "$EVENTS/pre_delete_alert_confirm.json" cancel
expect_ask "H6d delete_alert with confirm true" "Permanently deletes"
g "$EVENTS/pre_delete_alert_confirm_string.json" cancel
expect_ask "H6d delete_alert with confirm \"true\" (string)" "Permanently deletes"
g "$EVENTS/pre_cancel_unrelated.json" cancel
expect_ask "H6 unknown cancel_* gets the generic ask" "Check what protection it removes"
g "$EVENTS/pre_garbage.json" cancel
expect_ask "H6 unparsable cancel input gets the generic ask" "Check what protection it removes"
expect_file_has "H6 cancel asks are logged" "$T_STATE/audit/blocked.jsonl" '"tool":"cancel_advanced_order"'

# ================================================================ H7: classify

reset
g "$EVENTS/pre_classify_known.json" classify
expect_silent "H7 known tool: no opinion"
wait_for_text "$T_STATE/servers.txt" rh-sandbox
if [ "$(cat "$T_STATE/servers.txt" 2>/dev/null)" = "rh-sandbox" ]; then pass "H7 server recorded"; else fail "H7 server recorded" "servers.txt should hold rh-sandbox"; fi
g "$EVENTS/pre_classify_known.json" classify
sleep 0.5
if [ "$(grep -c . "$T_STATE/servers.txt")" = 1 ]; then pass "H7 server recorded once"; else fail "H7 server recorded once" "duplicate lines"; fi
g "$EVENTS/pre_classify_unknown.json" classify
expect_ask "H7b unknown tool on a learned server" "Unknown Robinhood tool 'get_new_thing'"
g "$EVENTS/pre_classify_unknown_money.json" classify
if [ "$RC" = 2 ] && [ -z "$OUT" ] && contains "$ERR" "is not a Robinhood tool this kit knows" && contains "$ERR" "Nothing was placed"; then
  pass "H7c unknown money verb on a learned server is blocked"
else
  fail "H7c unknown money verb on a learned server is blocked" "expected exit 2 with the unknown-tool message"
fi
expect_file_has "H7c logged as UNKNOWN_MONEY_TOOL" "$T_STATE/audit/blocked.jsonl" '"reason_code":"UNKNOWN_MONEY_TOOL"'
expect_file_has "H7b logged as UNKNOWN_TOOL" "$T_STATE/audit/blocked.jsonl" '"reason_code":"UNKNOWN_TOOL"'
# Claude Code keeps "-" in MCP tool names, so a hyphenated money verb counts too.
for tool in transfer-funds submit-order place-equity-order sell-crypto; do
  g "$(event_for "mcp__rh-sandbox__$tool")" classify
  if [ "$RC" = 2 ] && contains "$ERR" "is not a Robinhood tool this kit knows"; then
    pass "H7c hyphenated money verb $tool on a learned server is blocked"
  else
    fail "H7c hyphenated money verb $tool on a learned server is blocked" "expected exit 2"
  fi
done
g "$(event_for "mcp__rh-sandbox__get-new-thing")" classify
expect_ask "H7b hyphenated unknown tool on a learned server" "Unknown Robinhood tool 'get-new-thing'"
g "$EVENTS/pre_classify_unknown_other_server.json" classify
expect_silent "H7d unknown tool on an unlearned non-robinhood server"
g "$EVENTS/pre_classify_generic.json" classify
expect_silent "H7e generic name (search) on another server: no opinion"
if grep -qx notes "$T_STATE/servers.txt"; then fail "H7e generic name does not teach servers.txt" "notes was learned"; else pass "H7e generic name does not teach servers.txt"; fi
g "$EVENTS/pre_classify_unknown_robinhood_named.json" classify
expect_ask "H7f unknown tool on a robinhood-named server" "Unknown Robinhood tool"
g "$EVENTS/pre_garbage.json" classify
expect_silent "H7g unparsable input: no opinion"
T_ROOT=$R_NOKNOWN
g "$EVENTS/pre_classify_unknown_robinhood_named.json" classify
expect_silent "H7h no known-tools.txt: no opinion"

# ================================================================ H8: session line

reset
g "$EVENTS/session_start.json" session
if [ "$RC" = 0 ] && contains "$OUT" '"hookEventName":"SessionStart"' && contains "$OUT" "SIMULATE-ONLY" \
   && contains "$OUT" "CONFIRM MODE: NOT INSTALLED" && json_valid "$TMP/out"; then
  pass "H8 session line, build without a gate"
else
  fail "H8 session line, build without a gate" "expected SIMULATE-ONLY and CONFIRM MODE: NOT INSTALLED"
fi
if [ "$NO_PY" = 0 ]; then
  if ls "$T_STATE"/audit/audit-*.jsonl >/dev/null 2>&1 && grep -q '"event":"session_start"' "$T_STATE"/audit/audit-*.jsonl; then
    pass "H8 session start logged"
  else
    fail "H8 session start logged" "no session_start line"
  fi
fi

session_expect() {
  # session_expect ID TEXT
  if [ "$RC" = 0 ] && contains "$OUT" "$2" && json_valid "$TMP/out"; then pass "$1"; else fail "$1" "expected '$2'"; fi
}

reset
T_ROOT=$R_GATE
g "$EVENTS/session_start.json" session
session_expect "S2 gate installed, mode off" "CONFIRM MODE: INSTALLED, OFF."
T_MODE=simulate_only
g "$EVENTS/session_start.json" session
session_expect "S2 gate installed, mode simulate_only" "CONFIRM MODE: INSTALLED, OFF."
T_MODE=confirm
T_CAP=500.00
g "$EVENTS/session_start.json" session
if [ "$NO_PY" = 1 ]; then
  session_expect "S3 confirm selected, no python" "SELECTED BUT UNAVAILABLE (python3 was not found)"
else
  session_expect "S3 confirm mode on" "Robinhood order mode: CONFIRM MODE: ON."
fi
T_CAP=
g "$EVENTS/session_start.json" session
if [ "$NO_PY" = 1 ]; then session_expect "S4 cap unset (no python)" "SELECTED BUT UNAVAILABLE"; else session_expect "S4 confirm without a cap" "max_order_notional_usd is not set"; fi
T_CAP=0.00
g "$EVENTS/session_start.json" session
session_expect "S4 cap of zero" "SELECTED BUT UNAVAILABLE"
T_CAP=abc
g "$EVENTS/session_start.json" session
session_expect "S4 cap not a number" "SELECTED BUT UNAVAILABLE"
T_CAP=500.00
T_AUDIT=false
g "$EVENTS/session_start.json" session
if [ "$NO_PY" = 1 ]; then session_expect "S5 audit off (no python)" "SELECTED BUT UNAVAILABLE"; else session_expect "S5 confirm with the audit log off" "the local audit log is off"; fi
if contains "$OUT" "CONFIRM MODE: ON"; then fail "S5 never says ON when unavailable" "said ON"; else pass "S5 never says ON when unavailable"; fi

# ================================================================ config edits

reset
g "$EVENTS/pre_config_bash_state.json" config
expect_ask "CFG Bash writing the kit state" "Editing Robinhood kit config"
g "$EVENTS/pre_config_edit_settings.json" config
expect_ask "CFG Edit of .claude/settings.json" "Editing Robinhood kit config"
g "$EVENTS/pre_config_harmless.json" config
expect_silent "CFG harmless write"
g "$EVENTS/pre_config_similar_dir.json" config
expect_silent "CFG project folder named robinhood-skills-fork"
g "$EVENTS/pre_garbage.json" config
expect_silent "CFG unparsable input"

tool_event() {
  # tool_event TOOL KEY VALUE: a PreToolUse event for a built-in tool; VALUE must be JSON-safe.
  printf '{"session_id":"test-session-1","cwd":"/tmp/rh-test/project","permission_mode":"default","hook_event_name":"PreToolUse","tool_name":"%s","tool_input":{"%s":"%s"}}\n' \
    "$1" "$2" "$3" >"$TMP/tool-event.json"
  printf '%s\n' "$TMP/tool-event.json"
}

# An installed plugin runs every skill script from ~/.claude/plugins/cache/...; those runs must not
# prompt (a scheduled run would be denied every script call), but edits to the hook files must.
CACHE=/Users/x/.claude/plugins/cache/yashpatel-py/robinhood-trading/2.0.0
reset
g "$(tool_event Bash command "cd $CACHE/skills/robinhood-tax-loss-harvesting && python3 scripts/wash_sale.py run < in.json > out.json")" config
expect_silent "CFG skill script run from the plugin cache"
g "$(tool_event Bash command "python3 $CACHE/skills/robinhood-trading/scripts/rh_time.py session < in.json")" config
expect_silent "CFG absolute-path skill script in the plugin cache"
g "$(tool_event Read file_path "$CACHE/skills/robinhood-trading/SKILL.md")" config
expect_silent "CFG any other plugin-cache path"
for target in hooks/hooks.json hooks/guard.sh hooks/lib/canon.py hooks/known-tools.txt .claude-plugin/plugin.json \
              shared/scripts/kitconfig.py connector/tool-classes.json; do
  g "$(tool_event Bash command "sed -i s/a/b/ $CACHE/$target")" config
  expect_ask "CFG Bash edit of the cached $target" "Editing Robinhood kit config"
  g "$(tool_event Write file_path "$CACHE/$target")" config
  expect_ask "CFG Write to the cached $target" "Editing Robinhood kit config"
done
g "$(tool_event Bash command "cd ~/.local/state/robinhood-skill*/last_tool && rm -f latest")" config
expect_ask "CFG a glob aimed at the state folder" "Editing Robinhood kit config"
g "$(tool_event Bash command "ls \$XDG_STATE_HOME")" config
expect_ask "CFG XDG_STATE_HOME" "Editing Robinhood kit config"

# Shell routes around the MCP hook (docs/safety-model.md gaps): a pattern tripwire, one test each.
for cmd in "curl -s https://agent.robinhood.com/mcp/trading" \
           "claude -p hi --settings {disableAllHooks:true}" \
           "claude -p hi --setting-sources project" \
           "CLAUDE_CONFIG_DIR=/tmp/x claude -p hi" \
           "claude --bare -p hi" \
           "claude --dangerously-skip-permissions -p hi" \
           "security find-generic-password -s Claude Code-credentials -w" \
           "cat ~/.claude/.credentials.json" \
           "jq .mcpOAuth ~/.claude/.credentials"; do
  g "$(tool_event Bash command "$cmd")" config
  expect_ask "CFG Bash tripwire: $cmd" "outside the order guard"
done
g "$(tool_event Write file_path "/tmp/rh-test/project/notes-about-agent.robinhood.com.md")" config
expect_silent "CFG the shell tripwire is for Bash only"
g "$(tool_event Bash command "git status")" config
expect_silent "CFG an ordinary shell command"

# ================================================================ audit mode, logging, H11, H13

reset
g "$EVENTS/pre_place_equity_order.json" money
expect_file_has "LOG block written to blocked.jsonl" "$T_STATE/audit/blocked.jsonl" '"event":"pre_block","tool":"place_equity_order","server":"robinhood-trading","reason_code":"SIMULATE_ONLY"'
if [ -n "$(find "$T_STATE" -prune -type d -perm 700 2>/dev/null)" ]; then pass "LOG state dir is 0700"; else fail "LOG state dir is 0700" "wrong mode"; fi

reset
T_AUDIT=false
g "$EVENTS/pre_place_equity_order.json" money
expect_block "LOG audit off still blocks"
expect_no_file "LOG audit off writes no blocked.jsonl" "$T_STATE/audit/blocked.jsonl"

reset
T_ROOT=$REPO
g "$EVENTS/pre_place_equity_order.json" money
wait_for_text "$T_STATE/audit/blocked.jsonl" '"event":"pre_block"'
g "$EVENTS/post_get_equity_quotes.json" audit
expect_silent "AUD audit mode prints nothing"
if [ "$NO_PY" = 1 ]; then
  if ls "$T_STATE"/audit/audit-*.jsonl >/dev/null 2>&1; then fail "AUD no python: nothing logged" "audit file written"; else pass "AUD no python: nothing logged, exit 0"; fi
else
  cat "$T_STATE"/audit/audit-*.jsonl >"$TMP/audit-all" 2>/dev/null
  if grep -q '"event":"pre_block","tool":"place_equity_order"' "$TMP/audit-all" \
     && grep -q '"event":"post","tool":"get_equity_quotes"' "$TMP/audit-all"; then
    pass "H11 guard block folded into the chain, then the post line"
  else
    fail "H11 guard block folded into the chain, then the post line" "missing lines"
  fi
  if grep -q 'test-session-1' "$TMP/audit-all"; then fail "H11 session id hashed" "raw session id in the audit file"; else pass "H11 session id hashed"; fi
  expect_no_file "H11 blocked.jsonl consumed" "$T_STATE/audit/blocked.jsonl"
fi

reset
T_ROOT=$REPO
T_AUDIT=false
g "$EVENTS/post_get_equity_quotes.json" audit
expect_silent "AUD audit_log=false"
expect_no_file "AUD audit_log=false writes nothing" "$T_STATE/audit"

reset
T_ROOT=$REPO
g "$EVENTS/pre_garbage.json" audit
expect_silent "H13 audit with unparsable input"

if [ "$IS_ROOT" = 1 ]; then
  skip "H13 unwritable state" "running as root"
else
  reset
  T_ROOT=$REPO
  T_STATE=$TMP/readonly/state
  g "$EVENTS/post_get_equity_quotes.json" audit
  expect_silent "H13 audit with an unwritable state dir"
  T_STATE=$TMP/a-file/state
  g "$EVENTS/post_get_equity_quotes.json" audit
  expect_silent "H13 audit with the state path under a file"
fi

# ================================================================ Codex and Cursor adapters

reset
g "$EVENTS/codex_place.json" money --format codex
expect_block "CODEX place_equity_order"
g "$EVENTS/codex_read.json" money --format codex
expect_silent "CODEX read tool: no opinion"
g "$EVENTS/pre_garbage.json" money --format codex
if [ "$RC" = 2 ] && contains "$ERR" "fails closed"; then pass "CODEX unparsable input blocks"; else fail "CODEX unparsable input blocks" "expected exit 2"; fi
for tool in $MONEY_TOOLS; do
  g "$(event_for "mcp__robinhood__$tool")" money --format codex
  expect_block "CODEX $tool"
done
g "$EVENTS/pre_layer2_submit.json" money --format codex
expect_block "CODEX Layer-2 verb on a robinhood server"
for tool in transfer_funds withdraw_cash sell_crypto place-equity-order transfer-funds; do
  g "$(event_for "mcp__robinhood__$tool")" money --format codex
  expect_block "CODEX $tool on a robinhood server"
done
g "$(event_for "mcp__db__execute_sql")" money --format codex
expect_silent "CODEX execute_sql on another server"
g "$EVENTS/pre_classify_unknown_other_server.json" money --format codex
expect_silent "CODEX unrelated tool"

cursor_deny() {
  if [ "$RC" = 0 ] && contains "$OUT" '{"permission":"deny",' && contains "$OUT" '"agentMessage":"' \
     && contains "$OUT" '"user_message":"' && ! contains "$OUT" "$NL" && json_valid "$TMP/out"; then
    pass "$1"
  else
    fail "$1" "expected one JSON deny line and exit 0"
  fi
}
g "$EVENTS/cursor_place.json" money --format cursor
cursor_deny "CURSOR place_equity_order"
g "$EVENTS/cursor_place.json" money --format=cursor
cursor_deny "CURSOR --format=cursor spelling"
g "$EVENTS/cursor_read.json" money --format cursor
expect_silent "CURSOR read tool: no opinion"
g "$EVENTS/cursor_layer2.json" money --format cursor
cursor_deny "CURSOR Layer-2 verb on the Robinhood url"
g "$EVENTS/cursor_other_send.json" money --format cursor
expect_silent "CURSOR send_message on another server"
g "$EVENTS/pre_garbage.json" money --format cursor
cursor_deny "CURSOR unparsable input denies"
g "$EVENTS/pre_empty.json" money --format cursor
cursor_deny "CURSOR empty input denies"
g "$EVENTS/pre_exercise_option.json" money --format cursor
cursor_deny "CURSOR exercise_option with a server prefix"

reset
g "$EVENTS/pre_place_equity_order.json" nonsense-mode
if [ "$RC" = 2 ]; then pass "unknown guard mode exits 2"; else fail "unknown guard mode exits 2" "wrong exit"; fi

# ================================================================ H9: never allow

if [ -f "$TMP/all-output" ] && grep -qF -- "$ALLOW_QUOTED" "$TMP/all-output"; then
  fail "H9 no guard output contains an allow decision" "found $ALLOW_QUOTED"
else
  pass "H9 no guard output contains an allow decision"
fi
if grep -qF -- "$ALLOW_DECISION" "$TMP/all-output" 2>/dev/null; then
  fail "H9 no allow permissionDecision" "found"
else
  pass "H9 no allow permissionDecision"
fi
STATIC_RE='"(permissionDecision|permission)"[[:space:]]*:[[:space:]]*"'$(printf '%s%s' al low)'"'
if grep -rEl -- "$STATIC_RE" "$REPO/hooks" "$REPO/integrations" >"$TMP/allow-hits" 2>/dev/null; then
  fail "H9 no hook or integration file contains an allow decision" "$(cat "$TMP/allow-hits")"
else
  pass "H9 no hook or integration file contains an allow decision"
fi

# ================================================================ H14: matchers

MATCHERS=$(sed -n 's/.*"matcher": *"\([^"]*\)".*/\1/p' "$REPO/hooks/hooks.json")
M_MONEY1=$(printf '%s\n' "$MATCHERS" | sed -n 1p)
M_MONEY2=$(printf '%s\n' "$MATCHERS" | sed -n 2p)
M_CANCEL=$(printf '%s\n' "$MATCHERS" | sed -n 3p)
M_CLASSIFY=$(printf '%s\n' "$MATCHERS" | sed -n 4p)
M_CONFIG=$(printf '%s\n' "$MATCHERS" | sed -n 5p)
M_CODEX=$(sed -n 's/.*"matcher": *"\([^"]*\)".*/\1/p' "$REPO/integrations/codex/hooks.json")

matches() {
  printf '%s\n' "$2" | grep -Eq -- "$1"
}
expect_match() {
  # expect_match ID REGEX NAME yes|no
  if matches "$2" "$3"; then got=yes; else got=no; fi
  if [ "$got" = "$4" ]; then pass "$1"; else fail "$1" "regex '$2' on '$3': expected $4"; fi
}

expect_match "H14 layer 2 matches mcp__robinhood-trading__submit_order" "$M_MONEY2" mcp__robinhood-trading__submit_order yes
expect_match "H14 layer 2 ignores mcp__db__execute_sql" "$M_MONEY2" mcp__db__execute_sql no
expect_match "H14 layer 2 ignores mcp__pdf__convert_to_pdf" "$M_MONEY2" mcp__pdf__convert_to_pdf no
expect_match "H14 layer 2 matches the plugin connector" "$M_MONEY2" mcp__plugin_unofficial-rh-connector_robinhood__transfer_funds yes
for tool in $MONEY_TOOLS; do
  for prefix in $PREFIXES; do
    expect_match "H14 layer 1 matches $prefix$tool" "$M_MONEY1" "$prefix$tool" yes
  done
  expect_match "H14 codex matcher matches $tool" "$M_CODEX" "mcp__robinhood__$tool" yes
done
expect_match "H14 layer 1 ignores get_equity_quotes" "$M_MONEY1" mcp__robinhood-trading__get_equity_quotes no
expect_match "H14 layer 1 ignores review_equity_order" "$M_MONEY1" mcp__robinhood-trading__review_equity_order no
expect_match "H14 layer 1 ignores Bash" "$M_MONEY1" Bash no
expect_match "H14 cancel matcher: delete_alert" "$M_CANCEL" mcp__robinhood-trading__delete_alert yes
expect_match "H14 cancel matcher: cancel_advanced_order" "$M_CANCEL" mcp__x__cancel_advanced_order yes
expect_match "H14 cancel matcher ignores get_alerts" "$M_CANCEL" mcp__robinhood-trading__get_alerts no
expect_match "H14 classify matcher: any MCP tool" "$M_CLASSIFY" mcp__rh-sandbox__get_new_thing yes
expect_match "H14 classify matcher ignores built-in tools" "$M_CLASSIFY" Read no
for tool in Write Edit MultiEdit Bash; do
  expect_match "H14 config matcher: $tool" "$M_CONFIG" "$tool" yes
done
expect_match "H14 config matcher ignores BashOutput" "$M_CONFIG" BashOutput no

# Claude Code keeps "-" in MCP tool names (it replaces other characters with "_").
M_AUDIT=$(printf '%s\n' "$MATCHERS" | sed -n 6p)
expect_match "H14 classify matcher: hyphenated tool" "$M_CLASSIFY" mcp__x__some-tool yes
expect_match "H14 classify matcher: UUID server, hyphenated tool" "$M_CLASSIFY" "mcp__${UUID_SERVER}__transfer-funds" yes
expect_match "H14 audit matcher: hyphenated tool" "$M_AUDIT" mcp__x__some-tool yes
expect_match "H14 audit matcher ignores built-in tools" "$M_AUDIT" Read no
expect_match "H14 layer 1 matches place-equity-order" "$M_MONEY1" mcp__x__place-equity-order yes
expect_match "H14 layer 1 ignores placeholder_text" "$M_MONEY1" mcp__x__placeholder_text no
expect_match "H14 layer 2 matches transfer-funds" "$M_MONEY2" mcp__robinhood-trading__transfer-funds yes
expect_match "H14 cancel matcher: cancel-order" "$M_CANCEL" mcp__x__cancel-order yes

# The Codex README promises what the shipped Codex matcher routes to the guard, not only what
# guard.sh blocks when called directly: every verb it lists must reach the guard on a Robinhood
# server, and the matcher must still leave reads, reviews and other servers alone.
# shellcheck disable=SC2016  # the backticks are literal Markdown, not a command substitution
CODEX_VERBS=$(sed -n '/^## What it does/,/^## What it does not/p' "$REPO/integrations/codex/README.md" \
  | grep -o '`[a-z][a-z]*_`' | tr -d '`' | sort -u)
if [ "$(printf '%s\n' "$CODEX_VERBS" | grep -c .)" -ge 13 ]; then
  pass "H14 the Codex README lists the verbs it blocks"
else
  fail "H14 the Codex README lists the verbs it blocks" "found: $CODEX_VERBS"
fi
for verb in $CODEX_VERBS; do
  expect_match "H14 codex matcher routes the README's ${verb}x on a robinhood server" "$M_CODEX" "mcp__robinhood__${verb}x" yes
  expect_match "H14 codex matcher routes ${verb%_}-x on a robinhood server" "$M_CODEX" "mcp__robinhood__${verb%_}-x" yes
done
for name in mcp__robinhood__transfer_funds mcp__robinhood__submit_order mcp__robinhood__sell_crypto \
            "mcp__plugin_unofficial-rh-connector_robinhood__withdraw_cash"; do
  expect_match "H14 codex matcher routes $name" "$M_CODEX" "$name" yes
done
for name in mcp__db__execute_sql mcp__pdf__convert_to_pdf mcp__robinhood-trading__get_accounts \
            mcp__robinhood-trading__review_equity_order mcp__robinhood-trading__get_equity_quotes; do
  expect_match "H14 codex matcher leaves $name alone" "$M_CODEX" "$name" no
done
if [ -n "$(printf '%s\n' "$MATCHERS" | sed -n 6p)" ] && [ -z "$(printf '%s\n' "$MATCHERS" | sed -n 7p)" ]; then
  pass "H14 hooks.json has six matchers"
else
  fail "H14 hooks.json has six matchers" "unexpected matcher count"
fi
if json_valid "$REPO/hooks/hooks.json"; then pass "hooks.json is valid JSON"; else fail "hooks.json is valid JSON" "invalid"; fi
for f in "$REPO/integrations/claude-code/settings.deny.json" "$REPO/integrations/codex/hooks.json" "$REPO/integrations/cursor/hooks.json"; do
  if json_valid "$f"; then pass "valid JSON: ${f#"$REPO"/}"; else fail "valid JSON: ${f#"$REPO"/}" "invalid"; fi
done

# ================================================================ summary

if [ "$NO_PY" = 1 ]; then LABEL="no-python"; else LABEL="default"; fi
printf 'test_guard.sh (%s, guard shell %s): %s passed, %s failed, %s skipped\n' "$LABEL" "$GUARD_SH" "$PASS" "$FAIL" "$SKIP"
[ "$FAIL" = 0 ]
