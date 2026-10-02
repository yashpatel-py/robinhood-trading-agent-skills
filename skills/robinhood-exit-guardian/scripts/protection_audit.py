#!/usr/bin/env python3
"""protection_audit.py - which positions have a working exit, which have only an alert, which have nothing.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: "is this position protected?" has more wrong answers than right ones. A stop for
more shares than are left after another exit fills does nothing; a GFD exit disappears at the close;
a fractional remainder can never be covered by a stop or an OCO (whole shares only); a stop-market leg
can fill far below its price on a gap; stops and OCOs act in regular hours only; and a price alert
notifies the phone but sells nothing. An agent eyeballing orders gets these wrong in both directions.
This script does the bookkeeping the same way every time and says what it could not see.

Ops:
  audit   positions + open sell orders + OCOs + alerts (+ earnings, session) -> a status per position
          (protected | partial | backstop_only | unprotected | unknown), gap codes, dollars at stake,
          the report's first line, a table and the fixed footer.
  levels  the user's saved exit rules or their own levels + prices (+ ATR) -> stop/target prices, or
          {"ask": [...]} when a rule is UNSET or "ask". It never proposes a level of its own.

Coverage rule (the prose twin is references/formulas.md): only open stop, stop-limit and OCO sell
exits count. They are taken in the order a falling price reaches them (highest stop first; smaller
exit first on ties), and each one counts only if it fits in the shares still held at that point. An
exit bigger than what is left is assumed to be rejected, so it counts for nothing. Limit and market
sells are listed, and count toward "exits exceed position", but never as downside protection.

Usage:
    python3 protection_audit.py audit|levels < input.json > output.json
    python3 protection_audit.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network, no file writes; one JSON object out; exit 0
whenever JSON was printed, exit 1 only on a crash. Inputs carry account_last4, never full numbers.
"""

import json
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

VERSION = "2.0.0"
SCRIPT = "protection_audit"
MASK = "••••"
DOTS2 = "••"
MID = " · "
CENT = Decimal("0.01")
SUBPENNY = Decimal("0.0001")
OCO_MIN_DISTANCE = Decimal("0.0025")
OCO_MIN_GAP = Decimal("0.10")
DEFAULT_STALE_MINUTES = 15
DEFAULT_GTC_LIFETIME_DAYS = 90  # Robinhood equity GTC lifetime: unverified (VERIFY-D1); label it so
DEFAULT_GTC_WARN_DAYS = 7
DEFAULT_ATR_PERIOD = 14  # the connector's ATR default; a bare-number atr input is read as ATR(14)
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARNINGS_AM = time(8, 0)  # "am" reports land before the 09:30 open
EARNINGS_PM = time(16, 0)  # "pm" reports land after the close

OPEN_STATES = {
    "equity": ("new", "queued", "confirmed", "unconfirmed", "partially_filled"),
    "crypto": ("queued", "confirmed", "partially_filled"),
}
STOP_KINDS = ("stop_market", "stop_limit", "oco")
DOWNSIDE_ALERTS = ("price_below", "price_crosses")
READ_KEYS = ("positions", "equity_orders", "advanced_orders", "crypto_positions", "crypto_orders")
INCOMPLETE = ("partial", "failed", "not_read")
READ_VALUES = ("complete", "partial", "failed", "not_enabled", "not_read", "not_applicable")
STATUSES = ("unprotected", "unknown", "backstop_only", "partial", "protected")
UNSET = "UNSET"

# Gap codes in the order they are reported. SPEC_GAP_CODES is the build spec's list; the others cover
# stop-limits, unknown or expiring time in force, shares pledged as option collateral, and reads the kit
# could not complete (connector rule R26 and principle 4: unknown is never "clear").
SPEC_GAP_CODES = (
    "UNCOVERED_SHARES", "EXITS_EXCEED_POSITION", "GFD_EXPIRES_TODAY", "FRACTIONAL_REMAINDER",
    "STOP_MARKET_GAP_RISK", "NO_EXTENDED_HOURS_COVERAGE", "ALERT_DISABLED", "ALERT_NOTIFIES_ONLY",
    "CRYPTO_STOP_DAY_ONLY", "CRYPTO_GTC_90D", "NOT_AGENT_TRADABLE", "EARNINGS_BEFORE_NEXT_SESSION", "STALE_QUOTE",
)
GAP_CODES = (
    "UNCOVERED_SHARES",
    "EXITS_EXCEED_POSITION",
    "GFD_EXPIRES_TODAY",
    "TIF_UNKNOWN",
    "GTC_EXPIRING_SOON",
    "FRACTIONAL_REMAINDER",
    "COLLATERAL_SHARES",
    "STOP_MARKET_GAP_RISK",
    "STOP_LIMIT_MAY_NOT_FILL",
    "NO_EXTENDED_HOURS_COVERAGE",
    "CRYPTO_STOP_DAY_ONLY",
    "CRYPTO_GTC_90D",
    "EARNINGS_BEFORE_NEXT_SESSION",
    "NOT_AGENT_TRADABLE",
    "ALERT_DISABLED",
    "ALERT_NOTIFIES_ONLY",
    "OCO_UNREADABLE",
    "READ_INCOMPLETE",
    "HELD_SHARES_UNEXPLAINED",
    "STALE_QUOTE",
)
_GAP_RANK = dict((c, i) for i, c in enumerate(GAP_CODES))

FOOTER = (
    "Alerts notify your phone; they do not sell. Stock stops and OCOs act in regular hours only "
    "(09:30–16:00 ET); crypto stop orders can trigger at any hour. A triggered stop-market order sells at "
    "market: a gap or fast move can fill far below the stop. Nothing was placed or changed by this check."
)
LEVELS_NOTE = (
    "Levels come only from your saved rules or your own numbers; this script never picks one. "
    "A rule that is UNSET or 'ask' returns ask instead of a price."
)

try:  # zoneinfo is stdlib on 3.9+, but tzdata can be missing; the embedded US rule is the fallback
    from zoneinfo import ZoneInfo

    try:
        _ZONE = ZoneInfo("America/New_York")
    except Exception:  # ZoneInfoNotFoundError and friends
        _ZONE = None
except ImportError:  # pragma: no cover - only on stripped-down interpreters
    _ZONE = None


class InputError(Exception):
    def __init__(self, code, field, msg):
        Exception.__init__(self, msg)
        self.code, self.field, self.msg = code, field, msg


def _err(code, field, msg):
    return {"ok": False, "errors": [{"code": code, "field": field, "msg": msg}]}


# ---------------------------------------------------------------------------------------------
# Parsing and formatting
# ---------------------------------------------------------------------------------------------
def _dec(value, field, required=True, positive=False, nonneg=False):
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise InputError("MISSING_FIELD", field, "%s is required" % field)
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise InputError("BAD_DECIMAL", field, "%s must be a decimal string such as \"12.50\"" % field)
    try:
        d = Decimal(repr(value)) if isinstance(value, float) else Decimal(str(value).strip())
    except InvalidOperation:
        raise InputError("BAD_DECIMAL", field, "%s must be a decimal string such as \"12.50\"" % field) from None
    if not d.is_finite():
        raise InputError("BAD_DECIMAL", field, "%s must be a finite number" % field)
    if positive and d <= 0:
        raise InputError("NOT_POSITIVE", field, "%s must be greater than 0" % field)
    if nonneg and d < 0:
        raise InputError("NEGATIVE", field, "%s cannot be negative" % field)
    return d


def _round_price(d):
    step = CENT if abs(d) >= 1 else SUBPENNY
    return d.quantize(step, rounding=ROUND_HALF_UP)


def money(d):
    return str(d.quantize(CENT, rounding=ROUND_HALF_UP))


def fmt_money(d):
    return "$" + "{:,.2f}".format(d.quantize(CENT, rounding=ROUND_HALF_UP))


def fmt_qty(d):
    s = format(d.normalize(), "f")
    return s if s != "-0" else "0"


def fmt_px(d):
    """140.00 -> '140'; 142.5 -> '142.50'; 0.1234 -> '0.1234' (the order-table style)."""
    if d == d.to_integral_value():
        return "{:,}".format(int(d))
    q = d.quantize(CENT) if d.as_tuple().exponent >= -2 else d
    return "{:,}".format(q) if abs(d) >= 1 else fmt_qty(d)


def pct1(d):
    return float(d.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


_ISO = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?\s*(Z|z|[+-]\d{2}:?\d{2})$"
)


def parse_ts(value, field, required=True):
    if value is None or value == "":
        if required:
            raise InputError("MISSING_FIELD", field, "%s is required (ISO 8601 with an offset)" % field)
        return None
    if not isinstance(value, str):
        raise InputError("BAD_TIME", field, "%s must be an ISO 8601 string with an offset" % field)
    m = _ISO.match(value.strip())
    if not m:
        raise InputError("BAD_TIME", field, "%s must be ISO 8601 with an offset, e.g. 2026-11-17T01:05:00Z" % field)
    y, mo, d, hh, mi, ss, frac, off = m.groups()
    micro = int((frac or "0")[:6].ljust(6, "0"))
    if off in ("Z", "z"):
        tz = timezone.utc
    else:
        sign = -1 if off[0] == "-" else 1
        digits = off[1:].replace(":", "")
        tz = timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
    try:
        dt = datetime(int(y), int(mo), int(d), int(hh), int(mi), int(ss or 0), micro, tzinfo=tz)
    except ValueError:
        raise InputError("BAD_TIME", field, "%s is not a real date and time" % field) from None
    return dt.astimezone(timezone.utc)


