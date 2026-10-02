#!/usr/bin/env python3
"""kitconfig.py - find, parse and validate the kit's one config file.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: every threshold in this kit (stop rules, screener criteria, policy limits) is
the user's decision, not the agent's. Config values therefore ship as "UNSET", and this loader
reports exactly which values are still unset instead of letting anyone fill them in. A value
that is UNSET stops the screener and exit rules; for [policy] it means "not configured" and the
check is skipped and listed. Optional filters accept "OFF".

Grammar (deliberately small so it parses identically everywhere):
    # comments, [a] and [a.b.c] headers,
    key = "string" | integer | true/false | ["s", ...] | [["s", "s"], ...]
No inline tables, multi-line strings, single-quoted strings, dotted keys, dates or floats;
write decimals as strings ("31.24"). On Python >= 3.11 the file is also parsed with tomllib and
the two results must agree, so 3.9 and 3.12 read the same file the same way. config.json with
the same structure is accepted as a fallback.

Lookup order: a path the user names > $ROBINHOOD_SKILLS_CONFIG > ./.robinhood/config.toml >
${XDG_CONFIG_HOME:-~/.config}/robinhood-skills/config.toml > a pasted block that starts with
the line "# robinhood-skills:config" > none (the skill asks). config.json is tried after
config.toml at each location. This script only reads; it never writes the config.
"./" is the `cwd` input when given, else the working directory. Pass {"cwd": "<the user's project
directory>"} whenever the script runs from inside a skill folder: from there "./" is the skill, and the
script answers PROJECT_DIR_UNKNOWN (or warns, when a lower-priority file was found) instead of a false
CONFIG_NOT_FOUND.

Usage:
    python3 kitconfig.py locate|get|validate < input.json > output.json
    python3 kitconfig.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network, no file writes; one JSON object out; exit 0
whenever JSON was printed, exit 1 only on a crash.
"""

import json
import os
import re
import sys
from decimal import Decimal, InvalidOperation

try:
    import tomllib  # Python 3.11+
except ImportError:  # 3.9 / 3.10: the restricted parser below is the only parser
    tomllib = None

VERSION = "2.0.0"
SCRIPT = "kitconfig"
UNSET = "UNSET"
OFF = "OFF"
PASTE_HEADER = "# robinhood-skills:config"
MAX_BYTES = 1024 * 1024
ENV_VAR = "ROBINHOOD_SKILLS_CONFIG"

SESSIONS = ["regular_hours", "extended_hours", "all_day_hours", "regular_curb_hours",
            "regular_curb_overnight_hours"]
STRUCTURES = ["long_call", "long_put", "debit_call_spread", "debit_put_spread"]
SPREAD_STRUCTURES = ("debit_call_spread", "debit_put_spread")

# ---------------------------------------------------------------------------------------------
# Section schemas. mode "required": UNSET keys are reported as missing and stop the run.
# mode "optional": UNSET means "not configured" (the check is skipped and listed).
# Key specs: type, min/max (inclusive unless *_exclusive), off (accepts "OFF"), optional (never
# reported missing even in a required section), when_spread (required only for spread structures).
# ---------------------------------------------------------------------------------------------
def _dec(minimum=None, maximum=None, min_exclusive=False, **kw):
    spec = {"type": "decimal", "min": minimum, "max": maximum, "min_exclusive": min_exclusive}
    spec.update(kw)
    return spec


def _int(minimum=None, **kw):
    spec = {"type": "int", "min": minimum}
    spec.update(kw)
    return spec


def _enum(values, **kw):
    spec = {"type": "enum", "values": list(values)}
    spec.update(kw)
    return spec


SECTION_SCHEMAS = {
    "policy": {"mode": "optional", "keys": {
        "read_scope": _enum(["ask", "agentic_only", "all"]),
        "max_order_usd": _dec("0", min_exclusive=True),
        "max_symbol_pct_household": _dec("0", "100", min_exclusive=True),
        "symbol_allowlist": {"type": "symbols"},
        "symbol_denylist": {"type": "symbols"},
        "earnings_blackout_days": _int(0),
        "max_orders_per_day": _int(1),
        "allow_options": {"type": "bool"},
        "allow_crypto": {"type": "bool"},
        "allowed_sessions": {"type": "enum_list", "values": SESSIONS},
        "max_option_contracts": _int(1),
    }},
    "accounts": {"mode": "optional", "keys": {
        "retirement": {"type": "last4_list"},
    }},
    "tax": {"mode": "optional", "keys": {
        "min_loss_usd": _dec("0"),
        "lookahead_days": _int(1),
        "related_tickers": {"type": "ticker_groups"},
        "gtc_lookback_days": _int(1),
    }},
    "exits.equity": {"mode": "required", "subtables": ["symbols"], "keys": {
        "stop_rule": {"type": "rule", "forms": ["pct", "atr", "ask"]},
        "target_rule": {"type": "rule", "forms": ["pct", "r", "none", "ask"]},
        "time_in_force": _enum(["gfd", "gtc"]),
        "backstop_alerts": _enum(["yes", "no"]),
    }},
    "exits.equity.symbols.*": {"mode": "optional", "keys": {
        "stop": _dec("0", min_exclusive=True),
        "target": _dec("0", min_exclusive=True),
    }},
    "exits.crypto": {"mode": "required", "keys": {
        "stop_rule": {"type": "rule", "forms": ["pct", "ask"]},
        "time_in_force": _enum(["gtc", "gfd", "gfw", "gfm"]),
    }},
    "options.exits": {"mode": "required", "keys": {
        "profit_target_pct": _dec("0", min_exclusive=True),
        # No upper bound: a short option can lose more than the premium it collected, so a seller's stop
        # (for example "exit at a 200% loss") is legitimately above 100. On a long option a stop at or above
        # 100 can only fire at total loss; the screener, which opens long-only structures, says so itself.
        "stop_loss_pct": _dec("0", min_exclusive=True),
        "time_stop_dte": _int(0),
        "max_hold_days": _int(1),
        "exit_price_rule": _enum(["bid", "mid"], optional=True),
    }},
    "options.monitor": {"mode": "optional", "keys": {
        "radar_days": _int(1),
    }},
    "options.criteria": {"mode": "required", "keys": {
        "max_position_pct": _dec("0", "100", min_exclusive=True),
        "max_concurrent": _int(1),
        "max_cost_per_contract_usd": _dec("0", min_exclusive=True),
        "reserve_cash_usd": _dec("0"),
        "price_min": _dec("0", min_exclusive=True),
        "price_max": _dec("0", min_exclusive=True),
        "min_avg_volume": _int(0),
        "symbols_allowlist": {"type": "symbols", "optional": True},
        "symbols_blocklist": {"type": "symbols", "optional": True},
        "structure": _enum(STRUCTURES),
        "spread_width_min": _dec("0", min_exclusive=True, off=True, when_spread=True),
        "spread_width_max": _dec("0", min_exclusive=True, off=True, when_spread=True),
        "dte_min": _int(0),
        "dte_max": _int(0),
        "delta_min": _dec("0", "1"),
        "delta_max": _dec("0", "1"),
        "max_spread_pct": _dec("0", min_exclusive=True),
        "min_open_interest": _int(0),
        "iv_rank_min": _dec("0", "100", off=True),
        "iv_rank_max": _dec("0", "100", off=True),
        "earnings_policy": _enum(["avoid", "allow", "require"]),
        "earnings_buffer_days": _int(0),
        "scan_sessions": _enum(["regular_hours_only"]),
        "sort_by": {"type": "identifier", "optional": True},
    }},
    "options.entry": {"mode": "required", "keys": {
        "entry_price_rule": _enum(["ask_each_time", "natural", "mid"]),
        "contracts_per_entry": _int(1),
    }},
    "report": {"mode": "optional", "keys": {
        "window_days": _int(1),
    }},
}

