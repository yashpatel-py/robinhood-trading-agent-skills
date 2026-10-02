#!/usr/bin/env python3
"""audit_log.py - local, masked, hash-chained log of Robinhood MCP tool calls (Claude Code hooks).

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: "what did my agent actually do?" can only be answered by something outside the
model that wrote it down at the time. hooks/guard.sh pipes every PostToolUse event for an MCP tool
here (and SessionStart with --session-start). This script:

  * logs only Robinhood calls: tools named in hooks/known-tools.txt, plus any tool on a server that
    is Robinhood's (name contains "robinhood", or listed in servers.txt). A few Robinhood tool
    names are also common elsewhere ("search", "get_alerts"...); those count only on a Robinhood
    server, the same rule guard.sh uses when it learns servers.
  * appends one JSON line per call to $STATE/audit/audit-YYYY-MM.jsonl (UTC month). Each line
    carries "prev", the sha256 of the previous line's bytes (the first line ever carries
    "genesis:<random>"), so deleting or editing a line breaks the chain. This is tamper-EVIDENT,
    not tamper-proof: anyone who can write the file can rewrite the whole chain.
  * never stores a raw response. Reads keep a count; reviews keep alert heads, the quote and an
    estimate; writes keep a short id; places keep the order id and state; errors keep 200 masked
    characters. Inputs are masked: account-like keys become ****last4, any token matching a known
    account number (salted hashes learned from get_accounts) is masked wherever it appears, long
    free text becomes {len, sha256}, bearer tokens are dropped.
  * folds $STATE/audit/blocked.jsonl (lines guard.sh writes when it blocks or prompts) into the
    chain, hashing the session id.
  * keeps the review ledger ($STATE/ledger.jsonl) that the report card uses for slippage and the
    confirm gate uses to bind a place_* call to the review the user saw (each entry names the
    server that answered, as a salted hash, and binds only a place call on that server), the ref_id
    ledger ($STATE/refids.json) after a place, and $STATE/last_tool/ (the previous Robinhood tool).
  * opens state files non-blocking and only when they are regular files, so a FIFO or symlink
    planted in the state directory is skipped instead of hanging the hook or being written through.

Salted hashes: session = first 12 hex of sha256(salt + session_id); server = first 8 hex of
sha256(salt + server); salt = 16 random bytes in $STATE/salt (0600). One lock ($STATE/.lock,
O_CREAT|O_EXCL, waited on for up to 2 s, stale after 30 s) guards every state write; the confirm
gate takes the same lock before it touches the ledger. If the lock is busy the line is skipped.

State: ${ROBINHOOD_SKILLS_STATE:-${XDG_STATE_HOME:-~/.local/state}/robinhood-skills}; directories
0700, files 0600. Audit files are pruned oldest-first once they total more than 50 MB (the current
month is kept); audit_verify.py then reports truncated_start. Turned off entirely when the plugin
option audit_log is false.

Usage (from hooks/guard.sh; not meant to be run by hand):
    python3 audit_log.py [--session-start] < hook-event.json
Contract: stdlib only, Python >= 3.9, no network. It never raises and always exits 0, because a
logging failure must never change what the user's tool call does.
"""

import datetime
import errno
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
import time

KIT_VERSION_FALLBACK = "2.0.0"
CLIENT = "claude-code"
MASK = "\u2022\u2022\u2022\u2022"
DROPPED = "[dropped: credential]"
MAX_TOTAL_BYTES = 50 * 1024 * 1024
LOCK_WAIT_SECONDS = 2.0
STALE_LOCK_SECONDS = 30.0
FREE_TEXT_LIMIT = 200
ALERT_HEAD_CHARS = 120
ERROR_HEAD_CHARS = 200
LEDGER_TRIM_BYTES = 5 * 1024 * 1024
LEDGER_KEEP_DAYS = 35
LAST_TOOL_KEEP_DAYS = 7
TAIL_SCAN_BYTES = 4 * 1024 * 1024