def parse_date(value, field):
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", value.strip()):
        raise InputError("BAD_DATE", field, "%s must be YYYY-MM-DD" % field)
    try:
        return date(*[int(x) for x in value.strip().split("-")])
    except ValueError:
        raise InputError("BAD_DATE", field, "%s is not a real date" % field) from None


def _second_sunday_march(year):
    d = date(year, 3, 1)
    return d + timedelta(days=(6 - d.weekday()) % 7 + 7)


def _first_sunday_november(year):
    d = date(year, 11, 1)
    return d + timedelta(days=(6 - d.weekday()) % 7)


def _embedded_is_dst_utc(dt_utc):
    y = dt_utc.year
    start = datetime.combine(_second_sunday_march(y), time(7, 0), timezone.utc)  # 02:00 EST
    end = datetime.combine(_first_sunday_november(y), time(6, 0), timezone.utc)  # 02:00 EDT
    return start <= dt_utc < end


def to_et(dt_utc):
    if _ZONE is not None:
        return dt_utc.astimezone(_ZONE)
    hours = -4 if _embedded_is_dst_utc(dt_utc) else -5
    return dt_utc.astimezone(timezone(timedelta(hours=hours)))


def et_to_utc(d, t):
    """US Eastern wall time (at or after 02:00, so never ambiguous here) -> aware UTC."""
    if _ZONE is not None:
        return datetime.combine(d, t, _ZONE).astimezone(timezone.utc)
    dst = _second_sunday_march(d.year) <= d < _first_sunday_november(d.year)
    return datetime.combine(d, t, timezone(timedelta(hours=-4 if dst else -5))).astimezone(timezone.utc)


def fmt_et(dt_utc):
    et = to_et(dt_utc)
    return "%s ET %s" % (et.strftime("%H:%M"), et.strftime("%a %Y-%m-%d"))


def _last4(value, field):
    if not isinstance(value, str) or not re.match(r"^[A-Za-z0-9]{4}$", value.strip()):
        if isinstance(value, str) and re.match(r"^[A-Za-z0-9]{5,}$", value.strip()):
            raise InputError("FULL_ACCOUNT_NUMBER", field,
                             "pass only the last 4 characters of the account number (scripts never see full numbers)")
        raise InputError("BAD_ACCOUNT", field, "%s must be the last 4 characters of account_number" % field)
    return value.strip().upper()


def norm_symbol(value, asset_class, field):
    if not isinstance(value, str) or not value.strip():
        raise InputError("MISSING_FIELD", field, "%s is required" % field)
    s = value.strip().upper()
    if asset_class == "crypto":
        s = s.replace("-", "").replace("/", "")
        if len(s) > 3 and s.endswith("USD"):
            s = s[:-3]
    return s


def _asset_class(value, field, default="equity"):
    ac = (value or default)
    if not isinstance(ac, str) or ac.strip().lower() not in ("equity", "crypto"):
        raise InputError("BAD_ASSET_CLASS", field,
                         "asset_class must be equity or crypto (options: robinhood-options-monitor)")
    return ac.strip().lower()


def _bool(value, field, default=None):
    if value is None:
        return default
    if not isinstance(value, bool):
        raise InputError("BAD_BOOL", field, "%s must be true or false" % field)
    return value


def _lower(value):
    return value.strip().lower() if isinstance(value, str) and value.strip() else None


# ---------------------------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------------------------
class Exit(object):
    __slots__ = ("kind", "qty", "trigger", "limit", "tif", "created", "account", "asset", "symbol", "oid",
                 "id_short", "seq", "effective")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def word(self):
        if self.kind == "oco":
            return "OCO"
        if self.asset == "crypto":
            return {"stop_market": "stop order", "stop_limit": "stop limit order", "limit": "limit sell",
                    "market": "market sell"}.get(self.kind, self.kind)
        return {"stop_market": "stop", "stop_limit": "stop-limit", "limit": "limit sell",
                "market": "market sell"}.get(self.kind, self.kind)

    def tif_text(self):
        # A missing time in force is unknown, for crypto too: gfd is what preview_crypto_order *sends* when
        # none is given, which says nothing about how a listed order reports it.
        return self.tif.upper() if self.tif else "TIF?"

    def unit(self):
        return "sh" if self.asset == "equity" else self.symbol

    def label(self):
        q = "%s %s" % (fmt_qty(self.qty), self.unit())
        if self.kind == "oco":
            ident = (DOTS2 + self.id_short + " ") if self.id_short else ""
            return "OCO %s%s %s %s/%s" % (ident, q, self.tif_text(), fmt_px(self.limit), fmt_px(self.trigger))
        if self.kind == "stop_limit":
            lim = "/%s" % fmt_px(self.limit) if self.limit is not None else ""
            return "%s %s %s %s%s" % (self.word(), q, self.tif_text(), fmt_px(self.trigger), lim)
        if self.kind == "stop_market":
            return "%s %s %s %s" % (self.word(), q, self.tif_text(), fmt_px(self.trigger))
        if self.kind == "limit" and self.limit is not None:
            return "%s %s %s %s" % (self.word(), q, self.tif_text(), fmt_px(self.limit))
        return "%s %s" % (self.word(), q)

    def short(self):
        return "%s %s" % (self.word(), fmt_qty(self.qty))


def norm_order_type(o, asset, field):
    t = _lower(o.get("type"))
    trig = _lower(o.get("trigger"))
    if t in ("stop_loss", "stop", "stop_market"):
        return "stop_market"
    if t == "stop_limit":
        return "stop_limit"
    if t == "market":
        return "stop_market" if trig == "stop" else "market"
    if t == "limit":
        return "stop_limit" if trig == "stop" else "limit"
    raise InputError("BAD_ORDER_TYPE", field, "type %r is not an order type this audit reads" % o.get("type"))


def _reads_for(acct):
    return acct["read"] if acct else dict((k, "complete") for k in READ_KEYS)


def parse_accounts(data, notes):
    raw = data.get("accounts")
    if raw is None:
        notes.append("no accounts list was given, so read completeness is assumed; the status can't be CLEAR")
        return None
    if not isinstance(raw, list):
        raise InputError("BAD_INPUT", "accounts", "accounts must be a list")
    out = {}
    for i, a in enumerate(raw):
        f = "accounts[%d]" % i
        if not isinstance(a, dict):
            raise InputError("BAD_INPUT", f, "each account is an object")
        last4 = _last4(a.get("account_last4"), f + ".account_last4")
        read_in = a.get("read") if isinstance(a.get("read"), dict) else {}
        agentic = _bool(a.get("agentic"), f + ".agentic", False)
        read = {}
        for k in READ_KEYS:
            # Every read defaults to not_read, crypto included: get_crypto_positions and get_crypto_orders take
            # any account's rhs_account_number, so crypto outside the Agentic account is readable. Only the
            # caller can say a read had nothing to read (not_applicable: the account has no linked crypto
            # account, i.e. an empty rhc_account_number); a skipped read must never pass as "none held".
            v = read_in.get(k, "not_read")
            if v not in READ_VALUES:
                raise InputError("BAD_ENUM", "%s.read.%s" % (f, k), "must be one of %s" % ", ".join(READ_VALUES))
            read[k] = v
        label = a.get("label") if isinstance(a.get("label"), str) and a.get("label").strip() else (
            "Agentic" if agentic else "Account")
        label = label.strip()
        if label.islower():  # a raw enum such as "individual" reads better capitalized
            label = label[0].upper() + label[1:]
        out[last4] = {"last4": last4, "label": label, "agentic": agentic,
                      "in_scope": _bool(a.get("in_scope"), f + ".in_scope", True), "read": read}
    return out


def _session(data, now, notes):
    sess = data.get("session") if isinstance(data.get("session"), dict) else {}
    regular = data.get("session_regular_open")
    if regular is None:
        regular = sess.get("in_regular_session")
    regular = _bool(regular, "session_regular_open")
    if regular is None:
        et = to_et(now)
        regular = et.weekday() < 5 and REGULAR_OPEN <= et.time() < REGULAR_CLOSE
        notes.append("session not given; estimated from the ET clock without the holiday calendar "
                     "(pass rh_time.py session output)")
    nxt = data.get("next_regular_open_et") or sess.get("next_regular_open_et")
    nxt = parse_ts(nxt, "next_regular_open_et", required=False)
    last = parse_ts(data.get("last_regular_open_et"), "last_regular_open_et", required=False)
    last_estimated = False
    if last is None:
        d = to_et(now).date()
        for back in range(0, 8):
            cand = d - timedelta(days=back)
            if cand.weekday() < 5 and et_to_utc(cand, REGULAR_OPEN) <= now:
                last = et_to_utc(cand, REGULAR_OPEN)
                last_estimated = True
                break
    return regular, nxt, last, last_estimated