MIN_MAX_PAIRS = {
    "options.criteria": [("price_min", "price_max"), ("dte_min", "dte_max"), ("delta_min", "delta_max"),
                         ("spread_width_min", "spread_width_max"), ("iv_rank_min", "iv_rank_max")],
}

KNOWN_TREE = {"policy": None, "accounts": None, "tax": None, "report": None,
              "exits": {"equity": {"symbols": "*"}, "crypto": None},
              "options": {"exits": None, "monitor": None, "criteria": None, "entry": None}}

STRUCTURE_REQUIREMENTS = {
    "long_call": {"legs": 1, "option_level": "option_level_2", "account_types": ["cash", "margin", "limited_margin"],
                  "retirement_allowed": True, "requires": []},
    "long_put": {"legs": 1, "option_level": "option_level_2", "account_types": ["cash", "margin", "limited_margin"],
                 "retirement_allowed": True, "requires": []},
    "debit_call_spread": {"legs": 2, "option_level": "option_level_3", "account_types": ["margin", "limited_margin"],
                          "retirement_allowed": False, "requires": ["spread_width_min", "spread_width_max"],
                          "cash_account_route": ["get_limited_margin_upgrade_info", "user completes the flow",
                                                 "re-fetch get_accounts", "get_option_level_upgrade_info"]},
    "debit_put_spread": {"legs": 2, "option_level": "option_level_3", "account_types": ["margin", "limited_margin"],
                         "retirement_allowed": False, "requires": ["spread_width_min", "spread_width_max"],
                         "cash_account_route": ["get_limited_margin_upgrade_info", "user completes the flow",
                                                "re-fetch get_accounts", "get_option_level_upgrade_info"]},
}


class InputError(Exception):
    def __init__(self, code, field, msg, extra=None):
        Exception.__init__(self, msg)
        self.code, self.field, self.msg, self.extra = code, field, msg, extra or {}


class ConfigError(Exception):
    """A parse or grammar problem in the config text."""

    def __init__(self, code, line, msg):
        Exception.__init__(self, msg)
        self.code, self.line, self.msg = code, line, msg


def _err(code, field, msg, **extra):
    out = {"ok": False, "errors": [{"code": code, "field": field, "msg": msg}]}
    out.update(extra)
    return out


# ---------------------------------------------------------------------------------------------
# Restricted TOML parser
# ---------------------------------------------------------------------------------------------
_BARE = re.compile(r"[A-Za-z0-9_-]+")
_INT = re.compile(r"[+-]?(?:0|[1-9][0-9]*)")
_DELIMS = " \t\r\n#,]"
_ESCAPES = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}