# Keep in sync with is_generic_name() in hooks/guard.sh (hooks/tests/test_audit_log.py checks it).
GENERIC_NAMES = frozenset((
    "search", "get_accounts", "get_financials", "get_indexes", "get_scans", "run_scan",
    "create_scan", "preview_scan", "get_alerts", "get_alert_log", "create_alert", "update_alert",
    "delete_alert", "mark_alerts_read", "get_watchlists", "get_watchlist_items", "create_watchlist",
    "update_watchlist", "add_to_watchlist", "remove_from_watchlist", "follow_watchlist",
    "unfollow_watchlist",
))
REVIEW_TOOLS = ("review_equity_order", "review_option_order", "review_advanced_order", "preview_crypto_order")
PLACE_TOOLS = ("place_equity_order", "place_option_order", "place_crypto_order", "place_advanced_order")
ORDER_TOOLS = REVIEW_TOOLS + PLACE_TOOLS
ACCOUNT_VALUE_KEYS = ("account_number", "rhs_account_number", "rhc_account_number")
ID_KEYS = ("order_id", "id", "alert_id", "alert_log_id", "list_id", "watchlist_id", "scan_id", "exercise_id")
ERROR_MARKERS = ("error", "cannot be found", "does not exist", "failed", "invalid", "not allowed",
                 "denied", "rate_limited", "unauthorized", "forbidden")
FAILED_STATES = ("rejected", "failed")
PRE_EVENTS = {"pre_block": "deny", "pre_ask": "ask"}
MODES = ("simulate_only", "confirm")
SESSION_SOURCES = ("startup", "resume", "clear", "compact")

AUDIT_FILE_RE = re.compile(r"^audit-(\d{4})-(\d{2})\.jsonl$")
TOKEN_RE = re.compile(r"[A-Za-z0-9]{6,}")
LONG_ID_RE = re.compile(r"[A-Za-z0-9]{8,}")
BEARER_RE = re.compile(r"\bBearer\s+\S", re.IGNORECASE)
# A JWT starts with the base64 of '{"'; the prefix is split so secret scanners do not flag this line.
JWT_RE = re.compile("ey" + r"J[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}")
NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
CODE_RE = re.compile(r"^[A-Z][A-Z_]{1,39}$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")
SESSION_HASH_RE = re.compile(r"^[0-9a-f]{12}$")
REF_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
MESSAGE_KEY_RE = re.compile(r"message|title|text|description|reason|detail", re.IGNORECASE)


# --------------------------------------------------------------------------- small utilities

def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (dt.microsecond // 1000)


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def plugin_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def state_dir():
    env = os.environ.get("ROBINHOOD_SKILLS_STATE")
    if env:
        return env
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "robinhood-skills")


def audit_disabled():
    return os.environ.get("CLAUDE_PLUGIN_OPTION_AUDIT_LOG", "true").strip().lower() in ("false", "0", "no", "off")


def current_mode():
    return "confirm" if os.environ.get("CLAUDE_PLUGIN_OPTION_ORDER_MODE", "") == "confirm" else "simulate_only"


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


NONBLOCK = getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0)


def _require_regular(fd, path):
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError(errno.EINVAL, "not a regular file", path)
    return fd


def read_regular(path):
    """The bytes of a regular file. FileNotFoundError when absent; another OSError for anything else
    (a FIFO, a device, a directory). The open never blocks, so a FIFO planted in the state directory
    cannot hang this hook (a stuck hook is killed at its timeout, and the session line or audit line
    it was writing is lost)."""
    fd = _require_regular(os.open(path, os.O_RDONLY | NONBLOCK), path)
    try:
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def open_append(path, readable=False):
    """An O_APPEND descriptor for a regular file (created 0600 when absent). A FIFO, a device or a
    symlink is refused (OSError) instead of blocking or writing through to another file."""
    flags = (os.O_RDWR if readable else os.O_WRONLY) | os.O_APPEND | os.O_CREAT | NONBLOCK
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return _require_regular(os.open(path, flags, 0o600), path)


def read_json(path, default):
    try:
        return json.loads(read_regular(path).decode("utf-8"))
    except (OSError, ValueError):
        return default


def load_module(name):
    """hooks/lib/<name>.py (synced copy in a plugin install), else shared/scripts/<name>.py
    (a source checkout). None when neither loads; callers degrade instead of failing."""
    root = plugin_root()
    for folder in (os.path.join(root, "hooks", "lib"), os.path.join(root, "shared", "scripts")):
        path = os.path.join(folder, name + ".py")
        if not os.path.isfile(path):
            continue
        try:
            spec = importlib.util.spec_from_file_location("rh_audit_" + name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except Exception:
            continue
    return None


def kit_version():
    data = read_json(os.path.join(plugin_root(), ".claude-plugin", "plugin.json"), {})
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) and version else KIT_VERSION_FALLBACK


def load_classes():
    root = plugin_root()
    for path in (os.path.join(root, "hooks", "tool-classes.json"), os.path.join(root, "connector", "tool-classes.json")):
        data = read_json(path, None)
        tools = data.get("tools") if isinstance(data, dict) else None
        if isinstance(tools, list):
            return dict((t["name"], t.get("class") or "unknown") for t in tools
                        if isinstance(t, dict) and isinstance(t.get("name"), str))
    return {}