def _earnings_hit(info, sym, last_open, next_open):
    """True when the report lands after the last regular open and at or before the next one."""
    d = parse_date(info.get("date"), "earnings.%s.date" % sym)
    timing = _lower(info.get("timing"))
    if timing not in (None, "am", "pm"):
        raise InputError("BAD_ENUM", "earnings.%s.timing" % sym, "timing must be am, pm or null")
    early = et_to_utc(d, EARNINGS_PM if timing == "pm" else EARNINGS_AM)
    late = et_to_utc(d, EARNINGS_AM if timing == "am" else EARNINGS_PM)
    return early <= next_open and late >= last_open, d, timing


def op_audit(data):
    notes, not_checked = [], []
    now = parse_ts(data.get("now"), "now")
    regular_open, next_open, last_open, last_estimated = _session(data, now, notes)
    stale_min = data.get("stale_after_minutes", DEFAULT_STALE_MINUTES)
    life_days = data.get("gtc_lifetime_days", DEFAULT_GTC_LIFETIME_DAYS)
    warn_days = data.get("gtc_warn_days", DEFAULT_GTC_WARN_DAYS)
    for name, v in (("stale_after_minutes", stale_min), ("gtc_lifetime_days", life_days), ("gtc_warn_days", warn_days)):
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise InputError("BAD_INT", name, "%s must be a whole number >= 0" % name)
    accounts = parse_accounts(data, notes)
    alerts_read = data.get("alerts_read", "complete" if "alerts" in data else "not_read")
    if alerts_read not in ("complete", "partial", "failed", "not_read"):
        raise InputError("BAD_ENUM", "alerts_read", "alerts_read must be complete, partial, failed or not_read")

    # -- positions ---------------------------------------------------------------------------
    positions = []
    raw_positions = data.get("positions")
    if not isinstance(raw_positions, list):
        raise InputError("MISSING_FIELD", "positions", "positions must be a list (it may be empty)")
    seen = set()
    for i, p in enumerate(raw_positions):
        f = "positions[%d]" % i
        if not isinstance(p, dict):
            raise InputError("BAD_INPUT", f, "each position is an object")
        last4 = _last4(p.get("account_last4"), f + ".account_last4")
        acct = accounts.get(last4) if accounts is not None else None
        if accounts is not None and acct is None:
            raise InputError("UNKNOWN_ACCOUNT", f + ".account_last4",
                             "••••%s is not in accounts; list every account the positions come from" % last4)
        if acct is not None and not acct["in_scope"]:
            notes.append("%s position in %s%s skipped: that account is out of scope" % (p.get("symbol"), MASK, last4))
            continue
        asset = _asset_class(p.get("asset_class"), f + ".asset_class")
        sym = norm_symbol(p.get("symbol"), asset, f + ".symbol")
        qty = _dec(p.get("quantity"), f + ".quantity", nonneg=True)
        if qty == 0:
            notes.append("%s in %s%s has zero quantity; skipped" % (sym, MASK, last4))
            continue
        key = (last4, asset, sym)
        if key in seen:
            raise InputError("DUPLICATE_POSITION", f, "%s appears twice for %s%s; merge the rows" % (sym, MASK, last4))
        seen.add(key)
        agentic = _bool(p.get("agentic"), f + ".agentic", acct["agentic"] if acct else None)
        if agentic is None:
            raise InputError("MISSING_FIELD", f + ".agentic", "say whether the account is the Agentic one")
        label = p.get("account_label") if isinstance(p.get("account_label"), str) and p.get("account_label").strip() \
            else (acct["label"] if acct else ("Agentic" if agentic else "Account"))
        positions.append({
            "key": key, "last4": last4, "asset": asset, "symbol": sym, "qty": qty, "agentic": agentic,
            "label": label.strip(), "acct": acct,
            "held": _dec(p.get("held_for_orders"), f + ".held_for_orders", required=False, nonneg=True),
            "sellable": _dec(p.get("sellable"), f + ".sellable", required=False, nonneg=True),
            "collateral": _dec(p.get("held_for_collateral"), f + ".held_for_collateral", required=False, nonneg=True),
            "price": _dec(p.get("price"), f + ".price", required=False, positive=True),
            "price_as_of": parse_ts(p.get("price_as_of"), f + ".price_as_of", required=False),
        })

    # -- OCOs ----------------------------------------------------------------------------------
    exits = {}
    ocos = []
    raw_adv = data.get("advanced_orders") or []
    if not isinstance(raw_adv, list):
        raise InputError("BAD_INPUT", "advanced_orders", "advanced_orders must be a list")
    for i, a in enumerate(raw_adv):
        f = "advanced_orders[%d]" % i
        if not isinstance(a, dict):
            raise InputError("BAD_INPUT", f, "each advanced order is an object")
        last4 = _last4(a.get("account_last4"), f + ".account_last4")
        sym = norm_symbol(a.get("symbol"), "equity", f + ".symbol")
        side = _lower(a.get("side"))
        id_short = a.get("id_short") if isinstance(a.get("id_short"), str) else None
        id_short = re.sub(r"[^A-Za-z0-9]", "", id_short)[:6] if id_short else None
        if side != "sell":
            notes.append("OCO %s%s on %s is a %s OCO; only sell OCOs protect a long position" % (
                DOTS2, id_short or "?", sym, side or "unknown-side"))
            continue
        active = _bool(a.get("active"), f + ".active")
        e = Exit(kind="oco", qty=_dec(a.get("quantity"), f + ".quantity", positive=True),
                 trigger=_dec(a.get("stop_loss_stop_price"), f + ".stop_loss_stop_price", positive=True),
                 limit=_dec(a.get("take_profit_limit_price"), f + ".take_profit_limit_price", positive=True),
                 tif=_lower(a.get("time_in_force")),
                 created=parse_ts(a.get("created_at"), f + ".created_at", required=False),
                 account=last4, asset="equity", symbol=sym, id_short=id_short, seq=100000 + i)
        legs = a.get("leg_order_ids") or []
        if not isinstance(legs, list) or not all(isinstance(x, str) for x in legs):
            raise InputError("BAD_INPUT", f + ".leg_order_ids", "leg_order_ids must be a list of order ids")
        if active is not True:
            notes.append("OCO %s%s on %s: not active (or its state was not given); not counted" % (
                DOTS2, id_short or "?", sym))
            continue
        ocos.append((e, set(legs), {"stop": False, "limit": False}))
        exits.setdefault((last4, "equity", sym), []).append(e)

    # -- open orders -----------------------------------------------------------------------------
    raw_orders = data.get("open_orders") or []
    if not isinstance(raw_orders, list):
        raise InputError("BAD_INPUT", "open_orders", "open_orders must be a list")
    for i, o in enumerate(raw_orders):
        f = "open_orders[%d]" % i
        if not isinstance(o, dict):
            raise InputError("BAD_INPUT", f, "each order is an object")
        last4 = _last4(o.get("account_last4"), f + ".account_last4")
        asset = _asset_class(o.get("asset_class"), f + ".asset_class")
        sym = norm_symbol(o.get("symbol"), asset, f + ".symbol")
        if _lower(o.get("side")) != "sell":
            continue
        state = _lower(o.get("state"))
        if state is None:
            notes.append("a %s sell order on %s had no state; not counted" % (asset, sym))
            continue
        if state not in OPEN_STATES[asset]:
            continue
        kind = norm_order_type(o, asset, f + ".type")
        qty = _dec(o.get("quantity"), f + ".quantity", positive=True)
        filled = _dec(o.get("cumulative_quantity"), f + ".cumulative_quantity", required=False, nonneg=True)
        remaining = qty - (filled or Decimal(0))
        if remaining <= 0:
            continue
        trigger = _dec(o.get("stop_price"), f + ".stop_price", required=kind in ("stop_market", "stop_limit"),
                       positive=True)
        limit = _dec(o.get("limit_price") if o.get("limit_price") is not None else o.get("price"),
                     f + ".limit_price", required=False, positive=True)
        oid = next((o[k] for k in ("order_id", "id") if isinstance(o.get(k), str)), None)
        e = Exit(kind=kind, qty=remaining, trigger=trigger, limit=limit, tif=_lower(o.get("time_in_force")),
                 created=parse_ts(o.get("created_at"), f + ".created_at", required=False), account=last4,
                 asset=asset, symbol=sym, oid=oid, seq=i)
        # An OCO's legs are equity orders too; never count the same shares twice.
        leg_of = None
        if asset == "equity":
            for oco, leg_ids, used in ocos:
                if oco.account != last4 or oco.symbol != sym:
                    continue
                if oid and oid in leg_ids:
                    leg_of = oco
                    break
                if remaining == oco.qty and kind == "stop_market" and trigger == oco.trigger and not used["stop"]:
                    used["stop"] = True
                    leg_of = oco
                    notes.append("%s matched OCO %s%s's stop leg (same shares and price); counted once as the OCO"
                                 % (e.label(), DOTS2, oco.id_short or "?"))
                    break
                if remaining == oco.qty and kind == "limit" and limit == oco.limit and not used["limit"]:
                    used["limit"] = True
                    leg_of = oco
                    notes.append("%s matched OCO %s%s's take-profit leg; counted once as the OCO"
                                 % (e.label(), DOTS2, oco.id_short or "?"))
                    break
        if leg_of is None:
            exits.setdefault((last4, asset, sym), []).append(e)

    # -- alerts ------------------------------------------------------------------------------------
    alerts = {}
    all_alerts = []
    raw_alerts = data.get("alerts") or []
    if not isinstance(raw_alerts, list):
        raise InputError("BAD_INPUT", "alerts", "alerts must be a list")
    for i, al in enumerate(raw_alerts):
        f = "alerts[%d]" % i
        if not isinstance(al, dict):
            raise InputError("BAD_INPUT", f, "each alert is an object")
        asset = _asset_class(al.get("asset_class"), f + ".asset_class")
        sym = norm_symbol(al.get("symbol"), asset, f + ".symbol")
        cond = _lower(al.get("condition_type"))
        thr = _dec(al.get("threshold"), f + ".threshold", required=False)
        enabled = _bool(al.get("enabled"), f + ".enabled")
        row = {"symbol": sym, "asset": asset, "cond": cond, "threshold": thr, "enabled": enabled,
               "alert_id": al.get("alert_id") if isinstance(al.get("alert_id"), str) else None}
        all_alerts.append(row)
        if cond in DOWNSIDE_ALERTS:
            alerts.setdefault((asset, sym), []).append(row)

    earnings = data.get("earnings") or {}
    if not isinstance(earnings, dict):
        raise InputError("BAD_INPUT", "earnings", "earnings must be an object keyed by symbol")

    # -- per position ----------------------------------------------------------------------------
    results = []
    held_keys = set(p["key"] for p in positions)
    for p in positions:
        results.append(_evaluate(p, exits.get(p["key"], []), alerts.get((p["asset"], p["symbol"]), []),
                                 earnings, alerts_read, now, regular_open, next_open, last_open,
                                 stale_min, life_days, warn_days))
    if any(p["asset"] == "equity" for p in positions) and next_open is None:
        not_checked.append("EARNINGS_BEFORE_NEXT_SESSION (next_regular_open_et not given)")
    if last_estimated and earnings:
        notes.append("the last regular open was estimated from weekdays (holidays ignored); pass "
                     "last_regular_open_et after a market holiday")

    orphans = []
    for key, lst in sorted(exits.items()):
        if key in held_keys:
            continue
        acct = accounts.get(key[0]) if accounts is not None else None
        pos_read = "positions" if key[1] == "equity" else "crypto_positions"
        if accounts is not None and (acct is None or not acct["in_scope"] or acct["read"][pos_read] != "complete"):
            continue  # the position may exist but was not read; not a cleanup candidate
        for e in sorted(lst, key=lambda x: x.seq):
            if e.kind in STOP_KINDS or e.kind == "limit":
                orphans.append({"account_last4": key[0], "symbol": key[2], "asset_class": key[1],
                                "order": e.label(), "note": "a sell order on a symbol this account no longer "
                                "holds; cancelling it is a cleanup proposal (it protects nothing)"})
    alert_cleanup = _alert_cleanup(all_alerts)

    results.sort(key=lambda r: (STATUSES.index(r["status"]),
                                -(Decimal(r["value_usd"]) if r["value_usd"] is not None else Decimal(-1)),
                                r["symbol"], r["account_last4"]))
    summary = _summary(results)
    acct_rows = _account_rows(accounts, positions)
    out = {
        "ok": True,
        "as_of": {"now_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "now_et": fmt_et(now),
                  "session_regular_open": regular_open,
                  "next_regular_open_et": fmt_et(next_open) if next_open else None},
        "summary": summary,
        "positions": [dict((k, v) for k, v in r.items() if not k.startswith("_")) for r in results],
        "accounts": acct_rows,
        "orphan_exits": orphans,
        "alert_cleanup": alert_cleanup,
        "first_line": _first_line(results, summary, accounts),
        "scope_line": _scope_line(accounts, positions),
        "table": _table(results),
        "footer": FOOTER,
        "not_checked": sorted(set(not_checked)),
        "notes": notes,
    }
    return out