class _Parser(object):
    def __init__(self, text):
        self.s = text
        self.i = 0
        self.n = len(text)

    def line(self):
        return self.s.count("\n", 0, self.i) + 1

    def fail(self, msg, code="GRAMMAR"):
        raise ConfigError(code, self.line(), msg)

    def peek(self, k=0):
        j = self.i + k
        return self.s[j] if j < self.n else ""

    def skip_ws(self):
        while self.peek() in (" ", "\t"):
            self.i += 1

    def skip_comment(self):
        if self.peek() == "#":
            while self.i < self.n and self.s[self.i] != "\n":
                c = self.s[self.i]
                if (ord(c) < 0x20 and c not in ("\t", "\r")) or c == "\x7f":
                    self.fail("control character in a comment")
                if c == "\r" and self.peek(1) != "\n":
                    self.fail("bare carriage return")
                self.i += 1

    def newline(self):
        c = self.peek()
        if c == "\r":
            if self.peek(1) != "\n":
                self.fail("bare carriage return")
            self.i += 2
            return True
        if c == "\n":
            self.i += 1
            return True
        return False

    def end_of_line(self):
        self.skip_ws()
        self.skip_comment()
        if self.i >= self.n:
            return
        if not self.newline():
            self.fail("unexpected text after the value")

    def bare_key(self):
        m = _BARE.match(self.s, self.i)
        if not m:
            if self.peek() in ('"', "'"):
                self.fail("quoted keys are not allowed; use bare keys (letters, digits, _ and -)")
            self.fail("expected a key")
        self.i = m.end()
        return m.group(0)

    def document(self):
        root = {}
        explicit = set()
        current = root
        while self.i < self.n:
            self.skip_ws()
            c = self.peek()
            if c == "":
                break
            if self.newline():
                continue
            if c == "#":
                self.skip_comment()
                continue
            if c == "[":
                if self.peek(1) == "[":
                    self.fail("arrays of tables ([[...]]) are not allowed")
                self.i += 1
                self.skip_ws()
                path = [self.bare_key()]
                self.skip_ws()
                while self.peek() == ".":
                    self.i += 1
                    self.skip_ws()
                    path.append(self.bare_key())
                    self.skip_ws()
                if self.peek() != "]":
                    self.fail("expected ] to close the table header")
                self.i += 1
                key = tuple(path)
                if key in explicit:
                    self.fail("table [%s] is defined twice" % ".".join(path), "DUPLICATE")
                node = root
                for seg in path:
                    if seg not in node:
                        node[seg] = {}
                    elif not isinstance(node[seg], dict):
                        self.fail("[%s] conflicts with a value named %s" % (".".join(path), seg), "DUPLICATE")
                    node = node[seg]
                explicit.add(key)
                current = node
                self.end_of_line()
                continue
            key = self.bare_key()
            self.skip_ws()
            if self.peek() == ".":
                self.fail("dotted keys are not allowed; use a [section] header")
            if self.peek() != "=":
                self.fail("expected = after key %s" % key)
            self.i += 1
            self.skip_ws()
            value = self.value(0)
            if key in current:
                self.fail("key %s is defined twice" % key, "DUPLICATE")
            current[key] = value
            self.end_of_line()
        return root

    def value(self, depth):
        c = self.peek()
        if c == '"':
            if self.s.startswith('"""', self.i):
                self.fail("multi-line strings are not allowed")
            return self.string()
        if c == "'":
            self.fail("single-quoted strings are not allowed; use double quotes")
        if c == "[":
            if depth >= 2:
                self.fail("arrays nest at most two levels: [[\"a\", \"b\"]]")
            return self.array(depth + 1)
        if c == "{":
            self.fail("inline tables are not allowed; use a [section] header")
        for word, val in (("true", True), ("false", False)):
            if self.s.startswith(word, self.i):
                after = self.peek(len(word))
                if after == "" or after in _DELIMS:
                    self.i += len(word)
                    return val
        m = _INT.match(self.s, self.i)
        if m:
            after = self.s[m.end():m.end() + 1]
            if after == "" or after in _DELIMS:
                self.i = m.end()
                return int(m.group(0))
            if after in ".eE":
                self.fail("floats are not allowed; write decimals as strings, e.g. \"31.24\"")
            if after in "-:":
                self.fail("dates and times are not allowed; write them as strings")
            if after == "_":
                self.fail("underscores in numbers are not allowed")
            self.fail("only plain decimal integers are allowed")
        self.fail("unsupported value; use \"string\", integer, true/false or an array of strings")

    def string(self):
        self.i += 1
        out = []
        while True:
            if self.i >= self.n or self.s[self.i] in ("\n", "\r"):
                self.fail("unterminated string")
            c = self.s[self.i]
            if c == '"':
                self.i += 1
                return "".join(out)
            if c == "\\":
                e = self.peek(1)
                if e in _ESCAPES:
                    out.append(_ESCAPES[e])
                    self.i += 2
                    continue
                if e in ("u", "U"):
                    width = 4 if e == "u" else 8
                    hexs = self.s[self.i + 2:self.i + 2 + width]
                    if len(hexs) != width or not re.match(r"^[0-9A-Fa-f]+$", hexs):
                        self.fail("bad unicode escape")
                    cp = int(hexs, 16)
                    if cp > sys.maxunicode or 0xD800 <= cp <= 0xDFFF:
                        self.fail("unicode escape is not a scalar value")
                    out.append(chr(cp))
                    self.i += 2 + width
                    continue
                self.fail("unsupported escape \\%s" % e)
            if (ord(c) < 0x20 and c != "\t") or c == "\x7f":
                self.fail("control character in a string")
            out.append(c)
            self.i += 1

    def array_space(self):
        while True:
            c = self.peek()
            if c in (" ", "\t"):
                self.i += 1
            elif c == "#":
                self.skip_comment()
            elif c in ("\n", "\r"):
                self.newline()
            else:
                return

    def array(self, depth):
        self.i += 1
        items = []
        while True:
            self.array_space()
            if self.peek() == "]":
                self.i += 1
                break
            if self.peek() == "":
                self.fail("unterminated array")
            item = self.value(depth)
            if isinstance(item, bool) or isinstance(item, int):
                self.fail("arrays may hold only strings (or arrays of strings)")
            if depth == 2 and not isinstance(item, str):
                self.fail("inner arrays may hold only strings")
            if items and type(items[0]) is not type(item):
                self.fail("do not mix strings and arrays in one array")
            items.append(item)
            self.array_space()
            if self.peek() == ",":
                self.i += 1
                continue
            if self.peek() == "]":
                self.i += 1
                break
            self.fail("expected , or ] in array")
        return items


