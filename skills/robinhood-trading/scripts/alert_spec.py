#!/usr/bin/env python3
# synced from shared/scripts/alert_spec.py; do not edit
"""alert_spec.py - turn an alert intent into exact create_alert parameters, or refuse clearly.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: create_alert has 30 condition types in three shapes that look alike and behave
differently. "Alert me when the price crosses the 50-day SMA" is price_crosses_sma (no threshold);
sma_crosses is the SMA's own value crossing a number, and a mixed-up sibling still creates a
well-formed but wrong alert that pings the user's phone at the wrong time. Crypto tickers such as
BTC and ETH are also ETF tickers, and without asset_class an alert resolves to the equity first.
This script picks the shape, builds the indicator object, and refuses what an alert cannot
express (MA-vs-MA crosses, percent change, volume, option contracts, indexes).

Shapes (create_alert schema, verified 2026-09-22; not update_alert's condition families,
which group by indicator and are returned as update_group):
  price_above|below|crosses            threshold, no indicator
  sma|ema|vwap|rsi _above|below|crosses  threshold AND indicator (the indicator's own value)
  price_*_sma|ema|vwap, price_above_boll_upper, price_below_boll_lower, price_crosses_boll_mid,
  macd_above|below|crosses_signal      indicator only, NO threshold (price or MACD vs a line)
Bars: 5m 300, 10m 600, 1h 3600, 1d 86400, 1w 604800, 30d 2592000 (VWAP: 300 only).
Crypto supports price conditions only. asset_class is always emitted.

Usage:
    python3 alert_spec.py run|dedupe < input.json > output.json
    python3 alert_spec.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network, no file writes; one JSON object out; exit 0
whenever JSON was printed, exit 1 only on a crash.
"""

import json
import re
import sys
from decimal import Decimal, InvalidOperation

VERSION = "2.0.0"
SCRIPT = "alert_spec"

PRICE_KINDS = ("price_above", "price_below", "price_crosses")
VALUE_KINDS = tuple("%s_%s" % (ind, v) for ind in ("sma", "ema", "vwap", "rsi") for v in ("above", "below", "crosses"))
LINE_KINDS = tuple("price_%s_%s" % (v, ind) for ind in ("sma", "ema", "vwap") for v in ("above", "below", "crosses")) + (
    "price_above_boll_upper", "price_below_boll_lower", "price_crosses_boll_mid",
    "macd_above_signal", "macd_below_signal", "macd_crosses_signal")
ALL_KINDS = PRICE_KINDS + VALUE_KINDS + LINE_KINDS

BARS = {"5m": 300, "10m": 600, "1h": 3600, "1d": 86400, "1w": 604800, "30d": 2592000}
BAR_ALIASES = {"5min": "5m", "10min": "10m", "hour": "1h", "hourly": "1h", "60m": "1h", "day": "1d",
               "daily": "1d", "week": "1w", "weekly": "1w"}
BAR_WORDS = {300: "5-minute", 600: "10-minute", 3600: "hourly", 86400: "daily", 604800: "weekly",
             2592000: "30-day"}

# Crypto tickers that are also US-listed securities (Grayscale's BTC and ETH mini-trust ETFs, and
# other exchange tickers). Without asset_class, create_alert resolves the symbol as an equity first.
# Callers may extend this list with "ambiguous_tickers" (for example the eval fixture's list).
AMBIGUOUS_CRYPTO = ("BTC", "ETH", "LTC", "BCH", "LINK", "COMP", "SOL", "SUI", "SEI", "APT", "ARB", "OP")
INDEX_SYMBOLS = ("SPX", "SPXW", "NDX", "DJI", "DJX", "VIX", "RUT", "XSP", "OEX")
OCC_OPTION = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")

NOT_EXPRESSIBLE = {
    "golden_cross": "a moving average crossing another moving average (golden or death cross)",
    "death_cross": "a moving average crossing another moving average (golden or death cross)",
    "ma_cross": "a moving average crossing another moving average",
    "sma_crosses_sma": "a moving average crossing another moving average",
    "ema_crosses_ema": "a moving average crossing another moving average",
    "sma_crosses_ema": "a moving average crossing another moving average",
    "percent_change": "a percent-change move",
    "pct_change": "a percent-change move",
    "price_change_pct": "a percent-change move",
    "volume": "a volume level",
    "volume_above": "a volume level",
    "volume_spike": "a volume level",
    "option_price": "an option contract's price",
    "index_level": "an index level",
    "trailing_stop": "a trailing stop (the connector has no trailing stops or trailing alerts)",
}
EXPRESSIBLE_NOTE = ("create_alert compares the market price or one indicator's value with a number or one line; "
                    "it has no condition for this")