def _evaluate(p, exits, downside_alerts, earnings, alerts_read, now, regular_open, next_open, last_open,
              stale_min, life_days, warn_days):
    qty, asset, price = p["qty"], p["asset"], p["price"]
    reads = _reads_for(p["acct"])
    exits = sorted(exits, key=lambda x: x.seq)
    counted = [e for e in exits if e.kind in STOP_KINDS]
    others = [e for e in exits if e.kind not in STOP_KINDS]
    # Shares pledged as option collateral can't be sold by a stop, so exits compete for the rest only.
    collateral = min(p["collateral"] or Decimal(0), qty)
    protectable = qty - collateral
    # Walk down from the highest stop: each exit counts only if it fits in the shares still held.
    remaining, covered = protectable, Decimal(0)
    for e in sorted(counted, key=lambda x: (-x.trigger, x.qty)):
        e.effective = e.qty <= remaining
        if e.effective:
            covered += e.qty
            remaining -= e.qty
    uncovered = qty - covered

    orders_key = "equity_orders" if asset == "equity" else "crypto_orders"
    orders_ok = reads[orders_key] == "complete"
    oco_state = reads["advanced_orders"] if asset == "equity" else "not_applicable"
    oco_blind = asset == "equity" and oco_state in ("partial", "failed", "not_read")
    enabled_alerts = [a for a in downside_alerts if a["enabled"] is True]
    if covered == qty:
        status = "protected"
    elif not orders_ok or oco_blind:
        status = "unknown"
    elif covered > 0:
        status = "partial"
    elif enabled_alerts:
        status = "backstop_only"
    else:
        status = "unprotected"

    gaps = []

    def gap(code, text, short):
        gaps.append({"code": code, "text": text, "short": short})

    value = qty * price if price is not None else None
    unit = "sh" if asset == "equity" else p["symbol"]
    if status == "partial":
        val = " (%s)" % fmt_money(uncovered * price) if price is not None else ""
        gap("UNCOVERED_SHARES", "%s %s%s have no working exit" % (fmt_qty(uncovered), unit, val),
            "%s %s uncovered" % (fmt_qty(uncovered), unit))
    total_exits = sum((e.qty for e in exits), Decimal(0))
    if exits and total_exits > protectable:
        parts = " + ".join(e.short() for e in exits)
        if collateral > 0:
            vs = "%s free of option collateral (%s held)" % (fmt_qty(protectable), fmt_qty(qty))
            vs_short = "%s free" % fmt_qty(protectable)
        else:
            vs = vs_short = "%s held" % fmt_qty(qty)
        gap("EXITS_EXCEED_POSITION", "%s = %s %s of exits vs %s" % (parts, fmt_qty(total_exits), unit, vs),
            "exits %s vs %s" % (fmt_qty(total_exits), vs_short))
    working = [e for e in exits if e.kind in STOP_KINDS]
    if asset == "equity":
        for e in working:
            what = "OCO" if e.kind == "oco" else e.short() + " " + unit
            if e.tif == "gfd":
                gap("GFD_EXPIRES_TODAY", "%s is GFD: it expires at the close of its trading day and does not carry over"
                    % what, "GFD expires at close")
            elif e.tif is None:
                gap("TIF_UNKNOWN", "%s: time in force not in the order data; if it is GFD it expires at the close"
                    % what, "TIF not shown")
            elif e.tif == "gtc" and e.created is not None:
                created_et = to_et(e.created).date()
                lapse = created_et + timedelta(days=life_days)
                if (lapse - to_et(now).date()).days <= warn_days:
                    gap("GTC_EXPIRING_SOON", "%s (GTC, created %s) lapses around %s (%d-day GTC lifetime, unverified)"
                        % (what, created_et.isoformat(), lapse.isoformat(), life_days),
                        "GTC lapses ~%s" % lapse.isoformat())
        frac = protectable - protectable.to_integral_value(rounding=ROUND_FLOOR)
        if frac > 0:
            gap("FRACTIONAL_REMAINDER", "%s sh can't be covered by a stop or OCO (whole shares only); only an alert "
                "can watch it" % fmt_qty(frac), "%s sh fractional" % fmt_qty(frac))
        if collateral > 0:
            gap("COLLATERAL_SHARES", "%s sh are collateral for a short option (for example a covered call): no stop or "
                "OCO can sell them while the option is open; the option side is robinhood-options-monitor's"
                % fmt_qty(collateral), "%s sh option collateral" % fmt_qty(collateral))
    if any(e.kind in ("stop_market", "oco") for e in working):
        if asset == "crypto":
            gap("STOP_MARKET_GAP_RISK", "a stop order becomes a market order when it triggers: a fast move can fill "
                "well below the stop", "stop-market gap risk")
        else:
            gap("STOP_MARKET_GAP_RISK", "stop legs are stop-market: a gap can fill far below the stop",
                "stop-market gap risk")
    if any(e.kind == "stop_limit" for e in working):
        gap("STOP_LIMIT_MAY_NOT_FILL", "a stop-limit sells only at its limit or better: if the price gaps below the "
            "limit it may not fill at all", "stop-limit may not fill")
    if asset == "equity" and working:
        closed = ""
        if not regular_open and next_open is not None:
            closed = "; the regular session is closed now, so nothing triggers before %s" % fmt_et(next_open)
        gap("NO_EXTENDED_HOURS_COVERAGE", "stops and OCOs act in regular hours only (09:30–16:00 ET); pre-market, "
            "after-hours and overnight moves are not covered%s" % closed, "regular hours only")
    if asset == "crypto":
        for e in working:
            what = e.short() + " " + unit
            if e.tif == "gfd":
                gap("CRYPTO_STOP_DAY_ONLY", "the %s is day-only (time in force gfd): it expires at the end of the day"
                    % what, "stop expires today (day-only)")
            elif e.tif is None:
                # Not "gfd": that default applies to an order sent without one, not to a row that omits it.
                gap("TIF_UNKNOWN", "%s: time in force not in the order data; if it is gfd it expires at the end of "
                    "the day; check it in the app" % what, "TIF not shown")
            elif e.tif == "gtc":
                gap("CRYPTO_GTC_90D", "GTC crypto stop orders last 90 days, then lapse", "GTC lasts 90 days")
    info = earnings.get(p["symbol"]) if asset == "equity" else None
    if isinstance(info, dict) and next_open is not None:
        hit, d, timing = _earnings_hit(info, p["symbol"], last_open, next_open)
        if hit:
            when = [d.isoformat(), timing or "time not given"]
            if info.get("verified") is not True:
                when.append("date unverified by the company")
            risk = ("a stop can fill far below its price on the gap" if covered > 0
                    else "no exit order is in place")
            gap("EARNINGS_BEFORE_NEXT_SESSION", "earnings (%s) are priced in at the next regular open: %s"
                % (", ".join(when), risk), "earnings %s before next open" % d.strftime("%m-%d"))
    if not p["agentic"]:
        gap("NOT_AGENT_TRADABLE", "the agent can't place or simulate orders in this account; a native alert is the "
            "only agent-side protection (a stop you enter in the app is the other option)",
            "agent can't place orders here")
    for a in downside_alerts:
        if a["enabled"] is not True:
            gap("ALERT_DISABLED", "%s is off, so it will not notify you" % _alert_text(a), "alert is off")
    if status == "backstop_only":
        names = "; ".join(_alert_text(a) for a in enabled_alerts)
        lead = "only an alert (%s) watches" % names if len(enabled_alerts) == 1 else "only alerts (%s) watch" % names
        gap("ALERT_NOTIFIES_ONLY", "%s this position: it notifies your phone; it does not sell" % lead,
            "alert notifies only")
    if status != "protected":
        if asset == "equity" and oco_state == "not_enabled":
            gap("OCO_UNREADABLE", "the OCO tools are not enabled for this account (the connector answered that the "
                "tool does not exist), so OCOs can't be read or simulated here; this status counts stops and alerts "
                "only", "OCOs unreadable here")
        missing = []
        if not orders_ok:
            missing.append("open %s orders (%s)" % (asset, reads[orders_key]))
        if oco_blind:
            missing.append("OCOs (%s)" % oco_state)
        if alerts_read != "complete":
            missing.append("alerts (%s)" % alerts_read)
        if missing:
            gap("READ_INCOMPLETE", "not fully read: %s; a working exit or alert may exist that this check can't see"
                % ", ".join(missing), "not fully read")
    sell_holds, why = None, "held for sell orders"
    if p["held"] is not None:
        sell_holds = p["held"]
    elif p["sellable"] is not None:
        sell_holds = qty - p["sellable"] - collateral
        if p["collateral"] is None:
            why = "held for sell orders or as option collateral"
    if sell_holds is not None and sell_holds - total_exits > 0:
        unseen = sell_holds - total_exits
        gap("HELD_SHARES_UNEXPLAINED", "%s %s are %s this check could not see (for example an OCO or an order "
            "older than the lookback); check the app" % (fmt_qty(unseen), unit, why),
            "%s %s held by unseen orders" % (fmt_qty(unseen), unit))
    if price is None:
        gap("STALE_QUOTE", "no quote was given, so the position's value is unknown", "no quote")
    elif p["price_as_of"] is None:
        gap("STALE_QUOTE", "the quote time was not given; treat the value as unverified", "quote time unknown")
    else:
        age_min = (now - p["price_as_of"]).total_seconds() / 60.0
        stale = age_min > stale_min if (asset == "crypto" or regular_open) else (
            last_open is not None and p["price_as_of"] < last_open)
        if stale:
            gap("STALE_QUOTE", "the quote is from %s; values may be off" % fmt_et(p["price_as_of"]), "stale quote")
    gaps.sort(key=lambda g: _GAP_RANK[g["code"]])

    can_add = []
    whole = protectable.to_integral_value(rounding=ROUND_FLOOR) if asset == "equity" else protectable
    if p["agentic"] and asset == "equity" and whole >= 1:
        if oco_state == "complete":
            can_add.append("oco")
        can_add.append("stop_order")
    if p["agentic"] and asset == "crypto" and protectable > 0:
        can_add.append("crypto_stop_order")
    can_add.append("alert")

    by_trigger = sorted(working, key=lambda x: (-x.trigger, x.qty))
    covering = [e.label() for e in by_trigger if e.effective]
    table_cover = [e.label() + ("" if e.effective else " (not counted)") for e in by_trigger]
    not_counted = [{"order": e.label(), "why": "bigger than the %s %s left when it triggers, so it is assumed to be "
                    "rejected" % (fmt_qty(_left_before(e, working, protectable)), unit)}
                   for e in working if not e.effective]
    not_counted += [{"order": e.label(), "why": "a %s is not downside protection" % e.word()} for e in others]
    return {
        "symbol": p["symbol"],
        "asset_class": asset,
        "account_last4": p["last4"],
        "account": "%s %s%s" % (p["label"], MASK, p["last4"]),
        "agentic": p["agentic"],
        "quantity": fmt_qty(qty),
        "price": str(price) if price is not None else None,
        "value_usd": money(value) if value is not None else None,
        "status": status,
        "covered_qty": fmt_qty(covered),
        "uncovered_qty": fmt_qty(uncovered),
        "uncovered_value_usd": money(uncovered * price) if price is not None else None,
        "covering": covering,
        "not_counted": not_counted,
        "alerts": [_alert_text(a) + ("" if a["enabled"] is True else " (off)") for a in downside_alerts],
        "gaps": gaps,
        "can_add": can_add,
        "max_whole_shares": fmt_qty(whole) if asset == "equity" else None,
        "sellable_now": fmt_qty(p["sellable"]) if p["sellable"] is not None else None,
        "_table_cover": table_cover,
    }


