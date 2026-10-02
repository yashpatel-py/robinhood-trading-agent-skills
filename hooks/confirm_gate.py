#!/usr/bin/env python3
"""confirm_gate.py - binds a live place_* call to the review the user saw, then asks the human.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: the kit is simulate-only by default, and hooks/guard.sh blocks every order-placing
call. A user who wants the agent to place orders can switch the plugin option order_mode to
"confirm". Even then, the agent must not decide on its own what goes live. This gate lets a
place_* call through to Claude Code's permission prompt only when:

  * a human can answer that prompt (permission mode default or acceptEdits; no headless run),
  * the call is byte-for-byte the order the user reviewed in this session, recently, on the same
    MCP server: its canon.py fingerprint matches a successful review_*/preview_* entry that
    hooks/audit_log.py wrote to $STATE/ledger.jsonl for that server, no older than
    review_ttl_seconds,
  * its ref_id is a fresh UUID (or an honest retry of one that never went through), sent exactly
    as it will be recorded (no surrounding spaces), and the review has not already been spent on a
    different order,
  * its notional is computable and within max_order_notional_usd (an option order that opens a
    short leg is refused: its premium does not bound what it commits), and the user's own [policy]
    limits and the kit's order rules (order_lint.py) pass.

Then it prints exactly one "ask" line that restates the order in plain words, and the human decides
in the prompt. It never prints "allow": the prompt is the last gate, and the only one that does not
depend on files an agent could edit. exercise_option is never confirmable (guard.sh does not even
call this gate for it), and neither is any tool other than the four place_* tools that have a
review twin.

Contract with hooks/guard.sh (money mode, order_mode = confirm, tool name starts with place_):
  stdin   the Claude Code PreToolUse event JSON
  env     ROBINHOOD_SKILLS_STATE (set by guard.sh), CLAUDE_PLUGIN_OPTION_ORDER_MODE,
          CLAUDE_PLUGIN_OPTION_MAX_ORDER_NOTIONAL_USD, CLAUDE_PLUGIN_OPTION_REVIEW_TTL_SECONDS,
          CLAUDE_PLUGIN_OPTION_AUDIT_LOG
  pass    stdout = one line {"hookSpecificOutput":{"hookEventName":"PreToolUse",
          "permissionDecision":"ask","permissionDecisionReason":"<1-600 chars, ASCII, no quote or
          backslash>"}}, exit 0
  deny    stdout = one line "<CODE>: <reason>" (no double quotes), exit 3
  crash   stdout = "GATE_FAILED: ...", exit 4
guard.sh relays the ask only when it is exactly that shape and the exit code is 0; anything else,
including a crash, blocks the call (exit 2).

Deadline: Claude Code kills a hook that outlives its timeout (15 s for the money hook) and then
treats the call as if the hook had no opinion, which in bypass or auto mode lets it through. So
the checks run in a forked child, and this process only waits for it: after 8 s it kills the
child and answers GATE_FAILED (exit 4), which guard.sh turns into a block. The child writes to a
pipe of its own, never to guard.sh's stdout, so even a child stuck in the kernel (a stalled
network home) cannot hold guard.sh past that answer. State files are opened non-blocking and must
be regular files, so a FIFO planted in the state directory is refused at once instead of hanging.

State (the same files and lock as hooks/audit_log.py): reads $STATE/salt, $STATE/servers.txt,
$STATE/ledger.jsonl and $STATE/refids.json; on an ask it records the ref_id as "pending" in
refids.json and marks the matched review consumed_by = ref_id. audit_log.py updates the ref_id to
succeeded or failed after the call. Nothing is written on a deny. The lock is $STATE/.lock
(O_CREAT|O_EXCL, 2 s wait, stale after 30 s).

Usage:
    python3 hooks/confirm_gate.py < pre-tool-use-event.json     (from guard.sh)
    python3 hooks/confirm_gate.py --selftest                    (temporary state; touches nothing else)
Stdlib only, Python >= 3.9, no network.
"""

import datetime
import errno
import hashlib
import importlib.util
import json
import os
import re
import select
import shutil
import signal
import stat
import sys
import tempfile
import time
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

sys.dont_write_bytecode = True  # never drop __pycache__ into the plugin's own folders

VERSION = "2.0.0"
DENY_EXIT = 3
CRASH_EXIT = 4
DEADLINE_SECONDS = 8.0  # well inside the money hook's 15 s timeout (hooks/hooks.json)
# Tests shorten the deadline with this variable; it can only shorten it, never lengthen it.
DEADLINE_ENV = "ROBINHOOD_SKILLS_GATE_DEADLINE_SECONDS"
DEADLINE_MIN = 0.2
OUTPUT_MAX = 64 * 1024
REASON_MAX = 600
DENY_TEXT_MAX = 260  # guard.sh shows the first 300 characters of a refusal (code included) to the agent
DEFAULT_TTL = 300
TTL_MIN = 60
TTL_MAX = 900
FUTURE_SKEW_SECONDS = 5
LOCK_WAIT_SECONDS = 2.0
STALE_LOCK_SECONDS = 30.0
CENT = Decimal("0.01")
HUNDRED = Decimal(100)

PLACE_TO_REVIEW = {
    "place_equity_order": "review_equity_order",
    "place_option_order": "review_option_order",
    "place_advanced_order": "review_advanced_order",
    "place_crypto_order": "preview_crypto_order",
}
ORDER_GETTER = {
    "equity": "get_equity_orders",
    "option": "get_option_orders",
    "oco": "get_advanced_orders",
    "crypto": "get_crypto_orders",
}
INTERACTIVE_MODES = ("default", "acceptEdits")
# [policy] rules the gate can evaluate from the call alone. Concentration, earnings blackout and
# orders-per-day need live account data a hook cannot fetch; the skill checks them before the review.
GATE_POLICY_RULES = ("max_order_usd", "symbol_allowlist", "symbol_denylist", "allow_options", "allow_crypto",
                     "allowed_sessions", "max_option_contracts")
FALSE_WORDS = ("false", "0", "no", "off")

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
CAP_RE = re.compile(r"^[0-9]+(\.[0-9]+)?$")  # the same shape guard.sh accepts for the session line
TTL_RE = re.compile(r"^\s*[0-9]{1,6}\s*$")
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d{1,6})?Z$")
SAFE_RE = re.compile(r"[^A-Za-z0-9 ._:/$*+=%,()-]")
ASK_RE = re.compile(r'^\{"hookSpecificOutput":\{"hookEventName":"PreToolUse","permissionDecision":"ask",'
                    r'"permissionDecisionReason":"([^"\\]{1,600})"\}\}$')