class InputError(Exception):
    def __init__(self, code, field, msg):
        Exception.__init__(self, msg)
        self.code, self.field, self.msg = code, field, msg


def _err(code, field, msg):
    return {"ok": False, "errors": [{"code": code, "field": field, "msg": msg}]}


def indicator_family(kind):
    if kind in PRICE_KINDS:
        return None
    for fam in ("boll", "macd", "vwap", "sma", "ema", "rsi"):
        if fam in kind.split("_"):
            return fam
    return None


def _verb(kind):
    for v in ("above", "below", "crosses"):
        if v in kind.split("_"):
            return v
    return "crosses"


def direction_word(kind):
    return {"above": "rises above", "below": "falls below"}.get(_verb(kind), "crosses")


def _pos_int(value, field):
    if isinstance(value, bool):
        raise InputError("BAD_VALUE", field, "%s must be a whole number" % field)
    if isinstance(value, int):
        n = value
    elif isinstance(value, str) and re.match(r"^\s*\d+\s*$", value):
        n = int(value)
    else:
        raise InputError("BAD_VALUE", field, "%s must be a whole number" % field)
    if n < 1:
        raise InputError("BAD_VALUE", field, "%s must be at least 1" % field)
    return n


def _decimal_string(value, field):
    if isinstance(value, bool) or value is None:
        raise InputError("BAD_THRESHOLD", field, "threshold must be a decimal string like \"195.00\"")
    text = repr(value) if isinstance(value, float) else str(value).strip()
    if not re.match(r"^[+-]?(\d+(\.\d*)?|\.\d+)$", text):
        raise InputError("BAD_THRESHOLD", field, "threshold must be a plain decimal like \"195.00\" (got %r)" % value)
    return text, Decimal(text)


def _norm(text):
    d = Decimal(str(text))
    out = format(d, "f")
    if "." in out:
        out = out.rstrip("0").rstrip(".")
    return out or "0"


def _interval(intent, fam):
    if "interval_secs" in intent and intent["interval_secs"] is not None:
        secs = intent["interval_secs"]
        if isinstance(secs, bool) or not isinstance(secs, int) or secs not in BAR_WORDS:
            raise InputError("BAD_BAR", "intent.interval_secs",
                             "interval_secs must be one of 300, 600, 3600, 86400, 604800, 2592000")
    elif intent.get("bar") is not None:
        bar = str(intent["bar"]).strip().lower()
        bar = BAR_ALIASES.get(bar, bar)
        if bar not in BARS:
            raise InputError("BAD_BAR", "intent.bar", "bar must be one of 5m, 10m, 1h, 1d, 1w, 30d")
        secs = BARS[bar]
    elif fam == "vwap":
        secs = 300
    else:
        raise InputError("MISSING_FIELD", "intent.bar", "which bar size (5m, 10m, 1h, 1d, 1w or 30d)? Ask the user")
    if fam == "vwap" and secs != 300:
        raise InputError("VWAP_300_ONLY", "intent.bar", "VWAP alerts support 5-minute bars (300 s) only")
    return secs