def _left_before(target, working, qty):
    left = qty
    for e in sorted(working, key=lambda x: (-x.trigger, x.qty)):
        if e is target:
            return left
        if e.effective:
            left -= e.qty
    return left


def _alert_text(a):
    word = {"price_below": "below", "price_crosses": "crossing"}.get(a["cond"], a["cond"])
    level = fmt_money(a["threshold"]) if a["threshold"] is not None else "?"
    return "alert %s %s" % (word, level)


def _alert_cleanup(rows):
    out = []
    seen = {}
    for r in rows:
        level = str(r["threshold"].normalize()) if r["threshold"] is not None else None
        key = (r["symbol"], r["asset"], r["cond"], level)
        if key in seen:
            out.append({"symbol": r["symbol"], "alert": _alert_text(r) if r["cond"] in DOWNSIDE_ALERTS else
                        "%s %s" % (r["cond"], r["threshold"] if r["threshold"] is not None else ""),
                        "alert_id": r["alert_id"], "issue": "duplicate of another alert with the same condition"})
        seen[key] = True
        if r["enabled"] is False:
            out.append({"symbol": r["symbol"], "alert": _alert_text(r) if r["cond"] in DOWNSIDE_ALERTS else r["cond"],
                        "alert_id": r["alert_id"], "issue": "disabled: re-enable with update_alert or leave it"})
    return out


def _summary(results):
    s = {"positions": len(results)}
    for st in STATUSES:
        s[st] = sum(1 for r in results if r["status"] == st)

    def total(pred, field):
        vals = [Decimal(r[field]) for r in results if pred(r) and r[field] is not None]
        return money(sum(vals, Decimal(0)))

    s["value_total_usd"] = total(lambda r: True, "value_usd")
    s["value_unprotected_usd"] = total(lambda r: r["status"] == "unprotected", "value_usd")
    s["value_backstop_only_usd"] = total(lambda r: r["status"] == "backstop_only", "value_usd")
    s["value_partial_uncovered_usd"] = total(lambda r: r["status"] == "partial", "uncovered_value_usd")
    s["value_unknown_usd"] = total(lambda r: r["status"] == "unknown", "value_usd")
    s["unpriced_positions"] = sum(1 for r in results if r["value_usd"] is None)
    return s


def _names(rows, field, limit=3):
    dup = {}
    for r in rows:
        dup[r["symbol"]] = dup.get(r["symbol"], 0) + 1
    ranked = sorted(rows, key=lambda r: -(Decimal(r[field]) if r[field] is not None else Decimal(-1)))
    out = []
    for r in ranked[:limit]:
        name = r["symbol"] if dup[r["symbol"]] == 1 else "%s %s%s" % (r["symbol"], MASK, r["account_last4"])
        out.append("%s %s" % (name, fmt_money(Decimal(r[field])) if r[field] is not None else "value unknown"))
    return MID.join(out)