def load_known(classes):
    known = set(classes)
    try:
        with open(os.path.join(plugin_root(), "hooks", "known-tools.txt"), "r", encoding="utf-8") as handle:
            known.update(line.strip() for line in handle if line.strip())
    except OSError:
        pass
    return known


def load_servers(state):
    try:
        text = read_regular(os.path.join(state, "servers.txt")).decode("utf-8", "replace")
    except OSError:
        return set()
    return set(line.strip() for line in text.splitlines() if line.strip())


def learn_server(state, server, servers):
    if not server or server in servers or not NAME_RE.fullmatch(server):
        return
    fd = open_append(os.path.join(state, "servers.txt"))
    try:
        os.write(fd, (server + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    servers.add(server)


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


def is_rh_server(server, servers):
    return bool(server) and ("robinhood" in server.lower() or server in servers)


def policy_sha(cwd):
    """sha256 of the kit config file in effect (lookup order of kitconfig: $ROBINHOOD_SKILLS_CONFIG,
    ./.robinhood/config.{toml,json}, ${XDG_CONFIG_HOME:-~/.config}/robinhood-skills/config.{toml,json}),
    so a report can tell whether the user's limits changed between two calls. None when no file."""
    candidates = []
    env = os.environ.get("ROBINHOOD_SKILLS_CONFIG")
    if env:
        candidates.append(env)
    if isinstance(cwd, str) and cwd:
        candidates += [os.path.join(cwd, ".robinhood", "config.toml"), os.path.join(cwd, ".robinhood", "config.json")]
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    candidates += [os.path.join(config_home, "robinhood-skills", "config.toml"),
                   os.path.join(config_home, "robinhood-skills", "config.json")]
    for path in candidates:
        if os.path.isfile(path):
            try:
                return "sha256:" + sha256_hex(read_regular(path))
            except OSError:
                return None
    return None


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


# --------------------------------------------------------------------------- masking

def mask_value(value):
    text = str(value).strip()
    return MASK + text[-4:] if text else text


def is_account_key(key):
    if not isinstance(key, str):
        return False
    low = key.lower()
    return "account" in low and not low.endswith("_type")


class Masker(object):
    def __init__(self, salt, known_hashes):
        self.salt = salt
        self.known = known_hashes

    def salted(self, text):
        return sha256_hex(self.salt + text.encode("utf-8"))

    def is_known_account(self, token):
        return bool(self.known) and self.salted(token) in self.known

    def text(self, value, heuristic=False):
        if BEARER_RE.search(value) or JWT_RE.search(value):
            return DROPPED
        out = TOKEN_RE.sub(lambda m: mask_value(m.group(0)) if self.is_known_account(m.group(0)) else m.group(0), value)
        if heuristic:
            # Error and alert text can quote an account number before get_accounts taught us it.
            out = LONG_ID_RE.sub(lambda m: mask_value(m.group(0)) if any(c.isdigit() for c in m.group(0)) else m.group(0), out)
        return out

    def head(self, value, limit):
        text = " ".join(self.text(value, heuristic=True).split())
        return text[:limit]

    def value(self, value, key=None):
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, dict):
            return dict((k, self.value(value[k], k)) for k in sorted(value, key=str))
        if isinstance(value, list):
            return [self.value(item, key) for item in value]
        if is_account_key(key) and isinstance(value, (str, int, float)):
            return mask_value(value)
        if isinstance(value, (int, float)):
            digits = str(value)
            if len(digits) >= 6 and digits.isdigit() and self.is_known_account(digits):
                return mask_value(digits)
            return value
        if isinstance(value, str):
            text = self.text(value)
            if len(text) > FREE_TEXT_LIMIT:
                return {"len": len(text), "sha256": sha256_hex(text.encode("utf-8"))}
            return text
        return self.value(str(value), key)


# --------------------------------------------------------------------------- responses

def _content_text(blocks):
    parts = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


def parse_response(raw):
    """Claude Code's tool_response for an MCP tool -> (ok, data, text).

    The connector wraps every payload as {"data": ..., "guide": ...} (connector/FIELDS.md); the
    hook may see it as content blocks, as text or as an object, so each shape is accepted. data is
    the envelope's data; text is the raw text when the response was not JSON."""
    is_error = False
    obj = raw
    if isinstance(obj, dict):
        if obj.get("isError") is True or obj.get("is_error") is True:
            is_error = True
        if isinstance(obj.get("structuredContent"), dict):
            obj = obj["structuredContent"]
        elif isinstance(obj.get("content"), list):
            obj = _content_text(obj["content"])
    elif isinstance(obj, list) and obj and all(isinstance(b, dict) and "type" in b for b in obj):
        obj = _content_text(obj)
    text = None
    if isinstance(obj, str):
        text = obj
        stripped = obj.strip()
        obj = None
        if stripped[:1] in ("{", "["):
            try:
                obj = json.loads(stripped)
            except ValueError:
                obj = None
    data = obj
    if isinstance(obj, dict):
        if "data" in obj and ("guide" in obj or len(obj) == 1):
            data = obj["data"]
        elif obj.get("error") and "data" not in obj:
            is_error = True
    if obj is None and text is not None:
        low = text.lower()
        if any(marker in low for marker in ERROR_MARKERS):
            is_error = True
    return (not is_error), data, text


def error_text(data, text):
    if isinstance(text, str) and text.strip():
        return text
    if isinstance(data, dict):
        for key in ("error", "message", "detail"):
            value = data.get(key)
            if isinstance(value, str):
                return value
            if value is not None:
                return json.dumps(value, sort_keys=True)
    return "" if data is None else json.dumps(data, sort_keys=True, default=str)[:400]


def count_items(data):
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        lengths = [len(v) for v in data.values() if isinstance(v, list)]
        if lengths:
            return max(lengths)
        return 1 if data else 0
    return 0 if data is None else 1


def find_message(obj, depth=0):
    if depth > 2:
        return None
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, str) and value.strip() and MESSAGE_KEY_RE.search(str(key)):
                return value
        for value in obj.values():
            found = find_message(value, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj[:5]:
            found = find_message(value, depth + 1)
            if found:
                return found
    return None


def alert_head(item, masker):
    if isinstance(item, str):
        text = item
    elif isinstance(item, dict):
        parts = []
        for key in ("alertType", "alert_type", "type", "code"):
            if isinstance(item.get(key), str) and item[key].strip():
                parts.append(item[key].strip())
                break
        message = find_message(item)
        if message:
            parts.append(message)
        text = ": ".join(parts) if parts else "alert"
    else:
        text = str(item)
    return masker.head(text, ALERT_HEAD_CHARS)


def extract_alerts(data, masker):
    """-> (count, heads, invalid). review_equity_order's order_checks is an OBJECT ({} when clear,
    else {alertType, <x>Details}) per connector/FIELDS.md; a list form is accepted too. Validation
    errors (preview_crypto_order documents them; field name unverified) mark the review invalid."""
    if not isinstance(data, dict):
        return 0, [], False
    items = []
    invalid = False
    checks = data.get("order_checks")
    if isinstance(checks, dict) and checks:
        items.append(checks)
    elif isinstance(checks, list):
        items.extend(checks)
    alerts = data.get("alerts")
    if isinstance(alerts, list):
        items.extend(alerts)
    for key in ("validation_errors", "errors"):
        value = data.get(key)
        if isinstance(value, list) and value:
            items.extend(value)
            invalid = True
        elif isinstance(value, (dict, str)) and value:
            items.append(value)
            invalid = True
    return len(items), [alert_head(item, masker) for item in items], invalid


def extract_quote(data):
    if not isinstance(data, dict):
        return None
    candidates = [data.get("quote_data"), data.get("quote"), data]
    for quote in candidates:
        if not isinstance(quote, dict):
            continue
        if isinstance(quote.get("quote"), dict):
            quote = quote["quote"]
        bid, ask = quote.get("bid_price"), quote.get("ask_price")
        if bid is None and ask is None:
            continue
        ts = None
        for key in ("updated_at", "venue_ask_time", "venue_bid_time", "venue_last_trade_time"):
            if isinstance(quote.get(key), str) and quote[key]:
                ts = quote[key]
                break
        return {"bid": None if bid is None else str(bid), "ask": None if ask is None else str(ask), "ts": ts}
    return None


def kit_estimate(order_lint, bare, params, quote):
    """The kit's labeled estimate (order_lint.py); the equity and OCO reviews return no cost."""
    if order_lint is None or not isinstance(params, dict):
        return None
    context = {}
    if quote:
        if quote.get("bid") is not None:
            context["bid"] = quote["bid"]
        if quote.get("ask") is not None:
            context["ask"] = quote["ask"]
    try:
        result = order_lint.lint({"tool": bare, "params": params, "provenance": "user", "context": context})
    except Exception:
        return None
    estimate = result.get("estimate") if isinstance(result, dict) else None
    usd = estimate.get("usd") if isinstance(estimate, dict) else None
    return usd if isinstance(usd, str) and usd else None


def id_short(data):
    for obj in (data, ) + tuple(v for v in (data.values() if isinstance(data, dict) else ()) if isinstance(v, dict)):
        if not isinstance(obj, dict):
            continue
        for key in ID_KEYS:
            value = obj.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
                return str(value)[:8]
    return None


def order_fields(data):
    """(order_id, state) from a place_* response. Field names are not captured (no order was ever
    placed during the capture); id / order_id and state follow get_equity_orders' rows."""
    for obj in (data, data.get("order") if isinstance(data, dict) else None):
        if isinstance(obj, dict):
            order_id = obj.get("order_id") or obj.get("id")
            state = obj.get("state")
            if order_id or state:
                return (str(order_id) if order_id else None), (str(state) if state else None)
    return None, None


def looks_like_rh_accounts(data):
    accounts = data.get("accounts") if isinstance(data, dict) else None
    return isinstance(accounts, list) and any(
        isinstance(a, dict) and ("agentic_allowed" in a or "rhs_account_number" in a) for a in accounts)


# --------------------------------------------------------------------------- the chain

def audit_files(audit_dir):
    try:
        return sorted(name for name in os.listdir(audit_dir) if AUDIT_FILE_RE.match(name))
    except OSError:
        return []


def last_line(path, max_scan=TAIL_SCAN_BYTES):
    """The last non-empty line of a file, read backwards. Lines are small (long text is hashed),
    so the scan stops after max_scan bytes; a file whose tail has no newline in that window (a
    corrupt file) chains to the bytes that were read, and audit_verify flags the break."""
    fd = _require_regular(os.open(path, os.O_RDONLY | NONBLOCK), path)
    with os.fdopen(fd, "rb") as handle:
        handle.seek(0, 2)
        pos = handle.tell()
        chunks = []
        scanned = 0
        while pos > 0 and scanned < max_scan:
            step = min(65536, pos)
            pos -= step
            handle.seek(pos)
            chunks.insert(0, handle.read(step))
            scanned += step
            body = b"".join(chunks).rstrip(b"\n")
            cut = body.rfind(b"\n")
            if cut != -1:
                return body[cut + 1:]
        body = b"".join(chunks).rstrip(b"\n")
        return body or None


class Chain(object):
    def __init__(self, audit_dir):
        self.dir = audit_dir
        self.prev, self.seq = self._tail()

    def _tail(self):
        for name in reversed(audit_files(self.dir)):
            try:
                line = last_line(os.path.join(self.dir, name))
            except OSError:
                continue
            if line:
                seq = 0
                try:
                    parsed = json.loads(line.decode("utf-8"))
                    if isinstance(parsed, dict) and isinstance(parsed.get("seq"), int):
                        seq = parsed["seq"]
                except ValueError:
                    pass
                return "sha256:" + sha256_hex(line), seq
        return "genesis:" + os.urandom(8).hex(), 0

    def current_name(self):
        now = utcnow()
        return "audit-%04d-%02d.jsonl" % (now.year, now.month)

    def append(self, ts, fields):
        """fields: every line key after "prev", already in schema order."""
        line = {"v": 1, "seq": self.seq + 1, "ts": ts or iso(utcnow()), "prev": self.prev}
        line.update(fields)
        data = json.dumps(line, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        path = os.path.join(self.dir, self.current_name())
        fd = open_append(path, readable=True)
        try:
            size = os.fstat(fd).st_size
            prefix = b""
            if size and os.pread(fd, 1, size - 1) != b"\n":
                prefix = b"\n"  # an interrupted write left a partial line; start clean
            payload = prefix + data + b"\n"
            written = 0
            while written < len(payload):
                written += os.write(fd, payload[written:])
        finally:
            os.close(fd)
        self.prev = "sha256:" + sha256_hex(data)
        self.seq += 1


def enforce_retention(audit_dir, keep_name):
    names = audit_files(audit_dir)
    sizes = {}
    for name in names:
        try:
            sizes[name] = os.path.getsize(os.path.join(audit_dir, name))
        except OSError:
            sizes[name] = 0
    total = sum(sizes.values())
    for name in names:
        if total <= MAX_TOTAL_BYTES or name == keep_name:
            break
        try:
            os.remove(os.path.join(audit_dir, name))
        except OSError:
            break
        total -= sizes[name]


# --------------------------------------------------------------------------- state files

def load_salt(state):
    path = os.path.join(state, "salt")
    try:
        salt = read_regular(path)
        if len(salt) >= 16:
            return salt[:16]
    except OSError:
        pass
    salt = os.urandom(16)
    atomic_write(path, salt)
    return salt


def load_known_accounts(state):
    data = read_json(os.path.join(state, "known_accounts.json"), {})
    hashes = data.get("hashes") if isinstance(data, dict) else None
    return set(h for h in hashes if isinstance(h, str)) if isinstance(hashes, list) else set()


def learn_accounts(state, masker, data):
    values = set()
    for account in data.get("accounts") or []:
        if not isinstance(account, dict):
            continue
        for key in ACCOUNT_VALUE_KEYS:
            value = account.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
                values.add(str(value).strip())
    new = set(masker.salted(v) for v in values)
    if new - masker.known:
        masker.known = masker.known | new
        body = json.dumps({"v": 1, "hashes": sorted(masker.known)}, separators=(",", ":")).encode("utf-8")
        atomic_write(os.path.join(state, "known_accounts.json"), body)


def append_ledger(state, entry):
    path = os.path.join(state, "ledger.jsonl")
    try:
        if os.path.getsize(path) > LEDGER_TRIM_BYTES:
            cutoff = utcnow() - datetime.timedelta(days=LEDGER_KEEP_DAYS)
            keep = []
            for raw in read_regular(path).splitlines(True):
                try:
                    ts = json.loads(raw.decode("utf-8")).get("ts", "")
                    when = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
                except (ValueError, AttributeError, TypeError):
                    continue
                if when >= cutoff:
                    keep.append(raw if raw.endswith(b"\n") else raw + b"\n")
            atomic_write(path, b"".join(keep))
    except OSError:
        pass
    line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    fd = open_append(path)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def update_refids(state, ref_id, fingerprint, status):
    path = os.path.join(state, "refids.json")
    data = read_json(path, {})
    if not isinstance(data, dict):
        data = {}
    entry = data.get(ref_id) if isinstance(data.get(ref_id), dict) else {}
    entry["fingerprint"] = entry.get("fingerprint") or fingerprint
    entry["status"] = status
    entry["ts"] = iso(utcnow())
    data[ref_id] = entry
    atomic_write(path, json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def read_last_tool(state, session_hash):
    if not session_hash:
        return None
    try:
        name = read_regular(os.path.join(state, "last_tool", session_hash)).decode("utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return name if NAME_RE.match(name) else None


def write_last_tool(state, session_hash, bare):
    folder = os.path.join(state, "last_tool")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    body = (bare + "\n").encode("utf-8")
    atomic_write(os.path.join(folder, "latest"), body)
    if session_hash:
        atomic_write(os.path.join(folder, session_hash), body)


def prune_last_tool(state):
    folder = os.path.join(state, "last_tool")
    cutoff = time.time() - LAST_TOOL_KEEP_DAYS * 86400
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        if name == "latest" or not SESSION_HASH_RE.match(name):
            continue
        path = os.path.join(folder, name)
        try:
            if os.stat(path).st_mtime < cutoff:
                os.remove(path)
        except OSError:
            pass


# --------------------------------------------------------------------------- building lines

class Context(object):
    def __init__(self, state, event, classes):
        self.state = state
        self.event = event
        self.classes = classes
        self.salt = load_salt(state)
        self.masker = Masker(self.salt, load_known_accounts(state))
        self.version = kit_version()
        self.mode = current_mode()

    def session_hash(self, session_id):
        if not isinstance(session_id, str) or not session_id:
            return None
        return sha256_hex(self.salt + session_id.encode("utf-8"))[:12]

    def server_hash(self, server):
        if not isinstance(server, str) or not server:
            return None
        return sha256_hex(self.salt + server.encode("utf-8"))[:8]

    def fields(self, session, event, tool, server, cls, decision, reason_code, mode, fingerprint=None,
               ticket_id=None, ref_id=None, inputs=None, result=None, after_tool=None, policy=None):
        return {
            "session": session, "client": CLIENT, "event": event, "tool": tool, "server": server,
            "class": cls, "decision": decision, "reason_code": reason_code, "mode": mode,
            "fingerprint": fingerprint, "ticket_id": ticket_id, "ref_id": ref_id,
            "inputs": inputs if inputs is not None else {}, "result": result, "after_tool": after_tool,
            "kit_version": self.version, "policy_sha": policy,
        }


def parse_guard_line(raw):
    try:
        rec = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None
    if not isinstance(rec, dict) or rec.get("v") != 1 or rec.get("event") not in PRE_EVENTS:
        return None

    def name(key):
        value = rec.get(key)
        return value if isinstance(value, str) and NAME_RE.match(value) else None

    code = rec.get("reason_code")
    ts = rec.get("ts")
    session_id = rec.get("session_id")
    return {
        "event": rec["event"],
        "ts": ts if isinstance(ts, str) and TS_RE.match(ts) else None,
        "tool": name("tool"),
        "server": name("server"),
        "reason_code": code if isinstance(code, str) and CODE_RE.match(code) else None,
        "session_id": session_id if isinstance(session_id, str) and 0 < len(session_id) <= 200 else None,
        "after_tool": name("after_tool"),
        "mode": rec.get("mode") if rec.get("mode") in MODES else None,
    }


def fold_blocked(ctx, chain):
    audit_dir = os.path.join(ctx.state, "audit")
    source = os.path.join(audit_dir, "blocked.jsonl")
    try:
        batches = [n for n in os.listdir(audit_dir) if n.startswith("blocked.jsonl.folding-")]
    except OSError:
        batches = []
    if os.path.exists(source):
        target = "%s.folding-%d-%s" % (source, os.getpid(), os.urandom(3).hex())
        try:
            os.rename(source, target)  # the guard keeps appending to a fresh blocked.jsonl
            batches.append(os.path.basename(target))
        except OSError:
            pass
    for batch in sorted(batches):
        path = os.path.join(audit_dir, batch)
        try:
            raw_lines = read_regular(path).splitlines()
        except OSError:
            try:
                if not os.path.isfile(path):
                    os.remove(path)  # a FIFO or other non-file would otherwise be retried forever
            except OSError:
                pass
            continue
        for raw in raw_lines:
            rec = parse_guard_line(raw)
            if rec is None:
                continue
            tool = rec["tool"]
            chain.append(rec["ts"], ctx.fields(
                session=ctx.session_hash(rec["session_id"]), event=rec["event"], tool=tool,
                server=ctx.server_hash(rec["server"]),
                cls=(ctx.classes.get(tool, "unknown") if tool else None),
                decision=PRE_EVENTS[rec["event"]], reason_code=rec["reason_code"],
                mode=rec["mode"] or ctx.mode, after_tool=rec["after_tool"]))
        try:
            os.remove(path)
        except OSError:
            pass


def pending_blocked(audit_dir):
    try:
        return any(n == "blocked.jsonl" or n.startswith("blocked.jsonl.folding-") for n in os.listdir(audit_dir))
    except OSError:
        return False


def summarize(ctx, cls, bare, ok, data, text, params, order_lint):
    """-> (result, ledger_extras). ledger_extras is set for order reviews only."""
    if not ok:
        return {"ok": False, "error_head": ctx.masker.head(error_text(data, text), ERROR_HEAD_CHARS)}, None
    if bare in REVIEW_TOOLS:
        count, heads, invalid = extract_alerts(data, ctx.masker)
        result = {"ok": True, "alerts_count": count, "alerts_head": heads}
        quote = extract_quote(data)
        if quote:
            result["quote"] = quote
        estimate = kit_estimate(order_lint, bare, params, quote)
        if estimate:
            result["estimated_cost"] = estimate
        return result, {"review_ok": not invalid, "alerts_count": count, "quote": quote, "estimated_cost": estimate}
    if cls == "read" or bare == "preview_scan":
        return {"ok": True, "count": count_items(data)}, None
    if cls in ("write_confirm", "cancel"):
        return {"ok": True, "id_short": id_short(data)}, None
    if cls == "money":
        order_id, state = order_fields(data)
        return {"ok": True, "order_id": order_id, "state": state}, None
    return {"ok": True}, None


def process_post(ctx, chain, event, server, bare, known, servers, response):
    ok, data, text = response
    state = ctx.state
    if bare == "get_accounts" and ok and looks_like_rh_accounts(data):
        learn_accounts(state, ctx.masker, data)  # before masking anything in this call
        if server and "robinhood" not in server.lower():
            learn_server(state, server, servers)
    cls = ctx.classes.get(bare, "unknown") if bare in known else "unknown"
    params = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    session = ctx.session_hash(event.get("session_id"))

    canon = load_module("canon") if bare in ORDER_TOOLS else None
    fingerprint = ticket_id = canonical = None
    if canon is not None:
        try:
            fp = canon.fingerprint(bare, params)
            fingerprint, ticket_id, canonical = fp["fingerprint"], fp["ticket_id"], fp["canonical"]
        except Exception:
            fingerprint = ticket_id = canonical = None
    order_lint = load_module("order_lint") if bare in REVIEW_TOOLS else None
    result, review = summarize(ctx, cls, bare, ok, data, text, params, order_lint)
    # Recorded exactly as sent (fullmatch: a trailing newline or space is not stripped into a key
    # the confirm gate would then treat as the same order); the gate refuses such ref_ids anyway.
    ref_id = params.get("ref_id") if isinstance(params.get("ref_id"), str) and REF_ID_RE.fullmatch(params["ref_id"]) else None
    raw_inputs = canonical if canonical is not None else dict((k, v) for k, v in params.items() if k != "ref_id")
    inputs = ctx.masker.value(raw_inputs)
    now = iso(utcnow())

    if bare in REVIEW_TOOLS and fingerprint and ok and review:
        # Only a review the broker actually answered goes in the ledger. review_ok is false when
        # the answer carried validation errors; alerts alone (order_checks) do not change it,
        # because the user sees them on the ticket and the broker re-checks at placement. "server"
        # (the salted hash the chain lines use) ties the review to the MCP server that answered it:
        # the confirm gate binds a place call only to a review from the same server, so a review
        # answered by the sandbox or any look-alike server never vouches for a live order.
        try:
            entry = {"fingerprint": fingerprint, "session": session, "ts": now, "tool": bare,
                     "review_ok": bool(review["review_ok"]), "alerts_count": review["alerts_count"],
                     "server": ctx.server_hash(server)}
            if review.get("quote"):
                entry["quote"] = review["quote"]
            if review.get("estimated_cost"):
                entry["estimated_cost"] = review["estimated_cost"]
            entry["consumed_by"] = None
            entry["ticket_id"] = ticket_id
            entry["canonical"] = inputs
            append_ledger(state, entry)
        except Exception:
            pass
    if bare in PLACE_TOOLS and ref_id:
        try:
            order_id, order_state = order_fields(data) if ok else (None, None)
            if not ok or (order_state or "").lower() in FAILED_STATES:
                status = "failed"
            elif order_id:
                status = "succeeded"
            else:
                status = "pending"
            update_refids(state, ref_id, fingerprint, status)
        except Exception:
            pass

    after = read_last_tool(state, session)
    try:
        chain.append(now, ctx.fields(
            session=session, event="post", tool=bare, server=ctx.server_hash(server), cls=cls, decision="none",
            reason_code=None, mode=ctx.mode, fingerprint=fingerprint, ticket_id=ticket_id, ref_id=ref_id,
            inputs=inputs, result=result, after_tool=after, policy=policy_sha(event.get("cwd"))))
    finally:
        write_last_tool(state, session, bare)


def session_line(ctx, chain, event):
    source = event.get("source")
    chain.append(None, ctx.fields(
        session=ctx.session_hash(event.get("session_id")), event="session_start", tool=None, server=None,
        cls=None, decision="none", reason_code=None, mode=ctx.mode,
        result={"ok": True, "source": source if source in SESSION_SOURCES else None},
        policy=policy_sha(event.get("cwd"))))


# --------------------------------------------------------------------------- main

def read_event():
    try:
        raw = sys.stdin.buffer.read()
    except (AttributeError, OSError, ValueError):
        return {}
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return {}
    try:
        event = json.loads(text)
    except ValueError:
        return {}
    return event if isinstance(event, dict) else {}


def run(argv):
    if audit_disabled():
        return
    sys.dont_write_bytecode = True  # never drop __pycache__ into the plugin's own folders
    session_start = "--session-start" in argv
    event = read_event()
    os.umask(0o077)
    state = state_dir()
    audit_dir = os.path.join(state, "audit")
    os.makedirs(audit_dir, mode=0o700, exist_ok=True)

    classes = load_classes()
    known = load_known(classes)
    servers = load_servers(state)
    server, bare = split_tool(event.get("tool_name"))
    response = None
    log_post = False
    if not session_start and bare:
        response = parse_response(event.get("tool_response"))
        if bare in known and bare not in GENERIC_NAMES:
            log_post = True
        elif is_rh_server(server, servers):
            log_post = True
        elif bare == "get_accounts" and response[0] and looks_like_rh_accounts(response[1]):
            log_post = True  # the response shape itself proves this is Robinhood's server
    if not (session_start or log_post or pending_blocked(audit_dir)):
        return

    lock = os.path.join(state, ".lock")
    if not acquire_lock(lock):
        return
    try:
        ctx = Context(state, event, classes)
        chain = Chain(audit_dir)
        try:
            fold_blocked(ctx, chain)
        except Exception:
            pass
        if session_start:
            session_line(ctx, chain, event)
            prune_last_tool(state)
        elif log_post:
            process_post(ctx, chain, event, server, bare, known, servers, response)
        enforce_retention(audit_dir, chain.current_name())
    finally:
        release_lock(lock)


def main(argv=None):
    try:
        run(sys.argv[1:] if argv is None else argv)
    except BaseException:  # never let logging change the outcome of a tool call
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
