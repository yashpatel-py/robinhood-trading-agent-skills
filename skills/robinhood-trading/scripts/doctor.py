#!/usr/bin/env python3
"""doctor.py - readiness checks for the core robinhood-trading skill.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: three questions come up on the first Robinhood action of a session, and each is
easy to answer wrongly from memory.
  * "Is the order guard really on?" A hook that is installed but broken looks exactly like one that
    works, until the day it matters. `selftest` pipes a synthetic place_equity_order event to the
    local guard script and reports whether it blocked (exit 2). It never calls an MCP tool, so it
    can never place anything. A script that blocks proves only the script: the repo tree (and so
    hooks/guard.sh) also ships to Codex and Gemini, where no hook calls it. So `active` needs the
    caller to report that the session shows the plugin's SessionStart line ("Robinhood order
    mode: ..."), which appears only when the plugin's hooks are loaded; without it the result is
    `script_only`, and the order boundary is advised only.
  * "Has Robinhood changed the tool list?" The connector adds tools without a changelog, and a new
    money-moving tool is exactly what a stale kit would call without a second thought.
    `inventory` diffs the tool names this session can see against the kit's 81-tool snapshot and
    says whether each new money-like tool is covered by the always-on guard layer.
  * "Can this account do that strategy, and what is the upgrade route?" Spreads need options
    level 3 AND a margin or limited-margin account, and a cash account must switch to limited
    margin BEFORE the options upgrade. `capability` maps a strategy to what it requires and to the
    exact order of enrollment calls, including the calls not to make.

Ops:
  selftest    {"guard_path"?, "tools"?, "timeout_s"?, "order_mode_line_seen"?}
              -> {"guard": "active"|"script_only"|"FAILED"|"not_found", ...}
  inventory   {"seen_tools": [...], "inventory"?, "inventory_path"?} -> new / missing / money-like tools
  capability  {"account": {...}, "strategy": "..."} -> allowed, required level and account types, route

Usage:
    python3 doctor.py selftest|inventory|capability < input.json > output.json
    python3 doctor.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network. One JSON object out; exit 0 whenever JSON was
printed, exit 1 only on a crash. `selftest` runs `sh <guard_path> money` locally with a temporary
state directory that it deletes afterwards; no other op touches the file system beyond reading the
tool inventory.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

VERSION = "2.0.0"
SCRIPT = "doctor"
HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------------------------
# Constants shared with the hook contract (build spec D.4) and the connector rules (R1, R15, R16)
# ---------------------------------------------------------------------------------------------
SELFTEST_SERVER = "selftest"
DEFAULT_SELFTEST_TOOLS = ["place_equity_order"]
ALLOWED_SELFTEST_TOOLS = ("place_equity_order", "place_option_order", "place_crypto_order", "place_advanced_order",
                          "exercise_option", "replace_option_order")
DEFAULT_TIMEOUT_S = 10
STDERR_HEAD = 300
ALLOW_RE = re.compile(r'"(permissionDecision|permission)"\s*:\s*"allow"')
MONEY_LIKE_RE = re.compile(
    r"^(place|exercise|replace|submit|execute|transfer|withdraw|deposit|stake|unstake|convert|send|buy|sell|trade|"
    r"liquidate|lend|borrow)_")
GUARDED_RE = re.compile(r"^(place|exercise|replace)_")
TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
PREFIX_RE = re.compile(r"^mcp__.+__(?P<bare>[A-Za-z0-9_]+)$")
INVENTORY_FILE = os.path.join(HERE, "tool_inventory.json")
REPO_CLASSES_FILE = os.path.normpath(os.path.join(HERE, "..", "..", "..", "connector", "tool-classes.json"))
PLUGIN_GUARD_FILE = os.path.normpath(os.path.join(HERE, "..", "..", "..", "hooks", "guard.sh"))

ORDER_MODE_LINE_PREFIX = "Robinhood order mode:"
DENY_SNIPPET_NOTE = ("integrations/claude-code/settings.deny.json, which lists only the servers robinhood-trading and "
                     "plugin_unofficial-rh-connector_robinhood: for any other server name /mcp shows, add "
                     "mcp__<name>__place_equity_order, mcp__<name>__place_option_order, "
                     "mcp__<name>__place_crypto_order, mcp__<name>__place_advanced_order and "
                     "mcp__<name>__exercise_option to its deny list")
NO_HOOK_NOTE = ("only the session's '%s' line shows that the Claude Code plugin's hooks are loaded; a guard script "
                "found (or missing) on disk proves nothing about hook registration. On Codex or Cursor, merge "
                "integrations/<client>/hooks.json from the kit's repository and run that README's first-run test, "
                "which checks the hook end to end. In Claude Code without the plugin, add %s"
                % (ORDER_MODE_LINE_PREFIX, DENY_SNIPPET_NOTE))

UNGUARDED_NOTE = ("not caught by the always-on guard layer (place_/exercise_/replace_ on any server); the plugin's "
                  "classify layer blocks it only on servers it recognizes as Robinhood (name contains 'robinhood', or "
                  "the server already served a known Robinhood tool). Do not call it (connector rule R1).")

ACCOUNT_TYPES = ("cash", "margin", "limited_margin")
L2_STRATEGIES = ("long_call", "long_put", "covered_call", "cash_secured_put")
L3_STRATEGIES = ("vertical_spread", "calendar", "iron_condor", "roll_single_order", "straddle", "strangle",
                 "multi_leg")
STRATEGY_ALIASES = {
    "debit_call_spread": "vertical_spread", "debit_put_spread": "vertical_spread",
    "credit_call_spread": "vertical_spread", "credit_put_spread": "vertical_spread",
    "call_spread": "vertical_spread", "put_spread": "vertical_spread", "spread": "vertical_spread",
    "calendar_spread": "calendar", "roll": "roll_single_order", "csp": "cash_secured_put",
}
STRATEGIES = L2_STRATEGIES + L3_STRATEGIES + ("crypto",)
RETIREMENT_WORDS = re.compile(r"(ira|roth|retirement|401k)", re.IGNORECASE)
NON_RETIREMENT_TYPES = ("individual", "joint")


class InputError(Exception):
    def __init__(self, code, field, msg):
        Exception.__init__(self, msg)
        self.code, self.field, self.msg = code, field, msg


def _err(code, field, msg):
    return {"ok": False, "errors": [{"code": code, "field": field, "msg": msg}]}


# ---------------------------------------------------------------------------------------------
# selftest: pipe a synthetic money-tool event to the local guard and require exit 2
# ---------------------------------------------------------------------------------------------
def resolve_guard_path(data):
    """Return (path, source). The caller's path wins; then $CLAUDE_PLUGIN_ROOT; then this plugin's own tree."""
    given = data.get("guard_path")
    if given is not None:
        if not isinstance(given, str) or not given.strip():
            raise InputError("BAD_VALUE", "guard_path", "guard_path must be a non-empty string path to hooks/guard.sh")
        expanded = os.path.expanduser(os.path.expandvars(given.strip()))
        if "$" in expanded:
            # ${CLAUDE_PLUGIN_ROOT} was passed literally and is not set in this shell: fall back below.
            return fallback_guard_path("guard_path contained an unset variable (%s)" % given.strip())
        return os.path.abspath(expanded), "input"
    return fallback_guard_path(None)


def fallback_guard_path(reason):
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root:
        return os.path.join(root, "hooks", "guard.sh"), "CLAUDE_PLUGIN_ROOT" + ("" if reason is None else "; " + reason)
    return PLUGIN_GUARD_FILE, "plugin tree next to this skill" + ("" if reason is None else "; " + reason)


def selftest_tools(data):
    tools = data.get("tools")
    if tools is None:
        return list(DEFAULT_SELFTEST_TOOLS)
    if not isinstance(tools, list) or not tools:
        raise InputError("BAD_VALUE", "tools", "tools is a non-empty list of money tool names")
    out = []
    for i, name in enumerate(tools):
        if not isinstance(name, str) or name.strip() not in ALLOWED_SELFTEST_TOOLS:
            raise InputError("BAD_VALUE", "tools[%d]" % i,
                             "self-test tools must be among: %s" % ", ".join(ALLOWED_SELFTEST_TOOLS))
        if name.strip() not in out:
            out.append(name.strip())
    return out


def selftest_timeout(data):
    value = data.get("timeout_s", DEFAULT_TIMEOUT_S)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 30:
        raise InputError("BAD_VALUE", "timeout_s", "timeout_s is a whole number of seconds from 1 to 30")
    return value


def selftest_line_seen(data):
    """True only when the caller saw the plugin's SessionStart line; omitted or null counts as not seen."""
    value = data.get("order_mode_line_seen")
    if value is None:
        return False
    if not isinstance(value, bool):
        raise InputError("BAD_VALUE", "order_mode_line_seen",
                         "order_mode_line_seen is true only when this session shows the line starting '%s' "
                         "(the plugin's SessionStart hook), otherwise false" % ORDER_MODE_LINE_PREFIX)
    return value