def _first_line(results, s, accounts):
    incomplete = accounts is None or any(_positions_incomplete(a) for a in accounts.values())
    unprot = [r for r in results if r["status"] == "unprotected"]
    backstop = [r for r in results if r["status"] == "backstop_only"]
    partial = [r for r in results if r["status"] == "partial"]
    unknown = [r for r in results if r["status"] == "unknown"]
    if unprot or backstop or partial:
        parts = []
        if unprot:
            parts.append("%s unprotected (%s)" % (fmt_money(Decimal(s["value_unprotected_usd"])),
                                                  _names(unprot, "value_usd")))
        if backstop:
            parts.append("%s alert-only (%s)" % (fmt_money(Decimal(s["value_backstop_only_usd"])),
                                                 _names(backstop, "value_usd")))
        if partial:
            parts.append("%s uncovered in partial positions (%s)" % (
                fmt_money(Decimal(s["value_partial_uncovered_usd"])), _names(partial, "uncovered_value_usd")))
        if unknown:
            parts.append("%d unknown (exits not fully read)" % len(unknown))
        if s["unpriced_positions"]:
            parts.append("%d without a quote" % s["unpriced_positions"])
        if accounts is not None:
            unread = ["%s%s" % (MASK, a["last4"]) for a in accounts.values() if _positions_incomplete(a)]
            if unread:
                parts.append("holdings not fully read in %s" % ", ".join(unread))
        return "ACTION NEEDED: Dollars at stake: " + MID.join(parts)
    if unknown:
        return "UNKNOWN: Dollars at stake: %s in positions whose exits could not be fully read (%s)" % (
            fmt_money(Decimal(s["value_unknown_usd"])), _names(unknown, "value_usd"))
    if not results:
        if incomplete:
            return "UNKNOWN: Dollars at stake: none found, but not every account's positions were read"
        return "NO ACTION: Dollars at stake: none found (no stock or crypto positions in the accounts read)"
    if incomplete:
        return ("UNKNOWN: Dollars at stake: none found in what was read; read completeness was not confirmed for "
                "every account")
    ngaps = sum(len(r["gaps"]) for r in results)
    tail = ("%s%d gap%s to review" % (MID, ngaps, "" if ngaps == 1 else "s")) if ngaps else ""
    return "CLEAR: Dollars at stake: none found; every position read has a working stop or OCO" + tail


def _positions_incomplete(a):
    """An in-scope account whose stock or crypto holdings were not fully read (any account, not only Agentic)."""
    if not a["in_scope"]:
        return False
    if a["read"]["positions"] != "complete":
        return True
    return a["read"]["crypto_positions"] in INCOMPLETE


def _scope_line(accounts, positions):
    if accounts is None:
        seen = []
        for p in positions:
            tag = "%s %s%s" % (p["label"], MASK, p["last4"])
            if tag not in seen:
                seen.append(tag)
        return "Accounts read: %s (read completeness not stated)" % (MID.join(seen) or "none")
    read, skipped, partial = [], [], []
    for a in accounts.values():
        tag = "%s %s%s" % (a["label"], MASK, a["last4"])
        if not a["in_scope"]:
            skipped.append(tag)
            continue
        read.append(tag)
        bad = [k.replace("_", " ") + " " + v for k, v in sorted(a["read"].items()) if v in INCOMPLETE]
        if bad:
            partial.append("%s: %s" % (tag, ", ".join(bad)))
    line = "Accounts read: %s%sNot read: %s" % (MID.join(read) or "none", MID, ", ".join(skipped) or "none")
    if partial:
        line += "%sIncomplete: %s" % (MID, "; ".join(partial))
    return line


def _account_rows(accounts, positions):
    if accounts is None:
        return []
    rows = []
    for a in accounts.values():
        rows.append({"account": "%s %s%s" % (a["label"], MASK, a["last4"]), "account_last4": a["last4"],
                     "agentic": a["agentic"], "in_scope": a["in_scope"], "read": a["read"],
                     "positions": sum(1 for p in positions if p["last4"] == a["last4"])})
    return rows


def _table(results):
    rows = ["| Position | Account | Status | Covered by | Gaps |", "|---|---|---|---|---|"]
    for r in results:
        unit = "sh" if r["asset_class"] == "equity" else ""
        pos = ("%s %s %s" % (r["symbol"], r["quantity"], unit)).strip()
        cover = " + ".join(r["_table_cover"])
        if not cover:
            cover = ", ".join(r["alerts"]) or "none"
        elif r["alerts"]:
            cover += "; " + ", ".join(r["alerts"])
        gaps = MID.join(g["short"] for g in r["gaps"]) or "—"
        rows.append("| %s | %s | %s | %s | %s |" % (pos, r["account"], r["status"].replace("_", " "), cover, gaps))
    return rows


# ---------------------------------------------------------------------------------------------
# levels
# ---------------------------------------------------------------------------------------------
def parse_rule(value, forms, field):
    """-> ('unset',) | ('ask',) | ('none',) | ('pct', n) | ('r', k) | ('atr', m, period)."""
    if value is None or value == UNSET:
        return ("unset",)
    if not isinstance(value, str):
        raise InputError("BAD_RULE", field, "%s must be a string rule" % field)
    v = value.strip().lower()
    if v in ("ask", "none"):
        if v not in forms:
            raise InputError("BAD_RULE", field, "%r is not allowed for %s" % (value, field))
        return (v,)
    parts = v.split(":")
    kind = parts[0]
    if kind not in forms:
        raise InputError("BAD_RULE", field, "%r: %s accepts %s" % (value, field, ", ".join(forms)))
    try:
        if kind in ("pct", "r") and len(parts) == 2:
            n = Decimal(parts[1])
            if n.is_finite() and n > 0 and (kind != "pct" or n < 100):
                return (kind, n)
        if kind == "atr" and len(parts) == 3 and re.match(r"^\d+$", parts[2]):
            m, period = Decimal(parts[1]), int(parts[2])
            if m.is_finite() and m > 0 and period >= 1:
                return ("atr", m, period)
    except InvalidOperation:
        pass
    raise InputError("BAD_RULE", field, "%r is not a valid rule (examples: pct:8, atr:2.0:14, r:2)" % value)


def _rule_text(rule, raw):
    if rule[0] in ("pct", "r", "atr"):
        return raw.strip().lower()
    return rule[0]


def _override(o, key, sym):
    v = o.get(key) if isinstance(o, dict) else None
    if v is None or v == UNSET:
        return None
    return _dec(v, "symbol_overrides.%s.%s" % (sym, key), positive=True)