def build_indicator(intent, fam):
    secs = _interval(intent, fam)
    if fam in ("sma", "ema", "rsi"):
        if intent.get("period") is None:
            raise InputError("MISSING_FIELD", "intent.period", "how many bars for the %s? Ask the user (do not "
                                                                "assume the connector's default of 9 or 14)" % fam.upper())
        return {"period": _pos_int(intent["period"], "intent.period"), "interval_secs": secs}
    if fam == "vwap":
        return {"interval_secs": secs}
    if fam == "macd":
        vals = {}
        for key in ("fast_period", "slow_period", "signal_period"):
            if intent.get(key) is None:
                raise InputError("MISSING_FIELD", "intent." + key,
                                 "MACD needs fast_period, slow_period and signal_period (commonly 12, 26, 9); ask "
                                 "the user")
            vals[key] = _pos_int(intent[key], "intent." + key)
        if vals["fast_period"] >= vals["slow_period"]:
            raise InputError("BAD_VALUE", "intent.fast_period", "the fast period must be shorter than the slow period")
        vals["interval_secs"] = secs
        return vals
    if fam == "boll":
        if intent.get("period") is None or intent.get("std_dev") is None or intent.get("ma_type") is None:
            raise InputError("MISSING_FIELD", "intent.period",
                             "Bollinger alerts need period, std_dev and ma_type (sma or ema); ask the user")
        std = intent["std_dev"]
        if isinstance(std, bool):
            raise InputError("BAD_VALUE", "intent.std_dev", "std_dev must be a number")
        try:
            std_d = Decimal(repr(std) if isinstance(std, float) else str(std).strip())
        except InvalidOperation:
            raise InputError("BAD_VALUE", "intent.std_dev", "std_dev must be a number")
        if not std_d.is_finite() or std_d <= 0:
            raise InputError("BAD_VALUE", "intent.std_dev", "std_dev must be greater than zero")
        ma = str(intent["ma_type"]).strip().lower()
        if ma not in ("sma", "ema"):
            raise InputError("BAD_VALUE", "intent.ma_type", "ma_type must be sma or ema")
        std_num = int(std_d) if std_d == std_d.to_integral_value() else float(std_d)
        return {"period": _pos_int(intent["period"], "intent.period"), "std_dev": std_num, "ma_type": ma,
                "interval_secs": secs}
    raise InputError("UNKNOWN_KIND", "intent.kind", "no indicator for this kind")


def explain(kind, symbol, asset_class, threshold, ind):
    verb = direction_word(kind)
    bar = BAR_WORDS.get(ind.get("interval_secs")) if ind else None
    if kind in PRICE_KINDS:
        return "Market price of %s %s $%s." % (symbol, verb, threshold)
    fam = indicator_family(kind)
    if fam in ("sma", "ema"):
        line = "the %d-bar %s %s" % (ind["period"], bar, fam.upper())
    elif fam == "vwap":
        line = "the 5-minute VWAP"
    elif fam == "rsi":
        line = "RSI(%d) on %s bars" % (ind["period"], bar)
    elif fam == "boll":
        band = {"price_above_boll_upper": "upper", "price_below_boll_lower": "lower",
                "price_crosses_boll_mid": "middle"}[kind]
        line = "the %s Bollinger band (%d-bar %s %s, %s std dev)" % (band, ind["period"], bar,
                                                                     ind["ma_type"].upper(), ind["std_dev"])
    else:
        line = "MACD(%d,%d,%d) on %s bars" % (ind["fast_period"], ind["slow_period"], ind["signal_period"], bar)
    if kind in VALUE_KINDS:
        own = line if fam == "rsi" else line + "'s own value"
        return "%s%s %s %s (the indicator's value vs your number, not price vs the line)." % (
            own[0].upper(), own[1:], verb, threshold)
    if fam == "macd":
        return "%s %s its signal line (no threshold)." % (line, verb)
    return "Market price %s %s%s (no threshold)." % (verb, line, "" if fam == "boll" else " line")


def dedupe_key(symbol, asset_class, kind, threshold, ind):
    parts = [symbol, asset_class, kind]
    if threshold is not None:
        parts.append(_norm(threshold))
    if ind:
        for key in ("period", "fast_period", "slow_period", "signal_period", "std_dev", "ma_type", "interval_secs"):
            if key in ind and ind[key] is not None:
                val = ind[key]
                parts.append(_norm(val) if isinstance(val, (int, float)) and not isinstance(val, bool) else str(val))
    return "|".join(str(p) for p in parts)