def synthetic_event(tool):
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": "mcp__%s__%s" % (SELFTEST_SERVER, tool),
                       "tool_input": {}, "session_id": "selftest", "permission_mode": "default"},
                      separators=(",", ":"))


def one_line(text, limit=STDERR_HEAD):
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def run_guard_once(sh, guard_path, tool, timeout):
    """Run the guard for one synthetic event with a scrubbed environment and a throwaway state dir."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_PLUGIN_OPTION_")}
    state = tempfile.mkdtemp(prefix="rh-doctor-")
    env["ROBINHOOD_SKILLS_STATE"] = state
    try:
        try:
            proc = subprocess.run([sh, guard_path, "money"], input=synthetic_event(tool).encode("utf-8"),
                                  capture_output=True, env=env, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return {"tool": tool, "exit_code": None, "blocked": False, "stderr_head": "",
                    "problem": "the guard did not finish within %d s" % timeout}
        except OSError as exc:
            return {"tool": tool, "exit_code": None, "blocked": False, "stderr_head": "",
                    "problem": "the guard could not be started: %s" % exc}
    finally:
        shutil.rmtree(state, ignore_errors=True)
    stdout = proc.stdout.decode("utf-8", "replace")
    stderr = proc.stderr.decode("utf-8", "replace")
    row = {"tool": tool, "exit_code": proc.returncode, "blocked": proc.returncode == 2,
           "stderr_head": one_line(stderr)}
    if ALLOW_RE.search(stdout):
        row["blocked"] = False
        row["problem"] = "the guard printed a permission 'allow'; no hook in this kit may ever do that"
    elif proc.returncode != 2:
        row["problem"] = "expected exit 2 (block), got exit %d" % proc.returncode
    return row


def op_selftest(data):
    guard_path, source = resolve_guard_path(data)
    tools = selftest_tools(data)
    timeout = selftest_timeout(data)
    line_seen = selftest_line_seen(data)
    base = {"ok": True, "guard_path": guard_path, "guard_path_source": source, "payload_server": SELFTEST_SERVER,
            "tools_tested": tools, "never_calls_mcp": True, "order_mode_line_seen": line_seen}
    if not os.path.isfile(guard_path):
        if line_seen:
            summary = ("Order guard self-test could not run: no guard script at this path, although the session's "
                       "order-mode line shows the plugin's hooks are loaded.")
            note = ("pass the plugin's hooks/guard.sh as guard_path (expand ${CLAUDE_PLUGIN_ROOT}) and run the "
                    "self-test again; until then the guard is unverified")
        else:
            summary = "Order guard: not installed on this surface (advised only)."
            note = "no guard script at this path; " + NO_HOOK_NOTE
        base.update({"guard": "not_found", "exit_code": None, "stderr_head": "", "results": [],
                     "summary": summary, "note": note})
        return base
    sh = shutil.which("sh")
    if sh is None:
        base.update({"guard": "FAILED", "exit_code": None, "stderr_head": "", "results": [],
                     "summary": "Order guard self-test FAILED: no POSIX sh on this system, so the hook cannot run.",
                     "note": "without sh the plugin hook fails open (native Windows); to block the place tools, "
                             "add " + DENY_SNIPPET_NOTE})
        return base
    results = [run_guard_once(sh, guard_path, tool, timeout) for tool in tools]
    failures = [r for r in results if not r["blocked"]]
    first = failures[0] if failures else results[0]
    if len(results) == 1:
        what, each = "a synthetic %s" % results[0]["tool"], "exit 2"
    else:
        what, each = "synthetic %s" % ", ".join(r["tool"] for r in results), "exit 2 each"
    if failures:
        guard = "FAILED"
        summary = "Order guard self-test FAILED: %s was not blocked (%s)." % (first["tool"], first.get("problem", ""))
    elif line_seen:
        guard = "active"
        summary = "Guard self-test: blocked %s (%s)." % (what, each)
    else:
        # The script works, but nothing shows a PreToolUse hook calls it on this surface.
        guard = "script_only"
        summary = ("Guard script blocks %s (%s), but no order-guard hook is confirmed on this surface: order "
                   "boundary advised only." % (what, each))
    base.update({"guard": guard, "exit_code": first["exit_code"],
                 "stderr_head": first["stderr_head"], "results": results, "summary": summary})
    if failures:
        base["note"] = ("treat the order boundary as advised only until this passes; reinstall the plugin or check "
                        "hooks/guard.sh, then run the self-test again")
    elif guard == "script_only":
        base["note"] = "the script half works; " + NO_HOOK_NOTE
    return base


# ---------------------------------------------------------------------------------------------
# inventory: diff the visible tool names against the kit's snapshot
# ---------------------------------------------------------------------------------------------
def bare_name(raw):
    name = raw.strip()
    m = PREFIX_RE.match(name)
    if m:
        name = m.group("bare")
    return name


def load_inventory(data):
    """Return (tools {name: class}, snapshot_date, absent_referenced, source)."""
    inline = data.get("inventory")
    if inline is not None:
        return parse_inventory(inline, "input")
    path = data.get("inventory_path")
    if path is not None:
        if not isinstance(path, str) or not path.strip():
            raise InputError("BAD_VALUE", "inventory_path", "inventory_path must be a path to tool_inventory.json")
        candidates = [os.path.abspath(os.path.expanduser(path.strip()))]
    else:
        candidates = [INVENTORY_FILE, REPO_CLASSES_FILE]
    for candidate in candidates:
        if os.path.isfile(candidate):
            try:
                with open(candidate, encoding="utf-8") as fh:
                    parsed = json.load(fh)
            except (OSError, ValueError) as exc:
                raise InputError("INVENTORY_UNREADABLE", "inventory_path", "%s: %s" % (candidate, exc))
            return parse_inventory(parsed, candidate)
    raise InputError("INVENTORY_MISSING", "inventory_path",
                     "no tool inventory found (looked for %s); reinstall the skill so scripts/tool_inventory.json "
                     "is present" % ", ".join(candidates))


def parse_inventory(obj, source):
    if not isinstance(obj, dict):
        raise InputError("BAD_INVENTORY", "inventory", "the inventory must be a JSON object")
    raw = obj.get("tools")
    tools = {}
    if isinstance(raw, dict):  # scripts/tool_inventory.json: {"tools": {name: class}}
        for name, cls in raw.items():
            tools[str(name)] = cls if isinstance(cls, str) else None
    elif isinstance(raw, list):  # connector/tool-classes.json: {"tools": [{"name", "class"}]}
        for t in raw:
            if isinstance(t, dict) and isinstance(t.get("name"), str):
                tools[t["name"]] = t.get("class") if isinstance(t.get("class"), str) else None
    if not tools:
        raise InputError("BAD_INVENTORY", "inventory", "the inventory has no tools")
    absent = []
    for a in obj.get("absent_referenced") or []:
        if isinstance(a, str):
            absent.append(a)
        elif isinstance(a, dict) and isinstance(a.get("name"), str):
            absent.append(a["name"])
    snapshot = obj.get("snapshot_captured_at")
    return tools, (snapshot if isinstance(snapshot, str) else None), absent, source


def op_inventory(data):
    seen_raw = data.get("seen_tools")
    if not isinstance(seen_raw, list):
        raise InputError("MISSING_FIELD", "seen_tools", "seen_tools is the list of Robinhood tool names this session "
                                                        "can see (bare or prefixed)")
    seen, ignored = [], []
    for i, raw in enumerate(seen_raw):
        if not isinstance(raw, str) or not raw.strip():
            ignored.append({"index": i, "why": "not a tool name"})
            continue
        name = bare_name(raw)
        if not TOOL_NAME_RE.match(name):
            ignored.append({"index": i, "value": raw.strip()[:80], "why": "not a tool name"})
            continue
        if name not in seen:
            seen.append(name)
    if not seen:
        return _err("NO_TOOLS_SEEN", "seen_tools", "no Robinhood tool names given: if none are visible, report "
                                                   "CONNECTOR UNAVAILABLE instead of running the inventory")
    tools, snapshot, absent, source = load_inventory(data)
    new = sorted(n for n in seen if n not in tools)
    missing = sorted(n for n in tools if n not in seen)
    money_like = [n for n in new if MONEY_LIKE_RE.match(n)]
    guarded = [n for n in money_like if GUARDED_RE.match(n)]
    unguarded = [n for n in money_like if not GUARDED_RE.match(n)]
    other_new = [n for n in new if n not in money_like]
    by_class = {}
    for n in seen:
        cls = tools.get(n)
        if n in tools:
            by_class[cls or "unclassified"] = by_class.get(cls or "unclassified", 0) + 1
    advice = []
    if money_like:
        advice.append("new money-like tools: never call them; the kit may be out of date (connector rule R1)")
    if unguarded:
        advice.append("%s: %s" % (", ".join(unguarded), UNGUARDED_NOTE))
    if other_new:
        advice.append("other new tools: do not call one that might create, change or cancel orders or move money "
                      "until the kit classifies it (R1); read-only lookups are safe to describe but still unverified")
    if missing:
        advice.append("tools in the snapshot that this session does not list are unavailable here; do not call them")
    out = {
        "ok": True,
        "status": "match" if not new and not missing else "drift",
        "snapshot_date": snapshot,
        "inventory_source": source,
        "known": len(tools),
        "seen_count": len(seen),
        "seen_known": len(seen) - len(new),
        "seen_by_class": dict(sorted(by_class.items())),
        "new": new,
        "missing": missing,
        "new_money_like": money_like,
        "new_money_like_guarded": guarded,
        "new_money_like_unguarded": unguarded,
        "previously_absent_now_visible": sorted(n for n in new if n in absent),
        "ignored": ignored,
        "advice": advice,
        "limits": "a tool can be listed and still be disabled for the account (connector rule R26); only a call "
                  "shows that",
        "summary": "Connector: %d known tools visible · %d new · %d missing (snapshot %s)" % (
            len(seen) - len(new), len(new), len(missing), snapshot or "date unknown"),
    }
    return out


# ---------------------------------------------------------------------------------------------
# capability: strategy -> options level, account type and enrollment route
# ---------------------------------------------------------------------------------------------
def level_rank(level):
    if level is None:
        return 0
    if not isinstance(level, str):
        raise InputError("BAD_VALUE", "account.option_level", "option_level is a string such as option_level_2, "
                                                              "or null/empty")
    text = level.strip().lower()
    if text in ("", "none", "null", "option_level_0"):
        return 0
    m = re.match(r"^option_level_(\d+)$", text)
    if not m:
        raise InputError("BAD_VALUE", "account.option_level", "unrecognized option_level %r (expected option_level_N "
                                                              "or null/empty)" % level)
    return int(m.group(1))


def account_facts(account):
    if not isinstance(account, dict):
        raise InputError("MISSING_FIELD", "account", "account is {type, option_level, retirement} from get_accounts")
    acct_type = account.get("type")
    if acct_type is not None:
        if not isinstance(acct_type, str) or acct_type.strip().lower() not in ACCOUNT_TYPES:
            raise InputError("BAD_VALUE", "account.type", "type is cash, margin or limited_margin (or null if unknown)")
        acct_type = acct_type.strip().lower()
    retirement = account.get("retirement")
    inferred = False
    if retirement is not None and not isinstance(retirement, bool):
        raise InputError("BAD_VALUE", "account.retirement", "retirement is true, false or null (unknown)")
    if retirement is None:
        bat = account.get("brokerage_account_type")
        if isinstance(bat, str) and bat.strip():
            if RETIREMENT_WORDS.search(bat):
                retirement, inferred = True, True
            elif bat.strip().lower() in NON_RETIREMENT_TYPES:
                retirement, inferred = False, True
    agentic = account.get("agentic_allowed")
    if agentic is not None and not isinstance(agentic, bool):
        raise InputError("BAD_VALUE", "account.agentic_allowed", "agentic_allowed is true or false")
    crypto = account.get("has_crypto_account")
    if crypto is not None and not isinstance(crypto, bool):
        raise InputError("BAD_VALUE", "account.has_crypto_account", "has_crypto_account is true, false or null")
    return {"type": acct_type, "rank": level_rank(account.get("option_level")),
            "level": account.get("option_level"), "retirement": retirement, "retirement_inferred": inferred,
            "agentic": agentic, "crypto": crypto}


def canonical_strategy(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise InputError("MISSING_FIELD", "strategy", "strategy is one of: %s" % ", ".join(STRATEGIES))
    s = raw.strip().lower().replace("-", "_").replace(" ", "_")
    s = STRATEGY_ALIASES.get(s, s)
    if s not in STRATEGIES:
        raise InputError("BAD_VALUE", "strategy", "unknown strategy %r; one of: %s" % (raw, ", ".join(STRATEGIES)))
    return s


def level_name(rank):
    return "option_level_%d" % rank


def op_capability(data):
    facts = account_facts(data.get("account"))
    strategy = canonical_strategy(data.get("strategy"))
    out = {"ok": True, "strategy": strategy, "allowed": False, "route": [], "do_not_call": [], "unknowns": [],
           "reviewable": facts["agentic"], "notes": []}
    if strategy == "crypto":
        capability_crypto(facts, out)
    else:
        capability_options(facts, strategy, out)
    if facts["agentic"] is False:
        out["notes"].append("this account is read-only to agents: the review tools reject it, so any order here is "
                            "the user's to enter in the app (handoff variant b)")
        simulator = "preview_crypto_order" if strategy == "crypto" else "review_option_order"
        if simulator not in out["do_not_call"]:
            out["do_not_call"].append(simulator)
    if facts["retirement_inferred"]:
        out["notes"].append("retirement status inferred from brokerage_account_type; confirm with the user if unsure")
    out["note"] = " ".join(out["notes"]) if out["notes"] else ""
    return out


def capability_crypto(facts, out):
    out["required"] = {"option_level": None, "account_types": list(ACCOUNT_TYPES), "retirement_allowed": None,
                       "crypto_account": True}
    out["notes"].append("crypto is unavailable in some states, including New York; after a move from a restricted "
                        "state, access returns after 45 days")
    if facts["crypto"] is True:
        out["allowed"] = True
        out["do_not_call"] = ["get_crypto_account_onboarding_info"]
    elif facts["crypto"] is False:
        out["route"] = ["get_crypto_account_onboarding_info", "user signs the crypto agreement and opens the account",
                        "re-fetch get_accounts"]
        out["do_not_call"] = ["preview_crypto_order"]
        out["notes"].append("present the link as 'if you haven't opened a crypto account yet'")
    else:
        out["unknowns"].append("account.has_crypto_account")
        out["route"] = ["check get_accounts for a linked Crypto Account, or ask the user",
                        "only if they have none: get_crypto_account_onboarding_info"]
        out["notes"].append("never infer 'no crypto account' from an error alone")


def capability_options(facts, strategy, out):
    need = 2 if strategy in L2_STRATEGIES else 3
    rank = facts["rank"]
    if need == 2:
        out["required"] = {"option_level": level_name(2), "account_types": list(ACCOUNT_TYPES),
                           "retirement_allowed": True}
        if strategy == "covered_call":
            out["notes"].append("a covered call needs 100 shares of the underlying per contract in the same account")
        if strategy == "cash_secured_put":
            out["notes"].append("a cash-secured put needs strike x 100 in cash per contract; the options review shows "
                                "collateral when chain_symbol and underlying_type are sent")
        if rank >= 2:
            out["allowed"] = True
            out["do_not_call"] = ["get_option_level_upgrade_info"]
        else:
            out["route"] = ["get_option_level_upgrade_info", "user completes the options application",
                            "re-fetch get_accounts"]
            out["do_not_call"] = ["review_option_order"]
            out["notes"].append("no options access yet (option level %s)" % (facts["level"] or "empty"))
        return

    out["required"] = {"option_level": level_name(3), "account_types": ["margin", "limited_margin"],
                       "retirement_allowed": False}
    if strategy == "roll_single_order":
        out["notes"].append("a one-order roll is limit only and needs direction plus a net price from the user")
    if facts["retirement"] is True:
        out["do_not_call"] = ["get_option_level_upgrade_info", "get_limited_margin_upgrade_info", "review_option_order"]
        out["notes"].append("multi-leg orders are not available in retirement accounts through these tools")
        add_roll_alternative(facts, strategy, out)
        return
    if facts["retirement"] is None:
        out["unknowns"].append("account.retirement")
    if facts["type"] is None:
        out["unknowns"].append("account.type")
    if out["unknowns"]:
        out["route"] = ["re-fetch get_accounts", "ask the user whether this is a retirement account if still unclear"]
        out["do_not_call"] = ["get_option_level_upgrade_info", "get_limited_margin_upgrade_info", "review_option_order"]
        out["notes"].append("unknown is never clear: settle the account facts before routing an upgrade")
        return
    if facts["type"] == "cash":
        out["route"] = ["get_limited_margin_upgrade_info", "user completes the limited-margin upgrade",
                        "re-fetch get_accounts"]
        if rank < 3:
            out["route"].append("get_option_level_upgrade_info")
        out["do_not_call"] = ["get_option_level_upgrade_info", "review_option_order"]
        out["notes"].append("a cash account must become limited margin first; call the options upgrade only after "
                            "get_accounts shows margin or limited_margin (limited margin adds no borrowing or "
                            "leverage)")
        add_roll_alternative(facts, strategy, out)
        return
    if rank >= 3:
        out["allowed"] = True
        out["do_not_call"] = ["get_option_level_upgrade_info", "get_limited_margin_upgrade_info"]
        return
    out["route"] = ["get_option_level_upgrade_info", "user completes the options application", "re-fetch get_accounts"]
    out["do_not_call"] = ["get_limited_margin_upgrade_info", "review_option_order"]
    out["notes"].append("the account type already qualifies; only the options level (%s) is short"
                        % (facts["level"] or "empty"))
    add_roll_alternative(facts, strategy, out)


def add_roll_alternative(facts, strategy, out):
    if strategy == "roll_single_order" and facts["rank"] >= 2:
        out["alternative"] = {"how": "two single-leg orders: close the held contract, then open the replacement",
                              "allowed": True,
                              "risk": "legging risk: the market can move between the two fills, and only the first "
                                      "may fill"}


OPS = {"selftest": op_selftest, "inventory": op_inventory, "capability": op_capability}


def run(op, data):
    if op not in OPS:
        return _err("UNKNOWN_OP", "op", "unknown op %r; expected selftest, inventory or capability" % op)
    if not isinstance(data, dict):
        return _err("BAD_INPUT", "", "input must be a JSON object")
    try:
        return OPS[op](data)
    except InputError as exc:
        return _err(exc.code, exc.field, exc.msg)


# ---------------------------------------------------------------------------------------------
# JSON Schemas and self-test
# ---------------------------------------------------------------------------------------------
_S = {"type": "string"}
_SN = {"type": ["string", "null"]}
_B = {"type": "boolean"}
_BN = {"type": ["boolean", "null"]}
_LS = {"type": "array", "items": _S}
_ERR = {"type": "object", "required": ["ok", "errors"],
        "properties": {"ok": {"enum": [False]},
                       "errors": {"type": "array", "items": {"type": "object", "required": ["code", "field", "msg"],
                                                             "properties": {"code": _S, "field": _S, "msg": _S}}}}}
_INVENTORY_OBJ = {"type": "object", "required": ["tools"], "properties": {
    "tools": {"type": ["object", "array"]}, "snapshot_captured_at": _SN, "absent_referenced": {"type": "array"}}}
SCHEMAS = {
    "selftest": {
        "input": {"type": "object", "additionalProperties": False, "properties": {
            "guard_path": _S, "tools": {"type": "array", "items": {"enum": list(ALLOWED_SELFTEST_TOOLS)}},
            "timeout_s": {"type": "integer", "minimum": 1, "maximum": 30}, "order_mode_line_seen": _BN}},
        "output": {"anyOf": [{"type": "object", "additionalProperties": False,
                              "required": ["ok", "guard", "exit_code", "stderr_head", "summary",
                                           "order_mode_line_seen"],
                              "properties": {
                                  "ok": {"enum": [True]},
                                  "guard": {"enum": ["active", "script_only", "FAILED", "not_found"]},
                                  "exit_code": {"type": ["integer", "null"]}, "stderr_head": _S, "summary": _S,
                                  "guard_path": _S, "guard_path_source": _S, "payload_server": _S,
                                  "tools_tested": _LS, "never_calls_mcp": {"enum": [True]}, "note": _S,
                                  "order_mode_line_seen": _B,
                                  "results": {"type": "array", "items": {
                                      "type": "object", "required": ["tool", "exit_code", "blocked", "stderr_head"],
                                      "properties": {"tool": _S, "exit_code": {"type": ["integer", "null"]},
                                                     "blocked": _B, "stderr_head": _S, "problem": _S}}}}}, _ERR]},
    },
    "inventory": {
        "input": {"type": "object", "additionalProperties": False, "required": ["seen_tools"], "properties": {
            "seen_tools": _LS, "inventory": _INVENTORY_OBJ, "inventory_path": _S}},
        "output": {"anyOf": [{"type": "object", "additionalProperties": False,
                              "required": ["ok", "status", "snapshot_date", "known", "new", "missing",
                                           "new_money_like", "new_money_like_guarded", "new_money_like_unguarded"],
                              "properties": {
                                  "ok": {"enum": [True]}, "status": {"enum": ["match", "drift"]},
                                  "snapshot_date": _SN, "inventory_source": _S, "known": {"type": "integer"},
                                  "seen_count": {"type": "integer"}, "seen_known": {"type": "integer"},
                                  "seen_by_class": {"type": "object"}, "new": _LS, "missing": _LS,
                                  "new_money_like": _LS, "new_money_like_guarded": _LS,
                                  "new_money_like_unguarded": _LS, "previously_absent_now_visible": _LS,
                                  "ignored": {"type": "array"}, "advice": _LS, "limits": _S, "summary": _S}}, _ERR]},
    },
    "capability": {
        "input": {"type": "object", "additionalProperties": False, "required": ["account", "strategy"], "properties": {
            "account": {"type": "object", "properties": {
                "type": {"enum": ["cash", "margin", "limited_margin", None]}, "option_level": _SN,
                "retirement": _BN, "brokerage_account_type": _SN, "agentic_allowed": _BN,
                "has_crypto_account": _BN}},
            "strategy": _S}},
        "output": {"anyOf": [{"type": "object", "additionalProperties": False,
                              "required": ["ok", "strategy", "allowed", "required", "route", "do_not_call", "note"],
                              "properties": {
                                  "ok": {"enum": [True]}, "strategy": {"enum": list(STRATEGIES)}, "allowed": _B,
                                  "required": {"type": "object", "required": ["option_level", "account_types",
                                                                              "retirement_allowed"]},
                                  "route": _LS, "do_not_call": _LS, "unknowns": _LS, "reviewable": _BN,
                                  "notes": _LS, "note": _S,
                                  "alternative": {"type": "object", "required": ["how", "allowed", "risk"]}}},
                             _ERR]},
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
    """Minimal JSON Schema check used by --selftest (type, enum, required, properties, items, bounds)."""
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
    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append("%s: below minimum" % path)
        if "maximum" in schema and value > schema["maximum"]:
            errs.append("%s: above maximum" % path)
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
        ops[name] = {k: dict(v, **{"$schema": "https://json-schema.org/draft/2020-12/schema"})
                     for k, v in pair.items()}
    return {"script": SCRIPT, "version": VERSION, "ops": ops}


_MINI_INVENTORY = {"snapshot_captured_at": "2026-09-21", "absent_referenced": ["replace_option_order"],
                   "tools": {"get_accounts": "read", "get_portfolio": "read", "review_equity_order": "simulate",
                             "place_equity_order": "money", "cancel_equity_order": "cancel"}}
EXAMPLES = [
    ("capability", {"account": {"type": "cash", "option_level": "option_level_2", "retirement": False},
                    "strategy": "vertical_spread"},
     {"ok": True, "allowed": False,
      "required": {"option_level": "option_level_3", "account_types": ["margin", "limited_margin"],
                   "retirement_allowed": False},
      "route": ["get_limited_margin_upgrade_info", "user completes the limited-margin upgrade", "re-fetch get_accounts",
                "get_option_level_upgrade_info"],
      "do_not_call": ["get_option_level_upgrade_info", "review_option_order"]}),
    ("capability", {"account": {"type": "limited_margin", "option_level": "option_level_3", "retirement": False},
                    "strategy": "debit_call_spread"},
     {"ok": True, "strategy": "vertical_spread", "allowed": True, "route": [],
      "do_not_call": ["get_option_level_upgrade_info", "get_limited_margin_upgrade_info"]}),
    ("capability", {"account": {"type": "margin", "option_level": "option_level_2", "retirement": False},
                    "strategy": "iron_condor"},
     {"ok": True, "allowed": False, "route": ["get_option_level_upgrade_info", "user completes the options application",
                                              "re-fetch get_accounts"]}),
    ("capability", {"account": {"type": "cash", "option_level": None, "retirement": False}, "strategy": "long_call"},
     {"ok": True, "allowed": False, "route": ["get_option_level_upgrade_info", "user completes the options application",
                                              "re-fetch get_accounts"], "do_not_call": ["review_option_order"]}),
    ("capability", {"account": {"type": "cash", "option_level": "option_level_2", "retirement": True},
                    "strategy": "roll_single_order"},
     {"ok": True, "allowed": False, "route": [], "alternative": {"allowed": True}}),
    ("capability", {"account": {"type": "margin", "option_level": "option_level_3", "retirement": None},
                    "strategy": "calendar"},
     {"ok": True, "allowed": False, "unknowns": ["account.retirement"]}),
    ("capability", {"account": {"type": "cash", "option_level": "", "has_crypto_account": None}, "strategy": "crypto"},
     {"ok": True, "allowed": False, "unknowns": ["account.has_crypto_account"]}),
    ("capability", {"account": {"type": "cash"}, "strategy": "butterfly_of_doom"},
     {"ok": False, "errors": [{"code": "BAD_VALUE", "field": "strategy"}]}),
    ("inventory", {"seen_tools": ["mcp__robinhood-trading__get_accounts", "get_portfolio", "review_equity_order",
                                  "place_equity_order", "cancel_equity_order"], "inventory": _MINI_INVENTORY},
     {"ok": True, "status": "match", "known": 5, "new": [], "missing": [], "snapshot_date": "2026-09-21"}),
    ("inventory", {"seen_tools": ["get_accounts", "get_portfolio", "review_equity_order", "place_equity_order",
                                  "transfer_funds", "replace_option_order", "get_new_thing"],
                   "inventory": _MINI_INVENTORY},
     {"ok": True, "status": "drift", "new": ["get_new_thing", "replace_option_order", "transfer_funds"],
      "missing": ["cancel_equity_order"], "new_money_like": ["replace_option_order", "transfer_funds"],
      "new_money_like_guarded": ["replace_option_order"], "new_money_like_unguarded": ["transfer_funds"],
      "previously_absent_now_visible": ["replace_option_order"]}),
    ("inventory", {"seen_tools": [], "inventory": _MINI_INVENTORY},
     {"ok": False, "errors": [{"code": "NO_TOOLS_SEEN"}]}),
    ("selftest", {"guard_path": "/nonexistent/robinhood-skills-doctor/hooks/guard.sh"},
     {"ok": True, "guard": "not_found", "exit_code": None, "never_calls_mcp": True, "order_mode_line_seen": False}),
    ("selftest", {"guard_path": "/nonexistent/robinhood-skills-doctor/hooks/guard.sh", "order_mode_line_seen": True},
     {"ok": True, "guard": "not_found", "order_mode_line_seen": True}),
    ("selftest", {"guard_path": "/nonexistent/guard.sh", "order_mode_line_seen": "yes"},
     {"ok": False, "errors": [{"code": "BAD_VALUE", "field": "order_mode_line_seen"}]}),
    ("selftest", {"guard_path": "/nonexistent/guard.sh", "tools": ["cancel_equity_order"]},
     {"ok": False, "errors": [{"code": "BAD_VALUE", "field": "tools[0]"}]}),
]


def _subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
            _subset(e, a) for e, a in zip(expected, actual))
    return expected == actual and type(expected) is type(actual)


def selftest():
    failures = []
    for i, (op, inp, expected) in enumerate(EXAMPLES):
        problems = []
        out = run(op, inp)
        if out.get("ok"):
            problems += schema_errors(inp, SCHEMAS[op]["input"])
        problems += schema_errors(out, SCHEMAS[op]["output"])
        if not _subset(expected, out):
            problems.append("output mismatch: %s" % json.dumps(out, sort_keys=True))
        if problems:
            failures.append({"case": i, "op": op, "problems": problems})
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
        print(json.dumps(_err("MISSING_OP", "op", "usage: doctor.py selftest|inventory|capability < input.json")))
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