def parse_toml(text):
    """Parse the restricted grammar; on Python 3.11+ cross-check with tomllib."""
    if text.startswith("\ufeff"):
        text = text[1:]
    doc = _Parser(text).document()
    if tomllib is not None:
        try:
            other = tomllib.loads(text)
        except Exception as exc:  # tomllib.TOMLDecodeError
            raise ConfigError("PARSE_MISMATCH", 0, "tomllib rejects this file: %s" % exc)
        if other != doc:
            raise ConfigError("PARSE_MISMATCH", 0, "tomllib and the restricted parser disagree")
    return doc


def _json_pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ConfigError("DUPLICATE", 0, "key %s is defined twice" % k)
        out[k] = v
    return out


def _no_float(_):
    raise ConfigError("GRAMMAR", 0, "floats are not allowed; write decimals as strings, e.g. \"31.24\"")


def _no_constant(name):
    raise ConfigError("GRAMMAR", 0, "%s is not allowed" % name)


def _check_json_node(node, path):
    for key, val in node.items():
        where = ".".join(path + [key])
        if not _BARE.fullmatch(key):
            raise ConfigError("GRAMMAR", 0, "key %r must use letters, digits, _ or -" % where)
        if isinstance(val, dict):
            _check_json_node(val, path + [key])
        elif isinstance(val, list):
            kinds = set()
            for item in val:
                if isinstance(item, str):
                    kinds.add("s")
                elif isinstance(item, list) and all(isinstance(x, str) for x in item):
                    kinds.add("a")
                else:
                    raise ConfigError("GRAMMAR", 0, "%s: arrays may hold only strings or arrays of strings" % where)
            if len(kinds) > 1:
                raise ConfigError("GRAMMAR", 0, "%s: do not mix strings and arrays in one array" % where)
        elif val is None:
            raise ConfigError("GRAMMAR", 0, "%s: null is not allowed; use \"UNSET\"" % where)
        elif not isinstance(val, (str, int)):
            raise ConfigError("GRAMMAR", 0, "%s: unsupported value type" % where)


def parse_json(text):
    try:
        doc = json.loads(text, object_pairs_hook=_json_pairs, parse_float=_no_float, parse_constant=_no_constant)
    except ConfigError:
        raise
    except ValueError as exc:
        raise ConfigError("PARSE_ERROR", getattr(exc, "lineno", 0), "invalid JSON: %s" % exc)
    if not isinstance(doc, dict):
        raise ConfigError("GRAMMAR", 0, "the JSON config must be an object")
    _check_json_node(doc, [])
    return doc


def parse_text(text, fmt=None):
    if fmt is None:
        fmt = "json" if text.lstrip().startswith("{") else "toml"
    return parse_json(text) if fmt == "json" else parse_toml(text)


# ---------------------------------------------------------------------------------------------
# Locating the file
# ---------------------------------------------------------------------------------------------
SKILL_SUBFOLDERS = ("scripts", "references", "assets")


def _skill_folder(path):
    """The skill folder `path` is in, else None: a folder holding SKILL.md, one of its scripts/, references/ or
    assets/ subfolders, or anywhere under the skill this copy of the script ships in. A skill's scripts are
    often run from inside the skill, which makes the working directory the skill folder, not the user's
    project, and ./.robinhood/config.toml would then be looked for in the wrong place."""
    here = os.path.abspath(path)
    if os.path.isfile(os.path.join(here, "SKILL.md")):
        return here
    parent = os.path.dirname(here)
    if os.path.basename(here) in SKILL_SUBFOLDERS and os.path.isfile(os.path.join(parent, "SKILL.md")):
        return parent
    own = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isfile(os.path.join(own, "SKILL.md")) and (here == own or here.startswith(own + os.sep)):
        return own
    return None


def _project_dir_warning(skill_dir, found_inside):
    msg = ("the working directory is inside the skill folder %s, not the user's project, so the project config "
           "(<project>/.robinhood/config.toml) was not searched; run again with {\"cwd\": \"<the user's project "
           "directory>\"}" % skill_dir)
    if found_inside:
        msg += ("; the config found inside the skill folder is replaced when the skill is updated: move it to the "
                "project's .robinhood/config.toml or ~/.config/robinhood-skills/config.toml")
    return {"code": "PROJECT_DIR_UNKNOWN", "field": "cwd", "msg": msg}


def _candidates(data):
    """(ordered list of (label, path) to try, named, skill folder the working directory is in or None).
    A path the user names is the only candidate."""
    named = data.get("path")
    if named:
        return [("user_named", os.path.expanduser(str(named)))], True, None
    out = []
    env = os.environ.get(ENV_VAR)
    if env:
        out.append(("env:" + ENV_VAR, os.path.expanduser(env)))
    given = data.get("cwd")
    if given is not None and (not isinstance(given, str) or not given.strip()):
        raise InputError("BAD_INPUT", "cwd", "cwd is the user's project directory, as a path string")
    cwd = given or os.getcwd()
    skill_dir = None if given else _skill_folder(cwd)
    for name in ("config.toml", "config.json"):
        out.append(("project", os.path.join(cwd, ".robinhood", name)))
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    for name in ("config.toml", "config.json"):
        out.append(("user", os.path.join(xdg, "robinhood-skills", name)))
    return out, False, skill_dir


def _extract_pasted(text):
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if line.strip() == PASTE_HEADER:
            body = []
            for rest in lines[idx + 1:]:
                if rest.strip().startswith("```"):
                    break
                body.append(rest)
            return "\n".join(body) + "\n"
    return None