def op_run(data):
    symbol_raw = data.get("symbol")
    intent = data.get("intent")
    if not isinstance(symbol_raw, str) or not symbol_raw.strip():
        raise InputError("MISSING_FIELD", "symbol", "give the ticker or coin, e.g. NVDA or BTC")
    if not isinstance(intent, dict) or not intent.get("kind"):
        raise InputError("MISSING_FIELD", "intent.kind", "give intent.kind, e.g. price_below or price_crosses_sma")
    notes = []
    symbol = symbol_raw.strip().upper()
    kind = str(intent["kind"]).strip().lower()
    asset_class = data.get("asset_class")
    asset_class = asset_class.strip().lower() if isinstance(asset_class, str) else asset_class

    if kind in NOT_EXPRESSIBLE:
        raise InputError("NOT_EXPRESSIBLE", "intent.kind", "%s is not expressible as a Robinhood alert: %s"
                         % (NOT_EXPRESSIBLE[kind][0].upper() + NOT_EXPRESSIBLE[kind][1:], EXPRESSIBLE_NOTE))
    if asset_class in ("option", "options") or OCC_OPTION.match(symbol):
        raise InputError("NOT_EXPRESSIBLE", "symbol", "alerts on option contracts are not available; an alert on the "
                                                      "underlying's price is")
    if asset_class == "index" or symbol in INDEX_SYMBOLS or symbol.startswith("^"):
        raise InputError("NOT_EXPRESSIBLE", "symbol", "alerts on indexes are not available through create_alert")
    if kind not in ALL_KINDS:
        raise InputError("UNKNOWN_KIND", "intent.kind", "unknown kind %r; use one of the 30 create_alert condition "
                                                        "types" % kind)
    if symbol.endswith("-USD") and asset_class in (None, "crypto"):
        if asset_class is None:
            notes.append("asset_class set to crypto from the -USD pair form")
        asset_class = "crypto"
        symbol = symbol[:-4]
        notes.append("crypto alerts use the base code (%s), not the pair" % symbol)
    ambiguous = set(AMBIGUOUS_CRYPTO) | {str(x).strip().upper() for x in data.get("ambiguous_tickers") or []}
    if asset_class is None:
        if symbol in ambiguous:
            raise InputError("AMBIGUOUS_CRYPTO_TICKER", "asset_class",
                             "%s is both a crypto code and a US-listed ticker; say asset_class crypto (the coin) or "
                             "equity (the listed security)" % symbol)
        asset_class = "equity"
        notes.append("asset_class set to equity (pass crypto for a coin)")
    if asset_class not in ("equity", "crypto"):
        raise InputError("BAD_ENUM", "asset_class", "asset_class must be equity or crypto")
    if not re.match(r"^[A-Z0-9.\-]{1,15}$", symbol):
        raise InputError("BAD_SYMBOL", "symbol", "symbol must be a ticker like NVDA or a coin like BTC")
    if asset_class == "crypto" and kind not in PRICE_KINDS:
        raise InputError("CRYPTO_PRICE_ONLY", "intent.kind", "crypto alerts support price_above, price_below and "
                                                             "price_crosses only")

    has_threshold = intent.get("threshold") is not None
    fam = indicator_family(kind)
    threshold = None
    if kind in PRICE_KINDS or kind in VALUE_KINDS:
        if not has_threshold:
            raise InputError("THRESHOLD_REQUIRED", "intent.threshold",
                             "%s needs a threshold (the %s); ask the user for it"
                             % (kind, "trigger price" if kind in PRICE_KINDS else "indicator value"))
        threshold, tval = _decimal_string(intent["threshold"], "intent.threshold")
        if tval <= 0:
            raise InputError("BAD_THRESHOLD", "intent.threshold", "threshold must be greater than zero")
        if fam == "rsi" and tval > 100:
            raise InputError("BAD_THRESHOLD", "intent.threshold", "RSI runs from 0 to 100")
    elif has_threshold:
        sibling = "%s_%s" % (fam, _verb(kind)) if fam in ("sma", "ema", "vwap") else "price_above or price_below"
        raise InputError("THRESHOLD_NOT_ALLOWED", "intent.threshold",
                         "%s compares against a line and takes no threshold; for a number use %s" % (kind, sibling))
    indicator = None
    if kind in PRICE_KINDS:
        stray = [k for k in ("period", "bar", "interval_secs", "fast_period", "slow_period", "signal_period",
                             "std_dev", "ma_type") if intent.get(k) is not None]
        if stray:
            raise InputError("INDICATOR_NOT_ALLOWED", "intent." + stray[0],
                             "%s takes a price only; for price vs a moving average use price_%s_sma or price_%s_ema"
                             % (kind, kind.split("_")[1], kind.split("_")[1]))
    else:
        indicator = build_indicator(intent, fam)
        if fam == "vwap" and intent.get("bar") is None and intent.get("interval_secs") is None:
            notes.append("VWAP alerts use 5-minute bars (the only size supported)")

    params = {"symbol": symbol, "condition_type": kind}
    if threshold is not None:
        params["threshold"] = threshold
    if indicator is not None:
        params["indicator"] = indicator
    params["asset_class"] = asset_class
    out = {
        "ok": True,
        "params": params,
        "explain": explain(kind, symbol, asset_class, threshold, indicator),
        "dedupe_key": dedupe_key(symbol, asset_class, kind, threshold, indicator),
        # shape: how the condition reads (the table in alerts-watchlists.md). update_group: the indicator
        # group update_alert calls the condition family; condition_type may change only within it.
        "shape": "price" if kind in PRICE_KINDS else ("indicator_value" if kind in VALUE_KINDS else "price_vs_line"),
        "update_group": indicator_family(kind) or "price",
        "next": "get_alerts {symbol, asset_class} to dedupe, then a one-line confirmation, then create_alert. "
                "Alerts notify the user's phone; they do not trade.",
    }
    if notes:
        out["notes"] = notes
    return out