def op_levels(data):
    rules = data.get("rules") if data.get("rules") is not None else {}
    if not isinstance(rules, dict):
        raise InputError("BAD_INPUT", "rules", "rules must be an object")
    raw_stop = rules.get("stop_rule", UNSET)
    stop_rule = parse_rule(raw_stop, ("pct", "atr", "ask"), "rules.stop_rule")
    target_requested = "target_rule" in rules
    raw_target = rules.get("target_rule")
    target_rule = parse_rule(raw_target, ("pct", "r", "none", "ask"), "rules.target_rule") if target_requested else None
    overrides = data.get("symbol_overrides") or {}
    source = data.get("override_source", "user_config")
    if source not in ("user", "user_config"):
        raise InputError("BAD_ENUM", "override_source", "override_source must be user or user_config")
    prices = data.get("prices") or {}
    atr = data.get("atr") or {}
    for name, v in (("symbol_overrides", overrides), ("prices", prices), ("atr", atr)):
        if not isinstance(v, dict):
            raise InputError("BAD_INPUT", name, "%s must be an object keyed by symbol" % name)
    symbols = []
    for s in list(prices) + list(overrides):
        u = s.strip().upper()
        if u not in symbols:
            symbols.append(u)
    if not symbols:
        raise InputError("MISSING_FIELD", "prices", "give prices (and any overrides) for the symbols to protect")
    up = lambda d: dict((k.strip().upper(), v) for k, v in d.items())  # noqa: E731
    prices, overrides, atr = up(prices), up(overrides), up(atr)

    levels, ask_all, missing_all = {}, [], {}
    for sym in symbols:
        o = overrides.get(sym) or {}
        price = _dec(prices.get(sym), "prices.%s" % sym, required=False, positive=True)
        entry = {"stop": None, "target": None, "provenance": None, "provenance_by_field": {}, "rule": None,
                 "ask": [], "missing": [], "errors": [], "warnings": []}
        stop, target = _override(o, "stop", sym), _override(o, "target", sym)
        stop_txt = target_txt = None
        if stop is not None:
            entry["provenance_by_field"]["stop"] = source
            stop_txt = "your stop"
        elif stop_rule[0] in ("unset", "ask"):
            entry["ask"].append("stop")
        elif price is None:
            entry["missing"].append("price")
        elif stop_rule[0] == "pct":
            stop = _round_price(price * (1 - stop_rule[1] / 100))
            entry["provenance_by_field"]["stop"] = "user_config"
            stop_txt = _rule_text(stop_rule, raw_stop)
        else:  # atr
            a = atr.get(sym)
            val, period = (a.get("value"), a.get("period")) if isinstance(a, dict) else (a, None)
            if isinstance(period, str) and re.match(r"^\s*\d+\s*$", period):
                period = int(period)
            if val is None:
                entry["missing"].append("atr(%d)" % stop_rule[2])
            elif period is not None and period != stop_rule[2]:
                entry["errors"].append({"code": "ATR_PERIOD_MISMATCH", "msg": "the rule uses ATR(%d) but ATR(%s) was "
                                        "given" % (stop_rule[2], period)})
            elif period is None and stop_rule[2] != DEFAULT_ATR_PERIOD:
                # A bare number is taken as the connector's default ATR(14); never let it stand in for ATR(p).
                entry["errors"].append({"code": "ATR_PERIOD_UNSTATED", "msg": "the rule uses ATR(%d); pass atr as "
                                        "{\"value\": ..., \"period\": %d} fetched with period %d (a bare number is "
                                        "read as ATR(%d))" % (stop_rule[2], stop_rule[2], stop_rule[2],
                                                              DEFAULT_ATR_PERIOD)})
            else:
                stop = _round_price(price - stop_rule[1] * _dec(val, "atr.%s" % sym, positive=True))
                entry["provenance_by_field"]["stop"] = "user_config"
                stop_txt = _rule_text(stop_rule, raw_stop)
        if target is not None:
            entry["provenance_by_field"]["target"] = source
            target_txt = "your target"
        elif target_rule is None:
            target_txt = None
        elif target_rule[0] in ("unset", "ask"):
            entry["ask"].append("target")
        elif target_rule[0] == "none":
            target_txt = "no target"
        elif price is None:
            if "price" not in entry["missing"]:
                entry["missing"].append("price")
        elif target_rule[0] == "pct":
            target = _round_price(price * (1 + target_rule[1] / 100))
            entry["provenance_by_field"]["target"] = "user_config"
            target_txt = _rule_text(target_rule, raw_target)
        else:  # r:k needs the stop
            if stop is None:
                if "stop" in entry["ask"]:
                    entry["ask"].append("target")
                    entry["warnings"].append({"code": "TARGET_NEEDS_STOP", "msg": "%s is a multiple of the stop "
                                              "distance, so it waits for your stop" % raw_target})
            else:
                target = _round_price(price + target_rule[1] * (price - stop))
                entry["provenance_by_field"]["target"] = "user_config"
                target_txt = _rule_text(target_rule, raw_target)
        if stop is not None and stop <= 0:
            entry["errors"].append({"code": "STOP_NOT_POSITIVE", "msg": "the rule puts the stop at or below $0"})
        if price is not None:
            if stop is not None and stop >= price:
                entry["errors"].append({"code": "STOP_NOT_BELOW_PRICE", "msg": "a sell stop at or above the current "
                                        "price (%s) triggers at once" % fmt_px(price)})
            if target is not None and target <= price:
                entry["errors"].append({"code": "TARGET_NOT_ABOVE_PRICE", "msg": "a take-profit at or below the "
                                        "current price (%s) fills at once" % fmt_px(price)})
            if stop is not None and stop > 0:
                entry["stop_pct_below"] = pct1((price - stop) / price * 100)
                if abs(price - stop) / price < OCO_MIN_DISTANCE:
                    entry["warnings"].append({"code": "OCO_MIN_DISTANCE", "msg": "the stop is less than 0.25% from "
                                              "the market; an OCO would be rejected"})
            if target is not None:
                entry["target_pct_above"] = pct1((target - price) / price * 100)
                if abs(target - price) / price < OCO_MIN_DISTANCE:
                    entry["warnings"].append({"code": "OCO_MIN_DISTANCE", "msg": "the target is less than 0.25% from "
                                              "the market; an OCO would be rejected"})
        if stop is not None and target is not None:
            if target <= stop:
                entry["errors"].append({"code": "TARGET_NOT_ABOVE_STOP", "msg": "for a long position the take-profit "
                                        "must be above the stop"})
            elif target - stop < OCO_MIN_GAP:
                entry["warnings"].append({"code": "OCO_MIN_GAP", "msg": "an OCO needs the two prices at least $0.10 "
                                          "apart"})
        entry["stop"] = str(stop) if stop is not None else None
        entry["target"] = str(target) if target is not None else None
        provs = set(entry["provenance_by_field"].values())
        entry["provenance"] = ("user" if "user" in provs else "user_config") if provs else None
        texts = [t for t in (stop_txt, target_txt) if t]
        entry["rule"] = " / ".join(texts) if texts else None
        for k in ("warnings", "errors", "missing"):
            if not entry[k]:
                del entry[k]
        if not entry["ask"]:
            del entry["ask"]
        else:
            ask_all.extend(x for x in entry["ask"] if x not in ask_all)
        if entry.get("missing"):
            missing_all[sym] = entry["missing"]
        levels[sym] = entry
    out = {"ok": True, "levels": levels, "note": LEVELS_NOTE}
    if ask_all:
        out["ask"] = ask_all
    if missing_all:
        out["missing_inputs"] = missing_all
    return out


# ---------------------------------------------------------------------------------------------
# Dispatch, JSON Schemas and self-test
# ---------------------------------------------------------------------------------------------
OPS = {"audit": op_audit, "levels": op_levels}


def run(op, data):
    if op not in OPS:
        return _err("UNKNOWN_OP", "op", "unknown op %r; expected audit or levels" % op)
    if not isinstance(data, dict):
        return _err("BAD_INPUT", "", "input must be a JSON object")
    try:
        return OPS[op](data)
    except InputError as exc:
        return _err(exc.code, exc.field, exc.msg)


_S = {"type": "string"}
_NS = {"type": ["string", "null"]}
_DEC = {"type": "string", "pattern": r"^[+-]?(\d+(\.\d*)?|\.\d+)$"}
_B = {"type": "boolean"}
_READ = {"enum": list(READ_VALUES)}
_ERR = {"type": "object", "required": ["ok", "errors"],
        "properties": {"ok": {"enum": [False]},
                       "errors": {"type": "array", "items": {"type": "object", "required": ["code", "field", "msg"],
                                                             "properties": {"code": _S, "field": _S, "msg": _S}}}}}
_GAP = {"type": "object", "required": ["code", "text", "short"], "additionalProperties": False,
        "properties": {"code": {"enum": list(GAP_CODES)}, "text": _S, "short": _S}}
_POS_OUT = {"type": "object", "required": ["symbol", "account_last4", "status", "covered_qty", "uncovered_qty",
                                            "covering", "gaps", "can_add"],
            "properties": {"symbol": _S, "asset_class": {"enum": ["equity", "crypto"]}, "account_last4": _S,
                           "account": _S, "agentic": _B, "quantity": _S, "price": _NS, "value_usd": _NS,
                           "status": {"enum": list(STATUSES)}, "covered_qty": _S, "uncovered_qty": _S,
                           "uncovered_value_usd": _NS, "covering": {"type": "array", "items": _S},
                           "not_counted": {"type": "array"}, "alerts": {"type": "array", "items": _S},
                           "gaps": {"type": "array", "items": _GAP},
                           "can_add": {"type": "array", "items": {"enum": ["oco", "stop_order", "crypto_stop_order",
                                                                           "alert"]}},
                           "max_whole_shares": _NS, "sellable_now": _NS}}
_LEVEL = {"type": "object", "required": ["stop", "target", "provenance", "rule"],
          "properties": {"stop": _NS, "target": _NS, "provenance": {"enum": ["user", "user_config", None]},
                         "provenance_by_field": {"type": "object"}, "rule": _NS,
                         "ask": {"type": "array", "items": {"enum": ["stop", "target"]}},
                         "missing": {"type": "array", "items": _S}, "errors": {"type": "array"},
                         "warnings": {"type": "array"}, "stop_pct_below": {"type": "number"},
                         "target_pct_above": {"type": "number"}}}