def locate(data):
    """Returns (source_label, path_or_None, text_or_None, searched, warnings)."""
    if "config" in data:
        if not isinstance(data["config"], dict):
            raise InputError("BAD_INPUT", "config", "config must be an object")
        return "input", None, None, [{"source": "input", "path": None, "exists": True}], []
    if "text" in data:
        return "input", None, str(data["text"]), [{"source": "input", "path": None, "exists": True}], []
    searched = []
    cands, named, skill_dir = _candidates(data)

    def warnings(label):
        return [_project_dir_warning(skill_dir, label == "project")] if skill_dir else []

    for label, path in cands:
        exists = os.path.isfile(path)
        searched.append({"source": label, "path": path, "exists": exists})
        if exists:
            try:
                if os.path.getsize(path) > MAX_BYTES:
                    raise InputError("TOO_LARGE", "path", "%s is larger than 1 MB; not a kit config" % path)
                with open(path, "r", encoding="utf-8") as fh:
                    return label, path, fh.read(), searched, warnings(label)
            except (OSError, UnicodeDecodeError) as exc:
                raise InputError("READ_ERROR", "path", "cannot read %s: %s" % (path, exc.__class__.__name__),
                                 {"searched": searched})
    if named:
        return None, None, None, searched, []
    pasted = data.get("pasted")
    if pasted:
        body = _extract_pasted(str(pasted))
        searched.append({"source": "pasted", "path": None, "exists": body is not None})
        if body is not None:
            return "pasted", None, body, searched, warnings("pasted")
    return None, None, None, searched, warnings(None)


def _not_found(meta, msg, **extra):
    """CONFIG_NOT_FOUND, or PROJECT_DIR_UNKNOWN when the search ran from inside a skill folder (the project
    config was never looked for, so "not found" would be a false answer)."""
    extra["searched"] = meta["searched"]
    for w in meta.get("warnings") or []:
        if w["code"] == "PROJECT_DIR_UNKNOWN":
            return _err("PROJECT_DIR_UNKNOWN", "cwd", w["msg"], **extra)
    return _err("CONFIG_NOT_FOUND", "path", msg, **extra)


def load(data):
    """Returns (config_dict_or_None, meta). Raises InputError on read or parse failure."""
    source, path, text, searched, warnings = locate(data)
    meta = {"source": source, "path_used": path, "searched": searched, "warnings": warnings}
    if source is None:
        return None, meta
    if "config" in data and source == "input":
        cfg = data["config"]
        try:
            _check_json_node(cfg, [])
        except ConfigError as exc:
            raise InputError(exc.code, "config", exc.msg, {"searched": searched})
        return cfg, meta
    fmt = data.get("format")
    if fmt is None and path:
        fmt = "json" if path.endswith(".json") else "toml"
    try:
        return parse_text(text, fmt), meta
    except ConfigError as exc:
        where = path or source
        raise InputError(exc.code, "line %d" % exc.line if exc.line else "", "%s: %s" % (where, exc.msg),
                         {"searched": searched, "path_used": path})


# ---------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------
def get_node(cfg, dotted):
    node = cfg
    for seg in dotted.split("."):
        if not isinstance(node, dict) or seg not in node:
            return None
        node = node[seg]
    return node


def _schema_for(section):
    if section in SECTION_SCHEMAS:
        return SECTION_SCHEMAS[section]
    if section.startswith("exits.equity.symbols.") and section.count(".") == 3:
        return SECTION_SCHEMAS["exits.equity.symbols.*"]
    return None


def _to_decimal(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, str) and re.match(r"^[+-]?(\d+(\.\d*)?|\.\d+)$", v.strip()):
        try:
            return Decimal(v.strip())
        except InvalidOperation:
            return None
    return None