def _row_key(row):
    """Best-effort key for a get_alerts row. FIELDS.md records condition{target_price | indicator
    params}; the indicator key names inside condition are assumed to mirror create_alert's."""
    symbol = str(row.get("symbol", "")).strip().upper()
    asset = str(row.get("asset_class") or "").strip().lower() or "unknown"
    kind = str(row.get("condition_type", "")).strip().lower()
    cond = row.get("condition") if isinstance(row.get("condition"), dict) else {}
    threshold = cond.get("target_price", cond.get("threshold", row.get("threshold")))
    ind = cond.get("indicator") if isinstance(cond.get("indicator"), dict) else (
        row.get("indicator") if isinstance(row.get("indicator"), dict) else cond)
    ind = {k: v for k, v in ind.items() if k in ("period", "fast_period", "slow_period", "signal_period", "std_dev",
                                                 "ma_type", "interval_secs")}
    try:
        return dedupe_key(symbol, asset, kind, threshold if kind in PRICE_KINDS + VALUE_KINDS else None, ind or None)
    except (InvalidOperation, ValueError):
        return None


def op_dedupe(data):
    cand = data.get("candidate")
    rows = data.get("existing")
    if not isinstance(cand, dict) or not isinstance(rows, list):
        raise InputError("MISSING_FIELD", "candidate", "give candidate (the params from run) and existing (get_alerts rows)")
    ckey = _row_key({"symbol": cand.get("symbol"), "asset_class": cand.get("asset_class"),
                     "condition_type": cand.get("condition_type"), "threshold": cand.get("threshold"),
                     "indicator": cand.get("indicator")})
    prefix = "|".join((ckey or "").split("|")[:3])
    dupes, near = [], []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rkey = _row_key(row)
        entry = {"alert_id": row.get("alert_id"), "enabled": row.get("enabled"), "key": rkey}
        if rkey is not None and rkey == ckey:
            dupes.append(entry)
        elif rkey is not None and "|".join(rkey.split("|")[:3]) == prefix:
            near.append(entry)
    return {
        "ok": True,
        "key": ckey,
        "duplicates": dupes,
        "same_condition_other_level": near,
        "action": ("an identical alert exists; do not create another" if any(d["enabled"] is not False for d in dupes)
                   else "an identical alert exists but is disabled; offer update_alert {enabled: true} instead"
                   if dupes else "no identical alert; creating one is not a duplicate"),
        "assumption": "indicator key names inside get_alerts condition are assumed to match create_alert's",
    }


OPS = {"run": op_run, "dedupe": op_dedupe}


def run(op, data):
    if op not in OPS:
        return _err("UNKNOWN_OP", "op", "unknown op %r; expected one of %s" % (op, ", ".join(sorted(OPS))))
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
_INT = {"type": "integer"}
_ERR = {"type": "object", "required": ["ok", "errors"],
        "properties": {"ok": {"enum": [False]},
                       "errors": {"type": "array", "items": {"type": "object", "required": ["code", "field", "msg"],
                                                             "properties": {"code": _S, "field": _S, "msg": _S}}}}}
_INDICATOR = {"type": "object", "additionalProperties": False, "required": ["interval_secs"],
              "properties": {"period": _INT, "interval_secs": {"enum": sorted(BAR_WORDS)}, "fast_period": _INT,
                             "slow_period": _INT, "signal_period": _INT, "std_dev": {"type": "number"},
                             "ma_type": {"enum": ["sma", "ema"]}}}
_PARAMS = {"type": "object", "additionalProperties": False, "required": ["symbol", "condition_type", "asset_class"],
           "properties": {"symbol": _S, "condition_type": {"enum": list(ALL_KINDS)},
                          "threshold": {"type": "string", "pattern": r"^[+-]?(\d+(\.\d*)?|\.\d+)$"},
                          "indicator": _INDICATOR, "asset_class": {"enum": ["equity", "crypto"]}}}