SCHEMAS = {
    "audit": {
        "input": {"type": "object", "required": ["now", "positions"], "properties": {
            "now": _S, "session_regular_open": _B, "next_regular_open_et": _S, "last_regular_open_et": _S,
            "session": {"type": "object"}, "stale_after_minutes": {"type": "integer"},
            "gtc_lifetime_days": {"type": "integer"}, "gtc_warn_days": {"type": "integer"},
            "alerts_read": {"enum": ["complete", "partial", "failed", "not_read"]},
            "accounts": {"type": "array", "items": {"type": "object", "required": ["account_last4"], "properties": {
                "account_last4": _S, "label": _S, "agentic": _B, "in_scope": _B,
                "read": {"type": "object", "properties": dict((k, _READ) for k in READ_KEYS)}}}},
            "positions": {"type": "array", "items": {"type": "object",
                                                     "required": ["account_last4", "symbol", "quantity"],
                                                     "properties": {"account_last4": _S, "agentic": _B,
                                                                    "account_label": _S, "account_type": _S,
                                                                    "asset_class": {"enum": ["equity", "crypto"]},
                                                                    "symbol": _S, "quantity": _DEC,
                                                                    "held_for_orders": _DEC, "sellable": _DEC,
                                                                    "held_for_collateral": _DEC, "price": _DEC,
                                                                    "price_as_of": _S}}},
            "open_orders": {"type": "array", "items": {"type": "object",
                                                       "required": ["account_last4", "symbol", "side", "type", "state",
                                                                    "quantity"],
                                                       "properties": {"account_last4": _S,
                                                                      "asset_class": {"enum": ["equity", "crypto"]},
                                                                      "symbol": _S, "side": _S, "type": _S,
                                                                      "trigger": _S, "state": _S, "quantity": _DEC,
                                                                      "cumulative_quantity": _DEC, "stop_price": _DEC,
                                                                      "limit_price": _DEC, "price": _DEC,
                                                                      "time_in_force": _NS, "created_at": _S,
                                                                      "order_id": _S}}},
            "advanced_orders": {"type": "array", "items": {"type": "object",
                                                           "required": ["account_last4", "symbol", "side", "quantity",
                                                                        "take_profit_limit_price",
                                                                        "stop_loss_stop_price", "active"],
                                                           "properties": {"account_last4": _S, "id_short": _S,
                                                                          "symbol": _S, "side": _S, "quantity": _DEC,
                                                                          "take_profit_limit_price": _DEC,
                                                                          "stop_loss_stop_price": _DEC,
                                                                          "time_in_force": _NS, "active": _B,
                                                                          "created_at": _S,
                                                                          "leg_order_ids": {"type": "array",
                                                                                            "items": _S}}}},
            "alerts": {"type": "array", "items": {"type": "object", "required": ["symbol", "condition_type", "enabled"],
                                                  "properties": {"symbol": _S,
                                                                 "asset_class": {"enum": ["equity", "crypto"]},
                                                                 "condition_type": _S, "threshold": _DEC,
                                                                 "enabled": _B, "alert_id": _S}}},
            "earnings": {"type": "object"}}},
        "output": {"anyOf": [{"type": "object", "required": ["ok", "summary", "positions", "first_line", "table",
                                                             "footer"],
                              "properties": {"ok": {"enum": [True]}, "as_of": {"type": "object"},
                                             "summary": {"type": "object"},
                                             "positions": {"type": "array", "items": _POS_OUT},
                                             "accounts": {"type": "array"}, "orphan_exits": {"type": "array"},
                                             "alert_cleanup": {"type": "array"}, "first_line": _S, "scope_line": _S,
                                             "table": {"type": "array", "items": _S}, "footer": _S,
                                             "not_checked": {"type": "array", "items": _S},
                                             "notes": {"type": "array", "items": _S}}}, _ERR]},
    },
    "levels": {
        "input": {"type": "object", "required": ["prices"], "properties": {
            "rules": {"type": "object", "properties": {"stop_rule": _S, "target_rule": _S}},
            "symbol_overrides": {"type": "object"}, "override_source": {"enum": ["user", "user_config"]},
            "prices": {"type": "object"}, "atr": {"type": "object"}}},
        "output": {"anyOf": [{"type": "object", "required": ["ok", "levels", "note"],
                              "properties": {"ok": {"enum": [True]},
                                             "levels": {"type": "object"},
                                             "ask": {"type": "array", "items": {"enum": ["stop", "target"]}},
                                             "missing_inputs": {"type": "object"}, "note": _S}}, _ERR]},
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
    """Minimal JSON Schema check used by --selftest and the tests."""
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
        ops[name] = dict((k, dict(v, **{"$schema": "https://json-schema.org/draft/2020-12/schema"}))
                         for k, v in pair.items())
    return {"script": SCRIPT, "version": VERSION, "ops": ops, "gap_codes": list(GAP_CODES),
            "statuses": list(STATUSES), "level_item": _LEVEL}


# The spec's worked example (build spec B.3): AMD 12.5 sh with a GFD OCO for 10 and a GTC stop for 5.
_SPEC_AUDIT = {
    "now": "2026-11-17T01:05:00Z",
    "session_regular_open": False,
    "next_regular_open_et": "2026-11-17T09:30:00-05:00",
    "accounts": [{"account_last4": "X4F1", "label": "Agentic", "agentic": True,
                  "read": {"positions": "complete", "equity_orders": "complete", "advanced_orders": "complete",
                           "crypto_positions": "complete", "crypto_orders": "complete"}}],
    "positions": [{"account_last4": "X4F1", "agentic": True, "account_type": "taxable", "asset_class": "equity",
                   "symbol": "AMD", "quantity": "12.5", "held_for_orders": "0", "price": "161.40",
                   "price_as_of": "2026-11-17T01:00:00Z"}],
    "open_orders": [{"account_last4": "X4F1", "asset_class": "equity", "symbol": "AMD", "side": "sell",
                     "type": "stop_market", "state": "confirmed", "quantity": "5", "stop_price": "140.00",
                     "time_in_force": "gtc"}],
    "advanced_orders": [{"account_last4": "X4F1", "id_short": "a1f", "symbol": "AMD", "side": "sell", "quantity": "10",
                         "take_profit_limit_price": "180.00", "stop_loss_stop_price": "142.00",
                         "time_in_force": "gfd", "active": True}],
    "alerts": [],
}

EXAMPLES = [
    ("audit", _SPEC_AUDIT,
     {"ok": True, "summary": {"positions": 1, "partial": 1, "value_partial_uncovered_usd": "403.50"},
      "positions": [{"symbol": "AMD", "status": "partial", "covered_qty": "10", "uncovered_qty": "2.5",
                     "covering": ["OCO ••a1f 10 sh GFD 180/142"],
                     "gaps": [{"code": "UNCOVERED_SHARES"},
                              {"code": "EXITS_EXCEED_POSITION",
                               "text": "stop 5 + OCO 10 = 15 sh of exits vs 12.5 held"},
                              {"code": "GFD_EXPIRES_TODAY"}, {"code": "FRACTIONAL_REMAINDER"},
                              {"code": "STOP_MARKET_GAP_RISK"}, {"code": "NO_EXTENDED_HOURS_COVERAGE"}]}]}),
    ("levels", {"rules": {"stop_rule": "pct:8", "target_rule": "r:2"}, "symbol_overrides": {"NVDA": {"stop": "195.00"}},
                "prices": {"AMD": "161.40"}, "atr": {"AMD": "6.20"}},
     {"ok": True, "levels": {"AMD": {"stop": "148.49", "target": "187.22", "provenance": "user_config",
                                     "rule": "pct:8 / r:2"}}}),
    ("levels", {"rules": {"stop_rule": "UNSET", "target_rule": "ask"}, "prices": {"AMD": "161.40"}},
     {"ok": True, "ask": ["stop", "target"], "levels": {"AMD": {"stop": None, "target": None}}}),
    ("levels", {"rules": {"stop_rule": "atr:2.0:14"}, "prices": {"NVDA": "228.10"}, "atr": {"NVDA": "9.80"}},
     {"ok": True, "levels": {"NVDA": {"stop": "208.50", "target": None, "rule": "atr:2.0:14"}}}),
    ("audit", {"now": "2026-11-17T01:05:00Z", "positions": [{"account_last4": "5QR9X4F1", "symbol": "AMD",
                                                              "quantity": "1", "agentic": True}]},
     {"ok": False, "errors": [{"code": "FULL_ACCOUNT_NUMBER"}]}),
    # Crypto held outside the Agentic account is readable (any rhs_account_number); an unread crypto read on a
    # non-Agentic account is incomplete, so the audit can't say CLEAR or "nothing missing".
    ("audit", {"now": "2026-11-17T01:05:00Z", "session_regular_open": False,
               "next_regular_open_et": "2026-11-17T09:30:00-05:00", "alerts": [],
               "accounts": [{"account_last4": "X4F1", "label": "Agentic", "agentic": True,
                             "read": dict((k, "complete") for k in READ_KEYS)},
                            {"account_last4": "M7Q5", "label": "Individual", "agentic": False,
                             "read": {"positions": "complete", "equity_orders": "complete",
                                      "advanced_orders": "complete"}}],
               "positions": [{"account_last4": "X4F1", "asset_class": "crypto", "symbol": "ETH", "quantity": "0.42",
                              "price": "3000.00", "price_as_of": "2026-11-17T01:04:00Z"}],
               "open_orders": [{"account_last4": "X4F1", "asset_class": "crypto", "symbol": "ETH", "side": "sell",
                                "type": "stop_loss", "state": "confirmed", "quantity": "0.42", "stop_price": "2600.00",
                                "time_in_force": "gtc"}]},
     {"ok": True, "first_line": "UNKNOWN: Dollars at stake: none found in what was read; read completeness was not "
                                "confirmed for every account",
      "scope_line": "Accounts read: Agentic ••••X4F1 · Individual ••••M7Q5 · Not read: none · Incomplete: Individual "
                    "••••M7Q5: crypto orders not_read, crypto positions not_read"}),
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
            problems.append("output mismatch: %s" % json.dumps(out, sort_keys=True, ensure_ascii=False))
        if problems:
            failures.append({"case": i, "op": op, "problems": problems})
    return {"ok": not failures, "script": SCRIPT, "selftest": {"cases": len(EXAMPLES), "failed": failures}}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--schema" in argv:
        print(json.dumps(full_schema(), indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    if "--selftest" in argv:
        result = selftest()
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
        return 0 if result["ok"] else 1
    if not argv:
        print(json.dumps(_err("MISSING_OP", "op", "usage: protection_audit.py audit|levels < input.json")))
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