def _to_int(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and re.match(r"^[+-]?\d+$", v.strip()):
        return int(v.strip())
    return None


def _check_rule(value, forms):
    if value in ("ask", "none"):
        return value in forms
    parts = value.split(":")
    kind = parts[0]
    if kind not in forms:
        return False
    if kind in ("pct", "r") and len(parts) == 2:
        d = _to_decimal(parts[1])
        return d is not None and d > 0 and (kind != "pct" or d < 100)
    if kind == "atr" and len(parts) == 3:
        m, p = _to_decimal(parts[1]), _to_int(parts[2])
        return m is not None and m > 0 and p is not None and p >= 1
    return False


def check_value(spec, value):
    """Returns an error message or None. Called only for values that are not UNSET/OFF."""
    t = spec["type"]
    if t == "decimal":
        d = _to_decimal(value)
        if d is None:
            return "expected a decimal string like \"31.24\""
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None:
            if spec.get("min_exclusive") and d <= Decimal(lo):
                return "must be greater than %s" % lo
            if not spec.get("min_exclusive") and d < Decimal(lo):
                return "must be at least %s" % lo
        if hi is not None and d > Decimal(hi):
            return "must be at most %s" % hi
        return None
    if t == "int":
        i = _to_int(value)
        if i is None:
            return "expected a whole number (as a string, e.g. \"7\")"
        if spec.get("min") is not None and i < spec["min"]:
            return "must be at least %d" % spec["min"]
        return None
    if t == "enum":
        return None if value in spec["values"] else "must be one of: %s" % ", ".join(spec["values"])
    if t == "bool":
        return None if isinstance(value, bool) else "must be true or false"
    if t == "symbols":
        if not isinstance(value, list) or not all(isinstance(x, str) and re.match(r"^[A-Za-z0-9.\-]{1,15}$", x)
                                                  for x in value):
            return "must be a list of ticker strings"
        return None
    if t == "enum_list":
        if not isinstance(value, list) or not all(x in spec["values"] for x in value):
            return "must be a list drawn from: %s" % ", ".join(spec["values"])
        return None
    if t == "last4_list":
        if not isinstance(value, list) or not all(isinstance(x, str) and re.match(r"^[A-Za-z0-9]{4}$", x)
                                                  for x in value):
            return "must be a list of 4-character account endings, e.g. [\"P0Z9\"]"
        return None
    if t == "ticker_groups":
        if not isinstance(value, list) or not all(isinstance(g, list) and len(g) >= 2 and all(isinstance(x, str) for x in g)
                                                  for g in value):
            return "must be a list of groups of 2+ tickers, e.g. [[\"VOO\", \"IVV\"]]"
        return None
    if t == "rule":
        if not isinstance(value, str) or not _check_rule(value, spec["forms"]):
            examples = {"pct": "pct:8", "atr": "atr:2.0:14", "r": "r:2", "ask": "ask", "none": "none"}
            return "must look like %s" % " | ".join(examples[f] for f in spec["forms"])
        return None
    if t == "identifier":
        return None if isinstance(value, str) and re.match(r"^[a-z][a-z0-9_]*$", value) else "must be a lowercase name"
    return "unknown spec type"


def analyze_section(cfg, section):
    """Classify every key of one section. Returns a dict of findings."""
    schema = _schema_for(section)
    node = get_node(cfg, section) if cfg is not None else None
    res = {"section": section, "present": isinstance(node, dict), "values": node if isinstance(node, dict) else {},
           "unset": [], "off": [], "missing": [], "errors": [], "warnings": [], "unknown_keys": []}
    if schema is None:
        res["errors"].append({"code": "UNKNOWN_SECTION", "field": section, "msg": "not a section this kit reads"})
        return res
    if node is not None and not isinstance(node, dict):
        res["errors"].append({"code": "NOT_A_TABLE", "field": section, "msg": "expected a [section] table"})
        node = None
    values = node or {}
    structure = values.get("structure") if section == "options.criteria" else None
    is_spread = structure in SPREAD_STRUCTURES
    for key, spec in schema["keys"].items():
        name = "%s.%s" % (section, key)
        val = values.get(key, UNSET)
        if val == UNSET:
            res["unset"].append(key)
            needed = schema["mode"] == "required" and not spec.get("optional")
            if spec.get("when_spread"):
                needed = needed and is_spread
            if needed:
                res["missing"].append(name)
            continue
        if val == OFF:
            if spec.get("off") and not (spec.get("when_spread") and is_spread):
                res["off"].append(key)
                continue
            msg = "OFF is not allowed here"
            if spec.get("when_spread") and is_spread:
                msg = "OFF is not allowed for a spread structure; set a width"
            res["errors"].append({"code": "OFF_NOT_ALLOWED", "field": name, "msg": msg})
            continue
        problem = check_value(spec, val)
        if problem:
            code = "BAD_ENUM" if spec["type"] in ("enum", "enum_list") else "BAD_VALUE"
            res["errors"].append({"code": code, "field": name, "msg": problem})
    subtables = schema.get("subtables", [])
    for key in values:
        if key not in schema["keys"] and key not in subtables:
            res["unknown_keys"].append(key)
            res["warnings"].append({"code": "UNKNOWN_KEY", "field": "%s.%s" % (section, key),
                                    "msg": "not a key this kit reads (typo?); it is ignored"})
    for lo, hi in MIN_MAX_PAIRS.get(section, []):
        a, b = values.get(lo, UNSET), values.get(hi, UNSET)
        if a in (UNSET, OFF) or b in (UNSET, OFF):
            continue
        da, db = _to_decimal(a), _to_decimal(b)
        if da is not None and db is not None and da > db:
            res["errors"].append({"code": "MIN_GT_MAX", "fields": ["%s.%s" % (section, lo), "%s.%s" % (section, hi)],
                                  "msg": "%s (%s) is greater than %s (%s)" % (lo, a, hi, b)})
    if section.startswith("exits.equity.symbols."):
        s, t = _to_decimal(values.get("stop", UNSET)), _to_decimal(values.get("target", UNSET))
        if s is not None and t is not None and s >= t:
            res["errors"].append({"code": "STOP_NOT_BELOW_TARGET", "fields": [section + ".stop", section + ".target"],
                                  "msg": "for a long position the stop must be below the target"})
    return res


def _present_sections(cfg):
    found = []
    for top, sub in KNOWN_TREE.items():
        node = cfg.get(top)
        if not isinstance(node, dict):
            continue
        if sub is None:
            found.append(top)
            continue
        for name, subsub in sub.items():
            child = node.get(name)
            if not isinstance(child, dict):
                continue
            found.append("%s.%s" % (top, name))
            if isinstance(subsub, dict):
                for inner in subsub:
                    table = child.get(inner)
                    if isinstance(table, dict):
                        for sym, val in table.items():
                            if isinstance(val, dict):
                                found.append("%s.%s.%s.%s" % (top, name, inner, sym))
    return found


def _structure_warnings(cfg):
    out = []
    for top, val in cfg.items():
        if top not in KNOWN_TREE:
            out.append({"code": "UNKNOWN_SECTION", "field": top, "msg": "not a section this kit reads; ignored"})
        elif not isinstance(val, dict):
            out.append({"code": "NOT_A_TABLE", "field": top, "msg": "expected a [section] table"})
    return out


def _expand_sections(cfg, sections):
    out = []
    for s in sections:
        if s == "exits.equity.symbols":
            table = get_node(cfg, s) if cfg is not None else None
            if isinstance(table, dict):
                out.extend("exits.equity.symbols.%s" % sym for sym in sorted(table))
        else:
            out.append(s)
    return out


# ---------------------------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------------------------
def _with_warnings(meta, out):
    """Add a warnings list only when there is one (the usual output shape stays unchanged)."""
    if meta.get("warnings"):
        out["warnings"] = list(meta["warnings"])
    return out


def op_locate(data):
    source, path, _, searched, warnings = locate(data)
    return _with_warnings({"warnings": warnings},
                          {"ok": True, "path_used": path, "source": source, "searched": searched})


def op_get(data):
    section = data.get("section")
    if not isinstance(section, str) or not section:
        raise InputError("MISSING_FIELD", "section", "give the section name, e.g. \"tax\" or \"options.exits\"")
    if _schema_for(section) is None and section != "exits.equity.symbols":
        raise InputError("UNKNOWN_SECTION", "section", "%r is not a section this kit reads" % section)
    cfg, meta = load(data)
    if cfg is None:
        schema = _schema_for(section)
        keys = sorted(schema["keys"]) if schema else []
        return _not_found(meta, "no config file found; ask the user only for the values this run needs", unset=keys)
    if section == "exits.equity.symbols":
        node = get_node(cfg, section)
        return _with_warnings(meta, {"ok": True, "path_used": meta["path_used"], "source": meta["source"], "section_name": section,
                "section": node if isinstance(node, dict) else {}, "unset": [], "off": [], "invalid": [],
                "unknown_keys": [], "missing_section": not isinstance(node, dict)})
    res = analyze_section(cfg, section)
    return _with_warnings(meta, {
        "ok": True,
        "path_used": meta["path_used"],
        "source": meta["source"],
        "section_name": section,
        "section": res["values"],
        "unset": res["unset"],
        "off": res["off"],
        "invalid": res["errors"],
        "unknown_keys": res["unknown_keys"],
        "missing_section": not res["present"],
    })


def op_validate(data):
    sections = data.get("sections")
    if sections is not None and (not isinstance(sections, list) or not all(isinstance(s, str) for s in sections)):
        raise InputError("BAD_INPUT", "sections", "sections must be a list of section names")
    cfg, meta = load(data)
    if cfg is None:
        wanted = sections or []
        missing = []
        for s in wanted:
            schema = _schema_for(s)
            if schema and schema["mode"] == "required":
                missing.extend("%s.%s" % (s, k) for k, spec in schema["keys"].items()
                               if not spec.get("optional") and not spec.get("when_spread"))
        return _not_found(meta, "no config file found", missing=missing)
    wanted = _expand_sections(cfg, sections if sections else _present_sections(cfg))
    missing, errors, warnings = [], [], list(meta["warnings"])
    if not sections:
        warnings.extend(_structure_warnings(cfg))
    report = {}
    for s in wanted:
        res = analyze_section(cfg, s)
        missing.extend(res["missing"])
        errors.extend(res["errors"])
        warnings.extend(res["warnings"])
        report[s] = {"present": res["present"], "unset": res["unset"], "off": res["off"]}
    out = {
        "ok": True,
        "valid": not missing and not errors,
        "path_used": meta["path_used"],
        "source": meta["source"],
        "sections": report,
        "missing": missing,
        "errors": errors,
        "warnings": warnings,
    }
    if "options.criteria" in wanted:
        structure = (get_node(cfg, "options.criteria") or {}).get("structure")
        if structure in STRUCTURE_REQUIREMENTS:
            out["structure_requirements"] = dict(STRUCTURE_REQUIREMENTS[structure], structure=structure)
    return out


OPS = {"locate": op_locate, "get": op_get, "validate": op_validate}


def run(op, data):
    if op not in OPS:
        return _err("UNKNOWN_OP", "op", "unknown op %r; expected one of %s" % (op, ", ".join(sorted(OPS))))
    if not isinstance(data, dict):
        return _err("BAD_INPUT", "", "input must be a JSON object")
    try:
        return OPS[op](data)
    except InputError as exc:
        return _err(exc.code, exc.field, exc.msg, **exc.extra)


# ---------------------------------------------------------------------------------------------
# JSON Schemas and self-test
# ---------------------------------------------------------------------------------------------
_S = {"type": "string"}
_SN = {"type": ["string", "null"]}
_B = {"type": "boolean"}
_LIST_S = {"type": "array", "items": _S}
_FINDING = {"type": "object", "required": ["code", "msg"],
            "properties": {"code": _S, "field": _S, "fields": _LIST_S, "msg": _S}}
_SEARCHED = {"type": "array", "items": {"type": "object", "required": ["source", "exists"],
                                        "properties": {"source": _S, "path": _SN, "exists": _B}}}
_ERR = {"type": "object", "required": ["ok", "errors"],
        "properties": {"ok": {"enum": [False]}, "errors": {"type": "array", "items": _FINDING},
                       "searched": _SEARCHED, "unset": _LIST_S, "missing": _LIST_S, "path_used": _SN}}
_LOCATE_IN = {"path": _S, "pasted": _S, "cwd": _S, "text": _S, "format": {"enum": ["toml", "json"]},
              "config": {"type": "object"}}


def _obj(props, required=(), extra=False):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": extra}


SCHEMAS = {
    "locate": {
        "input": _obj(_LOCATE_IN),
        "output": {"anyOf": [_obj({"ok": {"enum": [True]}, "path_used": _SN, "source": _SN, "searched": _SEARCHED,
                                   "warnings": {"type": "array", "items": _FINDING}},
                                  ["ok", "path_used", "searched"]), _ERR]},
    },
    "get": {
        "input": _obj(dict(_LOCATE_IN, section=_S), ["section"]),
        "output": {"anyOf": [_obj({"ok": {"enum": [True]}, "path_used": _SN, "source": _SN, "section_name": _S,
                                   "section": {"type": "object"}, "unset": _LIST_S, "off": _LIST_S,
                                   "invalid": {"type": "array", "items": _FINDING}, "unknown_keys": _LIST_S,
                                   "missing_section": _B, "warnings": {"type": "array", "items": _FINDING}},
                                  ["ok", "path_used", "section_name", "section", "unset", "off"]), _ERR]},
    },
    "validate": {
        "input": _obj(dict(_LOCATE_IN, sections=_LIST_S)),
        "output": {"anyOf": [_obj({"ok": {"enum": [True]}, "valid": _B, "path_used": _SN, "source": _SN,
                                   "sections": {"type": "object"}, "missing": _LIST_S,
                                   "errors": {"type": "array", "items": _FINDING},
                                   "warnings": {"type": "array", "items": _FINDING},
                                   "structure_requirements": {"type": "object"}},
                                  ["ok", "valid", "missing", "errors"]), _ERR]},
    },
}


def _type_ok(value, t):
    return {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }.get(t, lambda v: False)(value)


def schema_errors(value, schema, path="$"):
    """Minimal JSON Schema check used by --selftest."""
    if "anyOf" in schema:
        if any(not schema_errors(value, s, path) for s in schema["anyOf"]):
            return []
        return ["%s: matches no anyOf branch" % path]
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_type_ok(value, x) for x in types):
            return ["%s: expected %s" % (path, "/".join(types))]
    errs = []
    if "enum" in schema and not any(value == e and type(value) is type(e) for e in schema["enum"]):
        errs.append("%s: %r not in enum" % (path, value))
    if "pattern" in schema and isinstance(value, str) and not re.search(schema["pattern"], value):
        errs.append("%s: does not match pattern" % path)
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errs.append("%s: missing %s" % (path, key))
        props, extra = schema.get("properties", {}), schema.get("additionalProperties", True)
        for key, sub in value.items():
            if key in props:
                errs.extend(schema_errors(sub, props[key], "%s.%s" % (path, key)))
            elif extra is False:
                errs.append("%s: unexpected key %s" % (path, key))
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errs.extend(schema_errors(item, schema["items"], "%s[%d]" % (path, i)))
    return errs