SCHEMAS = {
    "run": {
        "input": {"type": "object", "required": ["symbol", "intent"], "additionalProperties": False,
                  "properties": {"symbol": _S, "asset_class": {"enum": ["equity", "crypto", "option", "index"]},
                                 "ambiguous_tickers": {"type": "array", "items": _S},
                                 "intent": {"type": "object", "required": ["kind"], "properties": {
                                     "kind": _S, "threshold": {"type": ["string", "number"]},
                                     "period": {"type": ["integer", "string"]}, "bar": _S,
                                     "interval_secs": _INT, "fast_period": {"type": ["integer", "string"]},
                                     "slow_period": {"type": ["integer", "string"]},
                                     "signal_period": {"type": ["integer", "string"]},
                                     "std_dev": {"type": ["number", "string"]}, "ma_type": {"enum": ["sma", "ema"]}}}}},
        "output": {"anyOf": [{"type": "object", "additionalProperties": False,
                              "required": ["ok", "params", "explain", "dedupe_key"],
                              "properties": {"ok": {"enum": [True]}, "params": _PARAMS, "explain": _S,
                                             "dedupe_key": _S,
                                             "shape": {"enum": ["price", "indicator_value", "price_vs_line"]},
                                             "update_group": {"enum": ["price", "sma", "ema", "vwap", "rsi",
                                                                       "boll", "macd"]},
                                             "next": _S, "notes": {"type": "array", "items": _S}}}, _ERR]},
    },
    "dedupe": {
        "input": {"type": "object", "required": ["candidate", "existing"],
                  "properties": {"candidate": {"type": "object"}, "existing": {"type": "array"}}},
        "output": {"anyOf": [{"type": "object", "required": ["ok", "duplicates", "same_condition_other_level", "action"],
                              "properties": {"ok": {"enum": [True]}, "key": {"type": ["string", "null"]},
                                             "duplicates": {"type": "array"},
                                             "same_condition_other_level": {"type": "array"},
                                             "action": _S, "assumption": _S}}, _ERR]},
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
    return {"script": SCRIPT, "version": VERSION, "ops": ops, "condition_types": list(ALL_KINDS),
            "ambiguous_crypto_tickers": list(AMBIGUOUS_CRYPTO)}


EXAMPLES = [
    ("run", {"symbol": "NVDA", "asset_class": "equity", "intent": {"kind": "price_crosses_sma", "period": 200, "bar": "1d"}},
     {"ok": True, "params": {"symbol": "NVDA", "condition_type": "price_crosses_sma",
                             "indicator": {"period": 200, "interval_secs": 86400}, "asset_class": "equity"},
      "explain": "Market price crosses the 200-bar daily SMA line (no threshold).",
      "dedupe_key": "NVDA|equity|price_crosses_sma|200|86400"}),
    ("run", {"symbol": "NVDA", "asset_class": "equity", "intent": {"kind": "price_below", "threshold": "150.00"}},
     {"ok": True, "params": {"symbol": "NVDA", "condition_type": "price_below", "threshold": "150.00",
                             "asset_class": "equity"}, "dedupe_key": "NVDA|equity|price_below|150"}),
    ("run", {"symbol": "AMD", "asset_class": "equity", "intent": {"kind": "sma_crosses", "period": 50, "bar": "1d"}},
     {"ok": False, "errors": [{"code": "THRESHOLD_REQUIRED"}]}),
    ("run", {"symbol": "SPY", "asset_class": "equity", "intent": {"kind": "vwap_above", "threshold": "500", "bar": "1h"}},
     {"ok": False, "errors": [{"code": "VWAP_300_ONLY"}]}),
    ("run", {"symbol": "BTC", "asset_class": "crypto", "intent": {"kind": "rsi_above", "threshold": "70", "period": 14,
                                                                  "bar": "1h"}},
     {"ok": False, "errors": [{"code": "CRYPTO_PRICE_ONLY"}]}),
    ("run", {"symbol": "AAPL", "asset_class": "equity", "intent": {"kind": "golden_cross"}},
     {"ok": False, "errors": [{"code": "NOT_EXPRESSIBLE"}]}),
    ("run", {"symbol": "BTC", "intent": {"kind": "price_below", "threshold": "60000"}},
     {"ok": False, "errors": [{"code": "AMBIGUOUS_CRYPTO_TICKER"}]}),
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
    for i, (op, inp, expected) in enumerate(EXAMPLES):
        problems = schema_errors(inp, SCHEMAS[op]["input"])
        out = run(op, inp)
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
        print(json.dumps(_err("MISSING_OP", "op", "usage: alert_spec.py run|dedupe < input.json")))
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