class Deny(Exception):
    """A refusal: guard.sh turns it into exit 2 with the code and text shown to the agent."""

    def __init__(self, code, text):
        Exception.__init__(self, text)
        self.code = code
        self.text = text


# --------------------------------------------------------------------------- small utilities

def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (dt.microsecond // 1000)


def parse_ts(value):
    if not isinstance(value, str):
        return None
    m = TS_RE.match(value)
    if not m:
        return None
    try:
        base = datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    return base.replace(tzinfo=datetime.timezone.utc)


def _us_eastern_offset_hours(dt_utc):
    """US Eastern offset without tz data: DST from the second Sunday of March 07:00 UTC to the
    first Sunday of November 06:00 UTC."""
    year = dt_utc.year
    march8 = datetime.datetime(year, 3, 8, tzinfo=datetime.timezone.utc)
    start = march8 + datetime.timedelta(days=(6 - march8.weekday()) % 7, hours=7)
    nov1 = datetime.datetime(year, 11, 1, tzinfo=datetime.timezone.utc)
    end = nov1 + datetime.timedelta(days=(6 - nov1.weekday()) % 7, hours=6)
    return -4 if start <= dt_utc < end else -5


def to_eastern(dt_utc):
    try:
        from zoneinfo import ZoneInfo  # Python 3.9+, needs tz data on the machine
        return dt_utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return dt_utc.astimezone(datetime.timezone(datetime.timedelta(hours=_us_eastern_offset_hours(dt_utc))))


def plugin_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def state_dir(env):
    value = env.get("ROBINHOOD_SKILLS_STATE")
    if value:
        return value
    base = env.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "robinhood-skills")


def clean(value, limit=40):
    """Printable ASCII only, no quotes or backslashes: the ask reason goes into JSON unescaped."""
    text = SAFE_RE.sub("", str(value))
    text = " ".join(text.split())
    return text[:limit]


def deny_line(code, text):
    body = " ".join(str(text).replace('"', "'").replace("\\", "/").split())
    return "%s: %s" % (code, body[:DENY_TEXT_MAX])


def dec(value):
    """Decimal from a decimal string (or a JSON number); None when absent or malformed."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float):
        value = repr(value)
    text = str(value).strip()
    if not re.match(r"^[+-]?(\d+(\.\d*)?|\.\d+)$", text):
        return None
    try:
        d = Decimal(text)
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def money(d):
    return str(d.quantize(CENT, rounding=ROUND_HALF_UP))


def plain(d):
    """Decimal as written in a ticket: no exponent, no trailing zeros ("31.240" -> "31.24")."""
    text = format(d, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def lower(value):
    return value.strip().lower() if isinstance(value, str) else value


def split_tool(name):
    """mcp__<server>__<tool> -> (server, tool); anything else -> (None, None)."""
    if not isinstance(name, str) or not name.startswith("mcp__"):
        return None, None
    rest = name[len("mcp__"):]
    if "__" not in rest:
        return None, None
    server, bare = rest.rsplit("__", 1)
    if not server or not bare:
        return None, None
    return server, bare


def read_regular(path):
    """The bytes of a regular file. FileNotFoundError when it is absent; another OSError when it is
    anything else (a FIFO, a device, a directory) or cannot be read. The open never blocks: a FIFO
    planted in the state directory would otherwise hold the gate until the hook is killed."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "not a regular file", path)
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def read_json_file(path):
    """(exists, value). A file that exists but cannot be parsed returns (True, None)."""
    try:
        raw = read_regular(path)
    except FileNotFoundError:
        return False, None
    except OSError:
        return True, None
    try:
        return True, json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return True, None