def full_schema():
    ops = {}
    for name, pair in SCHEMAS.items():
        ops[name] = {k: dict(v, **{"$schema": "https://json-schema.org/draft/2020-12/schema"}) for k, v in pair.items()}
    return {"script": SCRIPT, "version": VERSION, "ops": ops, "sections": sorted(SECTION_SCHEMAS)}


_EXAMPLE_TOML = """# robinhood-skills:config
[options.criteria]
dte_min = "30"
dte_max = "21"           # min greater than max
structure = "debit_call_spread"
spread_width_min = "OFF" # OFF is not allowed for a spread
[options.exits]
profit_target_pct = "50"
stop_loss_pct = "40"
time_stop_dte = "7"
max_hold_days = "UNSET"
[tax]
related_tickers = [["VOO", "IVV"]]
"""

EXAMPLES = [
    ("get", {"text": _EXAMPLE_TOML, "section": "options.exits"},
     {"ok": True, "unset": ["max_hold_days", "exit_price_rule"], "section": {"profit_target_pct": "50"}}),
    ("validate", {"text": _EXAMPLE_TOML, "sections": ["options.exits"]},
     {"ok": True, "valid": False, "missing": ["options.exits.max_hold_days"]}),
    ("validate", {"text": _EXAMPLE_TOML, "sections": ["options.criteria"]},
     {"ok": True, "valid": False,
      "errors": [{"code": "OFF_NOT_ALLOWED", "field": "options.criteria.spread_width_min"},
                 {"code": "MIN_GT_MAX", "fields": ["options.criteria.dte_min", "options.criteria.dte_max"]}],
      "structure_requirements": {"option_level": "option_level_3"}}),
    ("get", {"text": "[policy]\nlimits = { max = 1 }\n", "section": "policy"},
     {"ok": False, "errors": [{"code": "GRAMMAR"}]}),
    ("get", {"text": "[tax]\nmin_loss_usd = 1.5\n", "section": "tax"},
     {"ok": False, "errors": [{"code": "GRAMMAR"}]}),
    ("get", {"config": {"tax": {"min_loss_usd": "UNSET", "lookahead_days": "45"}}, "section": "tax"},
     {"ok": True, "unset": ["min_loss_usd", "related_tickers", "gtc_lookback_days"]}),
    ("locate", {"pasted": "notes\n# robinhood-skills:config\n[report]\nwindow_days = \"7\"\n"},
     {"ok": True, "source": "pasted"}),
]