def atomic_write(path, data):
    tmp = "%s.tmp-%d-%s" % (path, os.getpid(), os.urandom(3).hex())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def load_module(name, extra_dirs=()):
    """hooks/lib/<name>.py (the synced copy in a plugin install), else shared/scripts/<name>.py."""
    root = plugin_root()
    folders = [os.path.join(root, "hooks", "lib"), os.path.join(root, "shared", "scripts")]
    folders += [os.path.join(root, d) for d in extra_dirs]
    for folder in folders:
        path = os.path.join(folder, name + ".py")
        if not os.path.isfile(path):
            continue
        try:
            spec = importlib.util.spec_from_file_location("rh_gate_" + name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except Exception:
            continue
    return None


def load_classes():
    root = plugin_root()
    for path in (os.path.join(root, "hooks", "tool-classes.json"), os.path.join(root, "connector", "tool-classes.json")):
        exists, data = read_json_file(path)
        tools = data.get("tools") if isinstance(data, dict) else None
        if isinstance(tools, list):
            return dict((t["name"], t) for t in tools if isinstance(t, dict) and isinstance(t.get("name"), str))
        if exists:
            return None  # present but unreadable: do not guess
    return {}


# --------------------------------------------------------------------------- lock

def acquire_lock(path):
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(fd, str(os.getpid()).encode("ascii"))
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - os.stat(path).st_mtime > STALE_LOCK_SECONDS:
                    os.remove(path)  # left behind by a killed process
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        except OSError:
            return False


def release_lock(path):
    try:
        os.remove(path)
    except OSError:
        pass


# --------------------------------------------------------------------------- the checks

def check_event(event):
    """Step 1: the four keys the gate needs, and a PreToolUse event."""
    if not isinstance(event, dict):
        raise Deny("CANNOT_VERIFY_SESSION", "the hook input is not a JSON object, so the gate cannot tell which "
                                            "session or order this is")
    for key in ("tool_name", "tool_input", "session_id", "permission_mode"):
        value = event.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise Deny("CANNOT_VERIFY_SESSION", "the hook input has no %s, so the gate cannot bind this call to a "
                                                "review in this session" % key)
    if not isinstance(event["tool_input"], dict) or not isinstance(event["session_id"], str):
        raise Deny("CANNOT_VERIFY_SESSION", "the hook input has an unexpected tool_input or session_id shape")
    hook_event = event.get("hook_event_name")
    if hook_event is not None and hook_event != "PreToolUse":
        raise Deny("CANNOT_VERIFY_SESSION", "the gate answers PreToolUse events only")


def check_tool(event, classes):
    """The four place_* tools with a review twin; never exercise_option, replace_* or anything else."""
    server, bare = split_tool(event.get("tool_name"))
    if bare not in PLACE_TO_REVIEW:
        raise Deny("NOT_CONFIRMABLE", "%s is not confirmable in any order mode; confirm mode covers reviewed "
                                      "place_equity_order, place_option_order, place_crypto_order and "
                                      "place_advanced_order only" % clean(bare or event.get("tool_name"), 60))
    if classes is None:
        raise Deny("GATE_FAILED", "hooks/tool-classes.json is present but unreadable, so the gate cannot map the "
                                  "place tool to its review")
    entry = classes.get(bare)
    if classes and (not isinstance(entry, dict) or entry.get("class") != "money"
                    or entry.get("review_twin") != PLACE_TO_REVIEW[bare]):
        raise Deny("GATE_FAILED", "tool-classes.json does not list %s as a money tool with review twin %s"
                   % (bare, PLACE_TO_REVIEW[bare]))
    return server, bare, PLACE_TO_REVIEW[bare]


def check_mode(event, env):
    """Steps 2-3: confirm mode switched on, a human at the prompt, the audit ledger on, a cap set."""
    if env.get("CLAUDE_PLUGIN_OPTION_ORDER_MODE", "") != "confirm":
        raise Deny("CONFIRM_MODE_OFF", "order_mode is not confirm, so live orders stay blocked (simulate-only)")
    mode = event.get("permission_mode")
    if mode not in INTERACTIVE_MODES:
        raise Deny("NOT_INTERACTIVE", "confirm mode needs a human at a permission prompt; permission mode %s can "
                                      "approve without one" % clean(mode, 30))
    entry = lower(env.get("CLAUDE_CODE_ENTRYPOINT", ""))
    if entry.startswith("sdk") or entry in ("headless", "print"):
        raise Deny("NOT_INTERACTIVE", "confirm mode needs a human at a permission prompt; this session runs "
                                      "headless or through the SDK (entrypoint %s)" % clean(entry, 30))
    if lower(env.get("CLAUDE_CODE_SESSION_ATTENDED", "")) in FALSE_WORDS[:3]:
        raise Deny("NOT_INTERACTIVE", "confirm mode needs a human at a permission prompt; this session reports "
                                      "that nobody is attending it")
    ci = lower(env.get("CI", ""))
    if ci and ci not in FALSE_WORDS:
        raise Deny("NOT_INTERACTIVE", "confirm mode never places orders from CI or other unattended runs")
    if lower(env.get("CLAUDE_PLUGIN_OPTION_AUDIT_LOG", "true")) in FALSE_WORDS:
        raise Deny("AUDIT_LOG_OFF", "the local audit log is off, and the gate binds orders to reviews through its "
                                    "review ledger; turn audit_log back on to use confirm mode")
    raw_cap = env.get("CLAUDE_PLUGIN_OPTION_MAX_ORDER_NOTIONAL_USD", "").strip()
    cap = dec(raw_cap) if CAP_RE.match(raw_cap or "-") else None
    if cap is None or cap <= 0:
        raise Deny("CAP_NOT_SET", "max_order_notional_usd is not set to an amount above zero (for example "
                                  "500.00); confirm mode denies every live order until it is")
    raw_ttl = env.get("CLAUDE_PLUGIN_OPTION_REVIEW_TTL_SECONDS", "")
    ttl = int(raw_ttl) if TTL_RE.match(raw_ttl or "") else DEFAULT_TTL
    return cap, max(TTL_MIN, min(TTL_MAX, ttl))


def check_server(server, state):
    """The place call must go to a server this kit has seen serve Robinhood's tools. That alone does
    not make a review binding: hooks/audit_log.py records a review from any server that serves a
    known review tool name (the kit's own sandbox, an eval mock, a look-alike), so find_review also
    requires the review to come from this same server."""
    if not server:
        raise Deny("CANNOT_VERIFY_SESSION", "the tool name is not in the mcp__<server>__<tool> form")
    if "robinhood" in server.lower():
        return
    try:
        text = read_regular(os.path.join(state, "servers.txt")).decode("utf-8", "replace")
        if server in set(line.strip() for line in text.splitlines()):
            return
    except OSError:
        pass
    raise Deny("NOT_ROBINHOOD_SERVER", "server %s has not served a known Robinhood tool in this kit's records, so "
                                       "no Robinhood review can match this call" % clean(server, 60))


def check_ref_id(params):
    """The ref_id exactly as sent. A padded or newline-terminated value is refused rather than
    trimmed: the broker receives the raw string, and if it does not trim it, a trimmed record here
    would call a second live order a retry."""
    ref_id = params.get("ref_id")
    if not isinstance(ref_id, str) or not UUID_RE.fullmatch(ref_id):
        raise Deny("REF_ID_INVALID", "ref_id must be a fresh UUID (for example from uuid4, 36 characters, no spaces) "
                                     "generated for this approved order; reuse it only to retry a call that never "
                                     "went through")
    return ref_id


def read_salt(state):
    try:
        salt = read_regular(os.path.join(state, "salt"))
    except OSError:
        return None
    return salt[:16] if len(salt) >= 16 else None


def salted(salt, text, length):
    """The salted hash hooks/audit_log.py writes: session = 12 hex, server = 8 hex."""
    if salt is None or not isinstance(text, str) or not text:
        return None
    return hashlib.sha256(salt + text.encode("utf-8")).hexdigest()[:length]


def session_hash(state, session_id):
    return salted(read_salt(state), session_id, 12)


def read_refids(state):
    exists, data = read_json_file(os.path.join(state, "refids.json"))
    if not exists:
        return {}
    if not isinstance(data, dict):
        raise Deny("GATE_FAILED", "refids.json is unreadable, so the gate cannot prove this ref_id is new")
    return data


def check_ref_history(refids, ref_id, fingerprint, family):
    """Step 6: a ref_id belongs to one order; a success is final; pending or failed may be retried."""
    record = refids.get(ref_id)
    if record is None:
        wanted = ref_id.lower()
        if any(isinstance(key, str) and key.lower() == wanted for key in refids):
            # Robinhood may treat a different spelling as a new idempotency key, so a "retry" with
            # changed letter case could place the order twice.
            raise Deny("REF_ID_REUSED", "this ref_id matches an earlier one except for letter case; a retry must "
                                        "resend the ref_id exactly as first sent, and a new order needs a new UUID")
        return None
    if not isinstance(record, dict) or record.get("fingerprint") != fingerprint:
        raise Deny("REF_ID_REUSED", "this ref_id was already used for a different order; generate a new UUID only "
                                    "after the user approves a newly reviewed ticket")
    status = record.get("status")
    if status in ("pending", "failed"):
        return status
    raise Deny("DUPLICATE_ORDER", "this ref_id already went through (status %s); the order may be live. Check %s "
                                  "and do not place it again" % (clean(status, 20), ORDER_GETTER[family]))


def read_ledger(state):
    """[(line_index, raw_bytes, entry_or_None)] for every line of ledger.jsonl; None when absent."""
    path = os.path.join(state, "ledger.jsonl")
    try:
        raw = read_regular(path)
    except FileNotFoundError:
        return None
    except OSError:
        raise Deny("GATE_FAILED", "ledger.jsonl is unreadable or not a regular file")
    rows = []
    for index, line in enumerate(raw.split(b"\n")):
        if not line.strip():
            continue
        try:
            entry = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            entry = None
        rows.append((index, line, entry if isinstance(entry, dict) else None))
    return rows


def masked_canonical(canonical):
    out = {}
    for key, value in canonical.items():
        low = key.lower()
        if "account" in low and not low.endswith("_type") and isinstance(value, (str, int)):
            text = str(value).strip()
            out[key] = "\u2022\u2022\u2022\u2022" + text[-4:]
        else:
            out[key] = value
    return out


def diff_hint(rows, session, twin, canonical, fingerprint, server, ticket_hint_limit=6):
    """Why no review matched: the review came from another MCP server, or the names of the fields
    that differ from the most recent review of this kind on this server in this session."""
    candidates = [entry for _, _, entry in rows
                  if entry and entry.get("session") == session and entry.get("tool") == twin]
    if not candidates:
        return "there is no %s in this session to compare with" % twin
    elsewhere = [e for e in candidates if e.get("server") != server]
    if any(e.get("fingerprint") == fingerprint for e in elsewhere):
        return ("your %s of this exact order was answered by a different MCP server than this place call; a review "
                "binds only a place call on the server that answered it" % twin)
    here = [e for e in candidates if e.get("server") == server]
    if not here:
        return "your latest %s was answered by a different MCP server than this place call" % twin
    latest = here[-1]
    theirs = latest.get("canonical")
    ticket = clean(latest.get("ticket_id"), ticket_hint_limit) or "unknown"
    if not isinstance(theirs, dict):
        return "your latest %s (ticket %s) cannot be compared field by field" % (twin, ticket)
    mine = masked_canonical(canonical)
    fields = [k for k in sorted(set(mine) | set(theirs)) if mine.get(k) != theirs.get(k)]
    if not fields:
        return "your latest %s (ticket %s) differs only in values the ledger stores hashed" % (twin, ticket)
    return "fields that differ from your latest %s (ticket %s): %s" % (twin, ticket, ", ".join(clean(f, 30) for f in fields))


def find_review(rows, session, server, twin, fingerprint, ref_id, ttl, now, family):
    """Step 5. Returns (row_index, raw_line, entry) of the review this call is bound to, or None.
    Only a review answered by the same MCP server counts (entry "server", the salted 8-hex hash
    audit_log.py writes); an entry without one, from an older build, never matches."""
    same = []
    for index, raw, entry in rows:
        if not entry or entry.get("session") != session or entry.get("tool") != twin:
            continue
        if server is None or entry.get("server") != server:
            continue
        if entry.get("fingerprint") != fingerprint:
            continue
        ts = parse_ts(entry.get("ts"))
        age = (now - ts).total_seconds() if ts else None
        same.append((index, raw, entry, age))
    fresh = [(i, r, e, a) for i, r, e, a in same
             if e.get("review_ok") is True and a is not None and -FUTURE_SKEW_SECONDS <= a <= ttl]
    own = [(i, r, e) for i, r, e, _ in fresh if e.get("consumed_by") == ref_id]
    if own:
        return own[-1]
    open_ = [(i, r, e) for i, r, e, _ in fresh if e.get("consumed_by") is None]
    if open_:
        return open_[-1]
    getter = ORDER_GETTER[family]
    if any(e.get("review_ok") is True and e.get("consumed_by") not in (None, ref_id) for _, _, e, _ in same):
        raise Deny("REVIEW_ALREADY_CONSUMED", "this reviewed order was already sent under another ref_id and may "
                                              "already be live; check %s, then review again for a new order" % getter)
    if any(e.get("review_ok") is True for _, _, e, _ in same):
        raise Deny("NO_MATCHING_REVIEW", "your review of this exact order is older than %d s; review it again, show "
                                         "the ticket and get a fresh approval" % ttl)
    if same:
        raise Deny("NO_MATCHING_REVIEW", "the review of this exact order returned validation errors; fix the order "
                                         "and review it again")
    return None


# --------------------------------------------------------------------------- notional and restatement

def _quote_price(entry, side):
    quote = entry.get("quote") if isinstance(entry.get("quote"), dict) else {}
    key = "ask" if side == "buy" else "bid"
    return dec(quote.get(key)), key


def notional(family, canonical, entry):
    """Step 7: (Decimal or None, basis text for the ask, or the reason it cannot be computed)."""
    p = canonical
    side = lower(p.get("side"))
    otype = lower(p.get("type"))
    qty = dec(p.get("quantity"))
    dollars = dec(p.get("dollar_amount"))
    if family in ("equity", "crypto"):
        if dollars is not None and qty is None:
            return dollars, ""
        if qty is None or dollars is not None:
            return None, "the order needs exactly one of quantity or dollar_amount"
        limit, stop = dec(p.get("limit_price")), dec(p.get("stop_price"))
        if otype in ("limit", "stop_limit"):
            return (qty * limit, "") if limit is not None else (None, "the limit price is missing")
        if family == "equity" and otype == "market":
            px, key = _quote_price(entry, side)
            if px is None:
                return None, "the review recorded no %s price for this market order" % key
            return qty * px, "at the review %s %s" % (key, plain(px))
        if family == "equity" and otype == "stop_market":
            if stop is None:
                return None, "the stop price is missing"
            px, key = _quote_price(entry, side)
            basis = max(stop, px) if px is not None else stop
            return qty * basis, "at the stop or review %s, whichever is higher; a gap can fill beyond it" % key
        if family == "crypto":
            est = dec(entry.get("estimated_cost"))
            if est is not None:
                return est, "the review estimate"
            if otype == "stop_loss" and stop is not None:
                return qty * stop, "at the stop price"
            return None, "the review recorded no estimate for this crypto order"
        return None, "order type %s has no notional rule" % clean(otype, 20)
    if family == "oco":
        tp, sl = dec(p.get("take_profit_limit_price")), dec(p.get("stop_loss_stop_price"))
        if qty is None or tp is None or sl is None:
            return None, "quantity, take-profit and stop are all needed"
        return max(tp, sl) * qty, "if the larger leg fills"
    if family == "option":
        raw_qty = p.get("quantity")
        if not (isinstance(raw_qty, str) and re.match(r"^[1-9][0-9]*$", raw_qty.strip())):
            return None, "the contract quantity is not a whole number"
        contracts = Decimal(int(raw_qty))
        legs = p.get("legs") if isinstance(p.get("legs"), list) else []
        problem, label = option_exposure(p, legs)
        if problem:
            return None, problem
        price, stop = dec(p.get("price")), dec(p.get("stop_price"))
        if len(legs) >= 2:
            return (price * HUNDRED * contracts, label) if price is not None else (None, "the net price is missing")
        if otype in ("limit", "stop_limit") and price is not None:
            return price * HUNDRED * contracts, label
        if otype == "stop_market" and stop is not None:
            return stop * HUNDRED * contracts, label + " at the stop price; a gap can fill beyond it"
        return None, "an option %s order has no price the gate can size" % clean(otype or "market", 20)
    return None, "unknown order family"


def option_exposure(canonical, legs):
    """(problem, label) for an option order. The gate sizes an option order by its premium, which
    bounds what the order commits only when no leg opens a short position that a long leg does not
    cover. A short put or call commits the strike (collateral, assignment), not the premium, and the
    gate never sees strikes: the call carries option_ids only. So problem is set, and the order is
    refused as NOTIONAL_UNKNOWN, for a single leg that sells to open, and for a multi-leg order that
    opens a short leg on a credit or undeclared direction, opens more short contracts than long, or
    also closes a position (a roll). What is left is sized at its premium: debit spreads and
    calendars, long options and closing orders. The gate cannot compare expirations, so a debit
    diagonal whose short leg outlives its long leg is still sized at its premium (docs/confirm-mode.md).
    label names the figure for the ask ("premium paid", "net premium received" ...)."""
    if not legs:
        return "the order has no legs the gate can read", ""
    sell_open = buy_open = 0
    closes = False
    for leg in legs:
        if not isinstance(leg, dict):
            return "a leg is not readable", ""
        side, effect = lower(leg.get("side")), lower(leg.get("position_effect"))
        ratio = leg.get("ratio_quantity", 1)
        if side not in ("buy", "sell") or effect not in ("open", "close"):
            return "a leg's side or position_effect is not readable", ""
        if isinstance(ratio, bool) or not isinstance(ratio, int) or ratio < 1:
            return "a leg's ratio_quantity is not a whole number", ""
        if effect == "close":
            closes = True
        elif side == "sell":
            sell_open += ratio
        else:
            buy_open += ratio
    direction = lower(canonical.get("direction"))
    if sell_open:
        why = None
        if len(legs) == 1:
            why = "it sells an option to open; a short option commits its strike, which the gate cannot see, not its premium"
        elif direction != "debit":
            why = "it opens a short option leg and is not a net debit, so its premium does not bound what it commits"
        elif sell_open > buy_open:
            why = "it opens more short option contracts than long ones, so its premium does not bound what it commits"
        elif closes:
            why = "it closes a position and opens a short option leg (a roll), so its premium does not bound the risk"
        if why:
            return why, ""
    if len(legs) == 1:
        return None, "premium paid" if lower(legs[0].get("side")) == "buy" else "premium received"
    if direction == "debit":
        return None, "net premium paid"
    if direction == "credit":
        return None, "net premium received"
    return None, "net premium"


def short_id(value):
    """An option_id as the ask shows it: whole when short, else its first 8 characters."""
    text = clean(value or "?", 64)
    return text if len(text) <= 12 else text[:8]


def _acct(canonical, key):
    value = canonical.get(key)
    return "****" + clean(str(value).strip()[-4:], 4) if value else "****"


def restate_order(bare, family, c, legs_detail=True):
    """The order in plain words, from the call's own parameters (never from the ledger)."""
    side = clean(lower(c.get("side")) or "?", 8).upper()
    otype = clean(lower(c.get("type")) or "", 20)
    symbol = clean(c.get("symbol") or "?", 20)
    parts = []
    if family in ("equity", "crypto"):
        qty, dollars = dec(c.get("quantity")), dec(c.get("dollar_amount"))
        size = "$%s of %s" % (money(dollars), symbol) if dollars is not None else "%s %s" % (
            plain(qty) if qty is not None else "?", symbol)
        parts.append("%s %s" % (side, size))
        limit, stop = dec(c.get("limit_price")), dec(c.get("stop_price"))
        if otype == "limit":
            parts.append("LIMIT %s" % (plain(limit) if limit is not None else "?"))
        elif otype == "market":
            parts.append("MARKET")
        elif otype in ("stop_market", "stop_loss"):
            parts.append("STOP %s (becomes a market order)" % (plain(stop) if stop is not None else "?"))
        elif otype == "stop_limit":
            parts.append("STOP %s LIMIT %s" % (plain(stop) if stop is not None else "?",
                                               plain(limit) if limit is not None else "?"))
        else:
            parts.append(otype.upper() or "TYPE ?")
        parts.append(clean(c.get("time_in_force") or "tif default", 20))
        if family == "equity":
            parts.append(clean(c.get("market_hours") or "regular_hours", 30))
            lots = c.get("tax_lots")
            if isinstance(lots, list) and lots:
                parts.append("%d tax lot%s as reviewed" % (len(lots), "" if len(lots) == 1 else "s"))
            parts.append("account %s" % _acct(c, "account_number"))
        else:
            parts.append("crypto account %s" % _acct(c, "rhs_account_number"))
    elif family == "oco":
        qty = dec(c.get("quantity"))
        tp, sl = dec(c.get("take_profit_limit_price")), dec(c.get("stop_loss_stop_price"))
        parts.append("OCO %s %s %s" % (side, plain(qty) if qty is not None else "?", symbol))
        parts.append("take-profit %s / stop %s" % (plain(tp) if tp is not None else "?",
                                                   plain(sl) if sl is not None else "?"))
        parts.append(clean(c.get("time_in_force") or "tif default", 20))
        parts.append(clean(c.get("market_hours") or "regular_hours", 30))
        parts.append("account %s" % _acct(c, "account_number"))
    else:
        legs = c.get("legs") if isinstance(c.get("legs"), list) else []
        price, stop = dec(c.get("price")), dec(c.get("stop_price"))
        head = "OPTION x%s" % clean(c.get("quantity") or "?", 8)
        if otype in ("limit", "stop_limit"):
            head += " - %s %s" % (otype.upper().replace("_", " "), plain(price) if price is not None else "?")
        else:
            head += " - %s" % (otype.upper().replace("_", " ") or "LIMIT")
        if stop is not None:
            head += " stop %s" % plain(stop)
        if c.get("direction"):
            head += " %s" % clean(c.get("direction"), 10)
        parts.append(head)
        leg_text = []
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            ratio = leg.get("ratio_quantity", 1)
            leg_text.append("%s %s %s%s" % (clean(leg.get("side") or "?", 6).upper(),
                                            clean(leg.get("position_effect") or "?", 6).upper(),
                                            short_id(leg.get("option_id")),
                                            "" if ratio in (1, "1") else " ratio %s" % clean(ratio, 4)))
        if legs_detail:
            parts.append("%d leg%s: %s" % (len(legs), "" if len(legs) == 1 else "s", ", ".join(leg_text)))
        else:
            parts.append("%d leg%s as reviewed" % (len(legs), "" if len(legs) == 1 else "s"))
        parts.append(clean(c.get("time_in_force") or "gfd", 20))
        parts.append(clean(c.get("market_hours") or "regular_hours", 30))
        parts.append("account %s" % _acct(c, "account_number"))
    return "LIVE ORDER - %s: %s" % (bare, " - ".join(parts))


def build_reason(bare, family, canonical, amount, basis, cap, entry, ticket_id, ref_id, retry_status):
    when = parse_ts(entry.get("ts"))
    at = to_eastern(when).strftime("%H:%M:%S ET") if when else "an earlier time"
    est = "est %s USD%s (cap %s)" % (money(amount), (" " + basis) if basis else "", money(cap))
    tail = ["Matches your review at %s (ticket %s)." % (at, clean(ticket_id, 6))]
    alerts = entry.get("alerts_count")
    if isinstance(alerts, int) and not isinstance(alerts, bool) and alerts > 0:
        tail.append("That review returned %d alert%s; read %s on the ticket." % (
            alerts, "" if alerts == 1 else "s", "it" if alerts == 1 else "them"))
    if retry_status:
        tail.append("This is a retry of ref %s (last status %s): confirm with %s that no order exists before "
                    "approving." % (clean(ref_id, 8), clean(retry_status, 10), ORDER_GETTER[family]))
    tail.append("Approve only if this is exactly what you intend.")
    head = restate_order(bare, family, canonical)
    reason = "%s - %s. %s" % (head, est, " ".join(tail))
    if len(reason) > REASON_MAX and family == "option":
        head = restate_order(bare, family, canonical, legs_detail=False)
        reason = "%s - %s. %s" % (head, est, " ".join(tail))
    if len(reason) > REASON_MAX:
        tail = [t for t in tail if not t.startswith("That review returned")]
        reason = "%s - %s. %s" % (head, est, " ".join(tail))
    return reason


def ask_line(reason):
    line = ('{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask",'
            '"permissionDecisionReason":"%s"}}' % reason)
    if not ASK_RE.match(line) or any(ord(ch) < 32 or ord(ch) > 126 for ch in reason):
        raise Deny("GATE_FAILED", "the restatement could not be made safe for the permission prompt")
    json.loads(line)  # must parse
    return line


# --------------------------------------------------------------------------- policy and lint

def check_policy(bare, params, amount, cwd):
    """The user's own [policy] limits that a hook can evaluate. No config file means none are set."""
    kitconfig = load_module("kitconfig", extra_dirs=(os.path.join("skills", "robinhood-trading", "scripts"),))
    if kitconfig is None:
        raise Deny("GATE_FAILED", "kitconfig.py could not be loaded, so your [policy] limits cannot be checked")
    res = kitconfig.run("get", {"section": "policy", "cwd": cwd})
    if not res.get("ok"):
        codes = [e.get("code") for e in res.get("errors") or [] if isinstance(e, dict)]
        if codes == ["CONFIG_NOT_FOUND"]:
            return
        raise Deny("POLICY_DENY", "your kit config could not be read (%s); fix it or move it aside"
                   % clean(",".join(str(c) for c in codes) or "unknown error", 60))
    if res.get("invalid"):
        first = res["invalid"][0]
        field = (first.get("field") or ",".join(first.get("fields") or [])) if isinstance(first, dict) else ""
        raise Deny("POLICY_DENY", "your [policy] has an invalid value (%s); fix the config" % clean(field, 60))
    section = res.get("section") or {}
    policy = dict((k, v) for k, v in section.items() if k in GATE_POLICY_RULES)
    if not policy:
        return
    policy_check = load_module("policy_check")
    if policy_check is None:
        raise Deny("GATE_FAILED", "policy_check.py could not be loaded, so your [policy] limits cannot be checked")
    out = policy_check.check({"policy": policy, "order": {"tool": bare, "params": params},
                              "estimate_usd": money(amount)})
    if not out.get("ok"):
        raise Deny("GATE_FAILED", "policy_check.py could not evaluate this order")
    violations = out.get("violations") or []
    if violations:
        first = violations[0]
        code = "POLICY_CAP" if first.get("code") == "POLICY_CAP" else "POLICY_DENY"
        raise Deny(code, "your [policy] %s: %s" % (clean(first.get("rule"), 40), clean(first.get("msg"), 160)))


def check_lint(bare, params, entry):
    """Step 8: the kit's order rules, with every field's provenance taken as the user's (the human
    approves every field in the prompt)."""
    order_lint = load_module("order_lint")
    if order_lint is None:
        raise Deny("GATE_FAILED", "order_lint.py could not be loaded, so the order rules cannot be checked")
    context = {}
    quote = entry.get("quote") if isinstance(entry.get("quote"), dict) else {}
    for key in ("bid", "ask"):
        if dec(quote.get(key)) is not None:
            context[key] = str(quote[key])
    out = order_lint.lint({"tool": bare, "params": params, "provenance": "user", "context": context})
    if not out.get("ok"):
        raise Deny("LINT_ERROR", "order_lint.py rejected the input: %s" % clean(
            ((out.get("errors") or [{}])[0] or {}).get("code", "unknown"), 40))
    errors = out.get("errors") or []
    if errors:
        first = errors[0]
        raise Deny("LINT_ERROR", "%s on %s: %s" % (clean(first.get("code"), 40), clean(first.get("field"), 40),
                                                   clean(first.get("msg"), 150)))


# --------------------------------------------------------------------------- state writes

def record(state, row_index, raw_line, entry, refids, ref_id, fingerprint, now):
    """Step 9: ref_id pending; the review consumed by this ref_id. Both or the call is denied."""
    record_ = refids.get(ref_id) if isinstance(refids.get(ref_id), dict) else {}
    record_["fingerprint"] = fingerprint
    record_["status"] = "pending"
    record_["ts"] = iso(now)
    refids[ref_id] = record_
    atomic_write(os.path.join(state, "refids.json"),
                 json.dumps(refids, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if entry.get("consumed_by") == ref_id:
        return
    entry["consumed_by"] = ref_id
    new_line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path = os.path.join(state, "ledger.jsonl")
    lines = read_regular(path).split(b"\n")
    if row_index >= len(lines) or lines[row_index] != raw_line:
        raise OSError("ledger.jsonl changed under the lock")
    lines[row_index] = new_line
    body = b"\n".join(lines)
    if not body.endswith(b"\n"):
        body += b"\n"
    atomic_write(path, body)


# --------------------------------------------------------------------------- the decision

def decide(event, env, now=None):
    """Returns the ask line, or raises Deny. Writes state only on the ask path."""
    now = now or utcnow()
    check_event(event)
    server, bare, twin = check_tool(event, load_classes())
    cap, ttl = check_mode(event, env)
    state = state_dir(env)
    check_server(server, state)
    params = event["tool_input"]
    ref_id = check_ref_id(params)

    canon = load_module("canon")
    if canon is None:
        raise Deny("GATE_FAILED", "canon.py could not be loaded, so the order cannot be fingerprinted")
    try:
        fp = canon.fingerprint(bare, params)
    except Exception:
        raise Deny("LINT_ERROR", "the order parameters could not be put in canonical form")
    family, canonical, fingerprint, ticket_id = fp["family"], fp["canonical"], fp["fingerprint"], fp["ticket_id"]

    salt = read_salt(state)
    session = salted(salt, event["session_id"], 12)
    server_tag = salted(salt, server, 8)
    if session is None or not os.path.isfile(os.path.join(state, "ledger.jsonl")):
        raise Deny("NO_MATCHING_REVIEW", "no review has been logged on this machine yet (the audit log writes the "
                                         "review ledger); review the order, show the ticket and get an approval")

    lock = os.path.join(state, ".lock")
    if not acquire_lock(lock):
        raise Deny("GATE_BUSY", "the kit's state is locked by another hook; nothing was placed")
    try:
        refids = read_refids(state)
        retry_status = check_ref_history(refids, ref_id, fingerprint, family)
        rows = read_ledger(state)
        if rows is None:
            raise Deny("NO_MATCHING_REVIEW", "no review has been logged on this machine yet")
        found = find_review(rows, session, server_tag, twin, fingerprint, ref_id, ttl, now, family)
        if found is None:
            raise Deny("NO_MATCHING_REVIEW", "no %s of this exact order on this server in this session in the last "
                                             "%d s; %s. Review the order again and get a fresh approval"
                       % (twin, ttl, diff_hint(rows, session, twin, canonical, fingerprint, server_tag)))
        row_index, raw_line, entry = found
        if entry.get("consumed_by") == ref_id and retry_status is None:
            raise Deny("DUPLICATE_ORDER", "this ref_id already used this review, and its outcome is not on record; "
                                          "check %s before anything else" % ORDER_GETTER[family])
        amount, basis = notional(family, canonical, entry)
        if amount is None or amount <= 0:
            why = basis if amount is None else "it is not above zero"
            raise Deny("NOTIONAL_UNKNOWN", "the order's notional cannot be computed (%s), so it cannot be checked "
                                           "against max_order_notional_usd" % why)
        if amount > cap:
            raise Deny("POLICY_CAP", "est %s USD is over max_order_notional_usd %s" % (money(amount), money(cap)))
        cwd = event.get("cwd") if isinstance(event.get("cwd"), str) and event.get("cwd") else os.getcwd()
        check_policy(bare, params, amount, cwd)
        check_lint(bare, params, entry)
        line = ask_line(build_reason(bare, family, canonical, amount, basis, cap, entry, ticket_id, ref_id,
                                     retry_status))
        try:
            record(state, row_index, raw_line, entry, refids, ref_id, fingerprint, now)
        except OSError:
            raise Deny("GATE_FAILED", "the gate could not record this ref_id, so it will not ask")
        return line
    finally:
        release_lock(lock)


def read_event():
    try:
        raw = sys.stdin.buffer.read()
    except (AttributeError, OSError, ValueError):
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def run(env=None):
    """(exit code, stdout line)."""
    env = dict(os.environ) if env is None else env
    try:
        return 0, decide(read_event(), env)
    except Deny as refusal:
        return DENY_EXIT, deny_line(refusal.code, refusal.text)


def crash_line(exc):
    return deny_line("GATE_FAILED", "internal error (%s); nothing was placed" % type(exc).__name__)


def deadline_seconds(env):
    """DEADLINE_SECONDS, or less when the environment asks for less (tests); never more."""
    try:
        value = float(env.get(DEADLINE_ENV, "") or DEADLINE_SECONDS)
    except (TypeError, ValueError):
        return DEADLINE_SECONDS
    if not value == value:  # NaN
        return DEADLINE_SECONDS
    return max(DEADLINE_MIN, min(DEADLINE_SECONDS, value))


def supervised(work, limit):
    """Runs work() -> (code, line) in a forked child and waits at most `limit` seconds for it.

    The child's stdout is a pipe to this process, not the caller's stdout, and its stderr is
    /dev/null, so a child stuck in the kernel holds nothing guard.sh waits on. This process only
    selects on the pipe and polls waitpid, so it always answers in time: the child's own line and
    exit code, or GATE_FAILED with CRASH_EXIT when the child ran out of time, died or exited with
    a code the contract does not have."""
    if not hasattr(os, "fork"):
        return work()
    try:
        read_fd, write_fd = os.pipe()
    except OSError:
        return CRASH_EXIT, deny_line("GATE_FAILED", "the gate could not start its watchdog; nothing was placed")
    try:
        pid = os.fork()
    except OSError:
        os.close(read_fd)
        os.close(write_fd)
        return CRASH_EXIT, deny_line("GATE_FAILED", "the gate could not start its watchdog; nothing was placed")
    if pid == 0:  # the child: do the work, write one answer to the pipe, never return
        code = CRASH_EXIT
        try:
            os.close(read_fd)
            os.dup2(write_fd, 1)
            os.close(write_fd)
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, 2)
            os.close(devnull)
            try:
                code, line = work()
            except BaseException as exc:
                code, line = CRASH_EXIT, crash_line(exc)
            data = (line + "\n").encode("utf-8", "replace")
            while data:
                data = data[os.write(1, data):]
        except BaseException:
            code = CRASH_EXIT
        finally:
            os._exit(code)
    os.close(write_fd)
    deadline = time.monotonic() + limit
    chunks, size, status = [], 0, None
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select([read_fd], [], [], remaining)
            if not ready:
                continue
            chunk = os.read(read_fd, 65536)
            if not chunk:
                break  # the child closed its end: it is exiting
            if size < OUTPUT_MAX:
                chunks.append(chunk)
                size += len(chunk)
        while time.monotonic() < deadline:
            done, wait_status = os.waitpid(pid, os.WNOHANG)
            if done:
                status = wait_status
                break
            time.sleep(0.005)
    finally:
        os.close(read_fd)
    if status is None:
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, os.WNOHANG)
        except OSError:
            pass
        return CRASH_EXIT, deny_line("GATE_FAILED", "the gate did not finish within %g s (a stalled or tampered "
                                                    "state directory?); nothing was placed" % limit)
    code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else None
    if code not in (0, DENY_EXIT, CRASH_EXIT):
        return CRASH_EXIT, deny_line("GATE_FAILED", "the gate stopped abnormally; nothing was placed")
    text = b"".join(chunks).decode("utf-8", "replace")
    if text.endswith("\n"):
        text = text[:-1]
    if not text:
        return CRASH_EXIT, deny_line("GATE_FAILED", "the gate returned no answer; nothing was placed")
    return code, text


# --------------------------------------------------------------------------- self-test

def selftest():
    """Runs the gate against a temporary state directory; never reads or writes the real one."""
    canon = load_module("canon")
    if canon is None:
        return {"ok": False, "failed": ["canon.py not found next to the gate"]}
    tmp = tempfile.mkdtemp(prefix="rh-confirm-selftest-")
    failures = []
    try:
        state = os.path.join(tmp, "state")
        os.makedirs(state, mode=0o700)
        with open(os.path.join(state, "salt"), "wb") as handle:
            handle.write(b"selftest-salt-16")
        acct = "demo" + "-" + "X4F1"
        review = {"account_number": acct, "symbol": "PLTR", "side": "buy", "type": "limit", "quantity": "10",
                  "limit_price": "31.24", "time_in_force": "gfd", "market_hours": "all_day_hours"}
        fp = canon.fingerprint("review_equity_order", review)
        sess = hashlib.sha256(b"selftest-salt-16" + b"selftest-session").hexdigest()[:12]
        srv = hashlib.sha256(b"selftest-salt-16" + b"robinhood-trading").hexdigest()[:8]
        entry = {"fingerprint": fp["fingerprint"], "session": sess, "ts": iso(utcnow()), "tool": "review_equity_order",
                 "review_ok": True, "alerts_count": 0, "server": srv, "consumed_by": None,
                 "ticket_id": fp["ticket_id"], "canonical": masked_canonical(fp["canonical"])}
        with open(os.path.join(state, "ledger.jsonl"), "wb") as handle:
            handle.write(json.dumps(entry, separators=(",", ":")).encode("utf-8") + b"\n")
        env = {"ROBINHOOD_SKILLS_STATE": state, "CLAUDE_PLUGIN_OPTION_ORDER_MODE": "confirm",
               "CLAUDE_PLUGIN_OPTION_MAX_ORDER_NOTIONAL_USD": "500.00",
               "ROBINHOOD_SKILLS_CONFIG": os.path.join(tmp, "no-config.toml"),
               "XDG_CONFIG_HOME": os.path.join(tmp, "config")}

        first_ref = str(uuid.uuid4())

        def event(tool="place_equity_order", mode="default", server="robinhood-trading", **changes):
            params = dict(review, ref_id=first_ref)
            params.update(changes)
            return {"session_id": "selftest-session", "permission_mode": mode, "hook_event_name": "PreToolUse",
                    "cwd": tmp, "tool_name": "mcp__%s__%s" % (server, tool), "tool_input": params}

        def outcome(ev):
            try:
                return "ask", decide(ev, env)
            except Deny as refusal:
                return refusal.code, refusal.text

        saved = dict((k, os.environ.get(k)) for k in ("ROBINHOOD_SKILLS_CONFIG", "XDG_CONFIG_HOME"))
        os.environ["ROBINHOOD_SKILLS_CONFIG"] = env["ROBINHOOD_SKILLS_CONFIG"]
        os.environ["XDG_CONFIG_HOME"] = env["XDG_CONFIG_HOME"]

        checks = [
            ("changed quantity is refused", event(quantity="12"), "NO_MATCHING_REVIEW"),
            ("bypassPermissions is refused", event(mode="bypassPermissions"), "NOT_INTERACTIVE"),
            ("exercise_option is refused", event(tool="exercise_option"), "NOT_CONFIRMABLE"),
            ("a review from another server does not bind", event(server="robinhood-sandbox"), "NO_MATCHING_REVIEW"),
            ("the reviewed order asks", event(), "ask"),
            ("a new ref_id for a spent review is refused",
             event(ref_id=str(uuid.uuid4())), "REVIEW_ALREADY_CONSUMED"),
        ]
        try:
            for name, ev, expected in checks:
                got, text = outcome(ev)
                if got != expected:
                    failures.append("%s: expected %s, got %s (%s)" % (name, expected, got, text[:120]))
                elif got == "ask" and ("Approve only if" not in text or not ASK_RE.match(text)):
                    failures.append("%s: malformed ask" % name)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"ok": not failures, "checks": len(checks), "failed": failures}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        result = selftest()
        sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
        return 0 if result["ok"] else 1
    if "--help" in argv or "-h" in argv:
        sys.stdout.write(__doc__)
        return 0
    try:
        code, line = supervised(run, deadline_seconds(os.environ))
    except BaseException as exc:  # a crash must never look like an ask; guard.sh blocks on exit 4
        sys.stdout.write(crash_line(exc) + "\n")
        sys.stdout.flush()
        return CRASH_EXIT
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