def _subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) <= len(actual) and all(
            _subset(e, a) for e, a in zip(expected, actual))
    return expected == actual and type(expected) is type(actual)


def selftest():
    failures = []
    # Isolate the self-test from any real config on this machine.
    nowhere = os.path.join(os.sep, "nonexistent-kitconfig-selftest")
    saved = {k: os.environ.get(k) for k in (ENV_VAR, "XDG_CONFIG_HOME")}
    os.environ.pop(ENV_VAR, None)
    os.environ["XDG_CONFIG_HOME"] = nowhere
    try:
        for i, (op, inp, expected) in enumerate(EXAMPLES):
            if op == "locate":
                inp = dict(inp, cwd=nowhere)
            problems = schema_errors(inp, SCHEMAS[op]["input"])
            out = run(op, inp)
            problems += schema_errors(out, SCHEMAS[op]["output"])
            if not _subset(expected, out):
                problems.append("output mismatch: %s" % json.dumps(out, sort_keys=True))
            if problems:
                failures.append({"case": i, "op": op, "problems": problems})
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return {"ok": not failures, "script": SCRIPT, "selftest": {"cases": len(EXAMPLES), "failed": failures}}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--schema" in argv:
        print(json.dumps(full_schema(), indent=2, sort_keys=True))
        return 0
    if "--selftest" in argv:
        result = selftest()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["ok"] else 1
    if not argv:
        print(json.dumps(_err("MISSING_OP", "op", "usage: kitconfig.py locate|get|validate < input.json")))
        return 0
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError as exc:
        print(json.dumps(_err("BAD_JSON", "", "stdin is not valid JSON: %s" % exc)))
        return 0
    print(json.dumps(run(argv[0], data), indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
