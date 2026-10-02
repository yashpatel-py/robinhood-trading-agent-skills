#!/usr/bin/env python3
# synced from shared/scripts/order_lint.py; do not edit
"""order_lint.py - check an order ticket against the connector's documented rules before review.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: the review tools reject some bad orders, but several traps pass review and bite
later - a market order sent at 8pm quietly queues for the next open, a sell limit "at the ask"
never fills, an OCO leg 0.1% from the market is rejected at placement, a CURB option order fails
only at place time. And no tool stops an agent from inventing a quantity or a price. This linter
encodes the rules from the tool schemas (verified 2026-09-22) and makes provenance checkable:
every quantity and price must say it came from the user or from their saved config.

Input:  {"tool": "review_equity_order" | "review_advanced_order" | "review_option_order" |
                  "preview_crypto_order" | a place_* twin,
         "params": {...exact tool parameters...},
         "provenance": {"quantity": "user" | "user_config" | "user_intent_marketable", ...}
                       or the string "user" when a human approved every field (confirm gate),
         "context": {"agentic_allowed", "market_price", "bid", "ask", "session", "user_wants_immediate",
                     "option_level", "account_type", "retirement", "sellable_qty" | "position_qty" +
                     "held_qty", "lots_available", "chain_extended_hours_state", "underlying_type",
                     "multiplier", "min_order_quantity_increment", "pair_halted"}}
Output: {"ok": true, "valid": bool, "errors": [...], "warnings": [...], "not_checked": [...],
         "estimate": {...}, "fingerprint", "ticket_id", "place_tool", "place_params"}
A context value that is absent is reported under not_checked, never assumed fine.
Account numbers are masked in every output.

Usage:
    python3 order_lint.py lint < input.json > output.json
    python3 order_lint.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network, no file writes; one JSON object out; exit 0
whenever JSON was printed, exit 1 only on a crash.
"""

import copy
import json
import os
import re
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import reduce
from math import gcd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:  # canon.py is synced next to this file everywhere order_lint.py goes (skills and hooks/lib)
    import canon as _canon
except ImportError:  # pragma: no cover - a copied-alone script still lints; it just cannot fingerprint
    _canon = None

VERSION = "2.0.0"
SCRIPT = "order_lint"
MASK = "\u2022\u2022\u2022\u2022"
CENT = Decimal("0.01")
OCO_MIN_DISTANCE = Decimal("0.0025")
OCO_MIN_GAP = Decimal("0.10")
ESTIMATE_LABEL = "agent estimate \u2014 broker did not compute"
X = "\u00d7"  # multiplication sign used in estimate basis text

FAMILY = {
    "review_equity_order": "equity", "place_equity_order": "equity",
    "review_advanced_order": "oco", "place_advanced_order": "oco",
    "review_option_order": "option", "place_option_order": "option",
    "preview_crypto_order": "crypto", "place_crypto_order": "crypto",
}
PLACE_TWIN = {
    "review_equity_order": "place_equity_order", "review_advanced_order": "place_advanced_order",
    "review_option_order": "place_option_order", "preview_crypto_order": "place_crypto_order",
}
# Parameter names exactly as the live schemas list them (additionalProperties is false on all).
_EQ = {"account_number", "dollar_amount", "limit_price", "market_hours", "quantity", "side", "stop_price",
       "symbol", "tax_lots", "time_in_force", "type"}
_OCO = {"account_number", "market_hours", "quantity", "side", "stop_loss_stop_price", "symbol",
        "take_profit_limit_price", "time_in_force"}
_OPT_PLACE = {"account_number", "direction", "legs", "market_hours", "price", "quantity", "stop_price",
              "time_in_force", "type"}
_CRY = {"dollar_amount", "limit_price", "quantity", "rhs_account_number", "side", "stop_price", "symbol",
        "tax_lots", "time_in_force", "type"}
TOOL_PARAMS = {
    "review_equity_order": _EQ, "place_equity_order": _EQ | {"ref_id"},
    "review_advanced_order": _OCO, "place_advanced_order": _OCO | {"ref_id"},
    "review_option_order": _OPT_PLACE | {"chain_symbol", "underlying_type"},
    "place_option_order": _OPT_PLACE | {"ref_id"},
    "preview_crypto_order": _CRY, "place_crypto_order": _CRY | {"ref_id"},
}
REQUIRED = {
    "equity": ["account_number", "symbol", "side", "type"],
    "oco": ["account_number", "symbol", "side", "quantity", "take_profit_limit_price", "stop_loss_stop_price"],
    "option": ["account_number", "legs", "quantity"],
    "crypto": ["rhs_account_number", "symbol", "side", "type"],
}
REVIEW_ONLY = ("chain_symbol", "underlying_type")
LEG_KEYS = {"option_id", "side", "position_effect", "ratio_quantity"}
LOT_KEYS = {"open_lot_id", "quantity"}
USER_SOURCES = ("user", "user_config")
PROVENANCE_FIELDS = {
    "equity": ["quantity", "dollar_amount", "limit_price", "stop_price"],
    "oco": ["quantity", "take_profit_limit_price", "stop_loss_stop_price"],
    "option": ["quantity", "price", "stop_price"],
    "crypto": ["quantity", "dollar_amount", "limit_price", "stop_price"],
}
CURB = ("regular_curb_hours", "regular_curb_overnight_hours")


class InputError(Exception):
    def __init__(self, code, field, msg):
        Exception.__init__(self, msg)
        self.code, self.field, self.msg = code, field, msg


def _err(code, field, msg):
    return {"ok": False, "errors": [{"code": code, "field": field, "msg": msg}]}


def money(d):
    return str(d.quantize(CENT, rounding=ROUND_HALF_UP)) if d is not None else None


def _mask(value):
    s = str(value).strip()
    return MASK + s[-4:] if s else s


def _decimals(d):
    exp = d.normalize().as_tuple().exponent
    return -exp if isinstance(exp, int) and exp < 0 else 0


class Linter(object):
    def __init__(self, tool, params, provenance, context):
        self.tool = tool
        self.family = FAMILY[tool]
        self.p = params
        self.prov = provenance
        self.ctx = context
        self.errors, self.warnings, self.not_checked, self.notes = [], [], [], []
        self.estimate = None
        self._dec_cache = {}

    # -- recording -------------------------------------------------------------------------
    def error(self, code, field, msg):
        self.errors.append({"code": code, "field": field, "msg": msg})

    def warn(self, code, field, msg):
        self.warnings.append({"code": code, "field": field, "msg": msg})

    def skip(self, check, why):
        self.not_checked.append({"check": check, "why": why})

    # -- value access ----------------------------------------------------------------------
    def has(self, key):
        return key in self.p and self.p[key] is not None

    def enum(self, key, allowed, default=None, code="BAD_ENUM"):
        """Lowercased enum value (or default when absent); records BAD_ENUM / NOT_LOWERCASE."""
        if not self.has(key):
            return default
        raw = self.p[key]
        if not isinstance(raw, str):
            self.error("BAD_VALUE_TYPE", key, "%s must be a string" % key)
            return None
        low = raw.strip().lower()
        if low not in allowed:
            self.error(code, key, "%s must be one of %s (got %r)" % (key, ", ".join(allowed), raw))
            return None
        if raw != low:
            if key == "time_in_force":
                self.warn("UPPERCASE_TIF", key, "send time_in_force in lowercase (%r); normalized in place_params" % low)
            else:
                self.warn("NOT_LOWERCASE", key, "send %s in lowercase (%r)" % (key, low))
        return low

    def dec(self, key, value=None, positive=True):
        """Decimal from a decimal-string parameter; records BAD_VALUE_TYPE problems once per key."""
        if value is None and key in self._dec_cache:
            return self._dec_cache[key]
        result = self._dec(key, value, positive)
        if value is None:
            self._dec_cache[key] = result
        return result

    def _dec(self, key, value, positive):
        raw = self.p.get(key) if value is None else value
        if raw is None:
            return None
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
            self.error("BAD_VALUE_TYPE", key, "%s must be a decimal string like \"10\"" % key)
            return None
        if not isinstance(raw, str):
            self.error("BAD_VALUE_TYPE", key, "send %s as a string (\"%s\"), not a JSON number" % (key, raw))
            raw = repr(raw) if isinstance(raw, float) else str(raw)
        text = raw.strip()
        if not re.match(r"^[+-]?(\d+(\.\d*)?|\.\d+)$", text):
            self.error("BAD_VALUE_TYPE", key, "%s is not a plain decimal: %r" % (key, raw))
            return None
        d = Decimal(text)
        if positive and d <= 0:
            self.error("BAD_VALUE_TYPE", key, "%s must be greater than zero" % key)
            return None
        return d

    def cdec(self, key):
        """Decimal from context, or None (malformed context values are reported, not trusted)."""
        raw = self.ctx.get(key)
        if raw is None or isinstance(raw, bool):
            return None
        try:
            d = Decimal(repr(raw) if isinstance(raw, float) else str(raw).strip())
        except InvalidOperation:
            self.skip(key, "context %s is not a decimal" % key)
            return None
        return d if d.is_finite() else None

    def sellable(self):
        s = self.cdec("sellable_qty")
        if s is not None:
            return s
        pos, held = self.cdec("position_qty"), self.cdec("held_qty")
        if pos is not None:
            return pos - (held or Decimal(0))
        return None

    # -- shared checks ---------------------------------------------------------------------
    def check_params_known(self):
        allowed = TOOL_PARAMS[self.tool]
        for key in self.p:
            if key not in allowed:
                why = "not a parameter of %s (the tool rejects unknown keys)" % self.tool
                if key in REVIEW_ONLY and self.tool == "place_option_order":
                    why = "%s is review-only; drop it for place_option_order" % key
                if key == "ref_id":
                    why = "ref_id belongs to place_* calls only"
                self.error("UNKNOWN_PARAM", key, why)

    def check_required(self):
        for key in REQUIRED[self.family]:
            if not self.has(key):
                self.error("MISSING_REQUIRED", key, "%s is required by %s" % (key, self.tool))

    def check_agentic(self):
        allowed = self.ctx.get("agentic_allowed")
        if allowed is False:
            what = "crypto orders need an agentic-enabled crypto account" if self.family == "crypto" else \
                "review and place tools accept only the account with agentic_allowed=true"
            self.error("NOT_AGENTIC_ACCOUNT", "account_number" if self.family != "crypto" else "rhs_account_number",
                       "%s; use a manual ticket for this account" % what)
        elif allowed is None:
            self.skip("agentic account", "context.agentic_allowed not given")

    def check_provenance(self, side, order_type):
        for key in PROVENANCE_FIELDS[self.family]:
            if not self.has(key):
                continue
            src = self.prov.get(key)
            if src in USER_SOURCES:
                continue
            if (src == "user_intent_marketable" and key == "limit_price" and self.family in ("equity", "crypto")
                    and order_type == "limit"):
                ok, why = self.marketable_basis(side)
                if ok:
                    self.notes.append(why)
                    continue
                self.error("NO_USER_SOURCE", key, "limit_price marked user_intent_marketable but %s" % why)
                continue
            got = "no source recorded" if src is None else "source %r" % src
            self.error("NO_USER_SOURCE", key,
                       "%s for %s: quantities and prices must come from the user or their saved config "
                       "(provenance user | user_config); never choose one" % (got, key))

    def marketable_basis(self, side):
        if self.ctx.get("user_wants_immediate") is not True:
            return False, "the user did not ask for an immediate fill"
        limit = self.dec("limit_price")
        if limit is None:
            return False, "limit_price is not a valid decimal"
        if side == "buy":
            ask = self.cdec("ask")
            if ask is None:
                return False, "the current ask is not in context"
            if limit < ask:
                return False, "a buy limit below the ask (%s) is not marketable" % ask
            return True, "limit priced as a marketable buy at or above the ask (%s) because the user asked for an " \
                         "immediate fill; state this basis on the ticket" % ask
        if side == "sell":
            bid = self.cdec("bid")
            if bid is None:
                return False, "the current bid is not in context"
            if limit > bid:
                return False, "a sell limit above the bid (%s) is not marketable" % bid
            return True, "limit priced as a marketable sell at or below the bid (%s) because the user asked for an " \
                         "immediate fill; state this basis on the ticket" % bid
        return False, "side is unknown"

    def check_marketable_warning(self, side, order_type):
        wants = self.ctx.get("user_wants_immediate") is True or self.prov.get("limit_price") == "user_intent_marketable"
        if order_type != "limit" or not wants:
            return
        limit = self.dec("limit_price")
        if limit is None:
            return
        if side == "buy":
            ask = self.cdec("ask")
            if ask is None:
                self.skip("marketable limit", "context.ask not given")
            elif limit < ask:
                self.warn("BUY_LIMIT_NOT_MARKETABLE", "limit_price",
                          "buy limit %s is below the ask %s, so it will not fill immediately" % (limit, ask))
        elif side == "sell":
            bid = self.cdec("bid")
            if bid is None:
                self.skip("marketable limit", "context.bid not given")
            elif limit > bid:
                self.warn("SELL_LIMIT_NOT_MARKETABLE", "limit_price",
                          "sell limit %s is above the bid %s (a sell at the ask is not marketable), so it will "
                          "not fill immediately" % (limit, bid))

    def check_session_queue(self, order_type, market_hours):
        session = self.ctx.get("session")
        if session is None:
            self.skip("session timing", "context.session not given (run rh_time.py session)")
            return
        if session != "regular" and market_hours == "regular_hours":
            if order_type in ("market", "stop_market", "stop_limit"):
                msg = ("the regular session is closed; this %s order is tagged regular_hours, so it queues and "
                       "executes at the next regular open at whatever price the open brings" % order_type)
            else:
                msg = "the regular session is closed; a regular_hours order waits for the next regular open"
            self.warn("QUEUES_NEXT_OPEN", "market_hours", msg)

    # -- equity ----------------------------------------------------------------------------
    def lint_equity(self):
        side = self.enum("side", ("buy", "sell"))
        otype = self.enum("type", ("market", "limit", "stop_market", "stop_limit"))
        self.enum("time_in_force", ("gfd", "gtc"), "gfd")
        mh = self.enum("market_hours", ("regular_hours", "extended_hours", "all_day_hours"), "regular_hours")
        has_q, has_d = self.has("quantity"), self.has("dollar_amount")
        if has_q == has_d:
            self.error("ONE_OF_QTY_OR_DOLLAR", "quantity", "provide exactly one of quantity or dollar_amount")
        qty = self.dec("quantity") if has_q else None
        dollars = self.dec("dollar_amount") if has_d else None
        limit = self.dec("limit_price") if self.has("limit_price") else None
        stop = self.dec("stop_price") if self.has("stop_price") else None
        if has_d and otype and otype != "market":
            self.error("DOLLAR_REQUIRES_MARKET", "dollar_amount", "dollar_amount works only with type=market")
        if otype in ("limit", "stop_limit") and not self.has("limit_price"):
            self.error("LIMIT_PRICE_REQUIRED", "limit_price", "limit_price is required for %s" % otype)
        if otype in ("stop_market", "stop_limit") and not self.has("stop_price"):
            self.error("STOP_PRICE_REQUIRED", "stop_price", "stop_price is required for %s" % otype)
        if otype in ("market", "stop_market", "stop_limit") and mh in ("extended_hours", "all_day_hours"):
            self.error("NON_REGULAR_REQUIRES_LIMIT", "market_hours",
                       "%s orders are regular_hours only and are rejected in %s; outside regular hours only limit "
                       "orders execute" % (otype, mh))
        fractional = qty is not None and qty != qty.to_integral_value()
        if qty is not None and _decimals(qty) > 6:
            self.error("MAX_6_DECIMALS", "quantity", "fractional quantities allow at most 6 decimal places")
        if fractional and otype and otype != "market":
            self.error("FRACTIONAL_REQUIRES_MARKET_REGULAR", "quantity",
                       "fractional shares need type=market and market_hours=regular_hours")
        if (has_d or fractional) and mh and mh != "regular_hours":
            self.error("DOLLAR_OR_FRACTIONAL_REGULAR_ONLY", "market_hours",
                       "dollar-based and fractional orders place only in regular_hours")
        self.lint_tax_lots(side, otype, mh, qty, fractional, has_d)
        self.check_agentic()
        if side == "sell" and qty is not None:
            sellable = self.sellable()
            if sellable is None:
                self.skip("sellable shares", "context.sellable_qty (shares_available_for_sells) not given")
            elif qty > sellable:
                self.error("EXCEEDS_SELLABLE", "quantity",
                           "sell quantity %s exceeds the %s shares available to sell" % (qty, sellable))
        self.check_session_queue(otype, mh)
        self.check_marketable_warning(side, otype)
        self.check_provenance(side, otype)
        self.estimate = self.estimate_equity(side, otype, qty, dollars, limit, stop)

    def lint_tax_lots(self, side, otype, mh, qty, fractional, has_d):
        if not self.has("tax_lots"):
            return
        lots = self.p["tax_lots"]
        if not isinstance(lots, list):
            self.error("BAD_VALUE_TYPE", "tax_lots", "tax_lots must be a list of {open_lot_id, quantity}")
            return
        if side and side != "sell":
            self.error("TAX_LOTS_SELL_ONLY", "tax_lots", "tax_lots is for sell orders only")
        if len(lots) > 30:
            self.error("TAX_LOTS_MAX_30", "tax_lots", "at most 30 lots per order (got %d)" % len(lots))
        if has_d:
            self.error("TAX_LOTS_NOT_ALLOWED_WITH", "dollar_amount", "tax_lots cannot be combined with dollar_amount")
        if otype in ("stop_market", "stop_limit"):
            self.error("TAX_LOTS_NOT_ALLOWED_WITH", "type", "tax_lots cannot be combined with stop orders")
        if mh == "all_day_hours":
            self.error("TAX_LOTS_NOT_ALLOWED_WITH", "market_hours", "tax_lots cannot be used in all_day_hours")
        if fractional and otype == "limit":
            self.error("TAX_LOTS_NOT_ALLOWED_WITH", "quantity", "tax_lots cannot be used on a fractional limit order")
        total = Decimal(0)
        by_lot = {}
        sum_ok = True
        for i, lot in enumerate(lots):
            where = "tax_lots[%d]" % i
            if not isinstance(lot, dict):
                self.error("BAD_VALUE_TYPE", where, "each lot is {open_lot_id, quantity}")
                sum_ok = False
                continue
            for key in lot:
                if key not in LOT_KEYS:
                    self.error("UNKNOWN_PARAM", "%s.%s" % (where, key), "lots take only open_lot_id and quantity")
            lot_id = lot.get("open_lot_id")
            if not isinstance(lot_id, str) or not lot_id.strip():
                self.error("MISSING_REQUIRED", where + ".open_lot_id", "open_lot_id from get_equity_tax_lots")
            q = self.dec(where + ".quantity", value=lot.get("quantity")) if lot.get("quantity") is not None else None
            if lot.get("quantity") is None:
                self.error("MISSING_REQUIRED", where + ".quantity", "each lot needs a quantity")
            if q is None:
                sum_ok = False
                continue
            total += q
            if isinstance(lot_id, str):
                by_lot[lot_id.strip()] = by_lot.get(lot_id.strip(), Decimal(0)) + q
        if qty is not None and sum_ok and total != qty:
            self.error("TAX_LOTS_SUM_MISMATCH", "tax_lots",
                       "lot quantities sum to %s but the order quantity is %s; they must match exactly" % (total, qty))
        available = self.ctx.get("lots_available")
        if not isinstance(available, dict):
            self.skip("tax lot availability", "context.lots_available (quantity_available per open_lot_id) not given")
            return
        for lot_id, q in sorted(by_lot.items()):
            if lot_id not in available:
                self.error("TAX_LOTS_EXCEEDS_AVAILABLE", "tax_lots",
                           "lot %s is not among the lots read from get_equity_tax_lots" % lot_id)
                continue
            try:
                avail = Decimal(str(available[lot_id]).strip())
            except InvalidOperation:
                self.skip("tax lot %s" % lot_id, "quantity_available is not a decimal")
                continue
            if q > avail:
                self.error("TAX_LOTS_EXCEEDS_AVAILABLE", "tax_lots",
                           "lot %s: %s requested but only %s available" % (lot_id, q, avail))

    def estimate_equity(self, side, otype, qty, dollars, limit, stop):
        est = {"usd": None, "basis": None, "worst_case_usd": None, "label": ESTIMATE_LABEL}
        if dollars is not None:
            est.update(usd=money(dollars), basis="dollar_amount (the server sizes shares from last_trade_price)")
        elif qty is None:
            est["basis"] = "not computable (no valid quantity)"
        elif otype in ("limit", "stop_limit") and limit is not None:
            est.update(usd=money(qty * limit), basis="qty" + X + "limit")
        elif otype == "market":
            key = "ask" if side == "buy" else "bid"
            px = self.cdec(key)
            if px is None:
                est["basis"] = "qty%s%s (%s not in context)" % (X, key, key)
            else:
                est.update(usd=money(qty * px), basis="qty%s%s" % (X, key))
        elif otype == "stop_market" and stop is not None:
            est.update(usd=money(qty * stop), basis="qty" + X + "stop (trigger basis; a gap can fill far from the stop)")
        else:
            est["basis"] = "not computable"
        est["note"] = "review_equity_order returns a quote and alerts, not a cost; this figure is the kit's"
        return est

    # -- OCO -------------------------------------------------------------------------------
    def lint_oco(self):
        side = self.enum("side", ("buy", "sell"))
        if self.has("time_in_force"):
            self.enum("time_in_force", ("gfd", "gtc"), code="BAD_TIF")
        else:
            self.warn("TIF_NOT_STATED", "time_in_force",
                      "state gfd or gtc explicitly; a gfd OCO expires at today's close and leaves the position "
                      "unprotected tomorrow")
        if self.has("market_hours"):
            raw = self.p["market_hours"]
            if not isinstance(raw, str) or raw.strip().lower() != "regular_hours":
                self.error("SESSION_NOT_REGULAR", "market_hours",
                           "OCO orders support regular_hours only; omit market_hours or pass regular_hours")
        qty = self.dec("quantity") if self.has("quantity") else None
        if qty is not None and qty != qty.to_integral_value():
            self.error("WHOLE_SHARES", "quantity",
                       "OCO quantity must be whole shares; a fractional remainder stays unprotected")
        tp = self.dec("take_profit_limit_price") if self.has("take_profit_limit_price") else None
        sl = self.dec("stop_loss_stop_price") if self.has("stop_loss_stop_price") else None
        if tp is not None and sl is not None:
            if tp == sl:
                self.error("PRICES_MUST_DIFFER", "take_profit_limit_price",
                           "take-profit and stop-loss prices must differ")
            else:
                if side == "sell" and not tp > sl:
                    self.error("SIDE_ORDER", "take_profit_limit_price",
                               "for a sell OCO (protecting a long) the take-profit must be above the stop")
                if side == "buy" and not sl > tp:
                    self.error("SIDE_ORDER", "stop_loss_stop_price",
                               "for a buy OCO (covering a short) the stop must be above the take-profit")
                if abs(tp - sl) < OCO_MIN_GAP:
                    self.error("MIN_GAP_0_10", "stop_loss_stop_price",
                               "the legs are %s apart; they must be at least $0.10 apart" % abs(tp - sl))
        market = self.cdec("market_price")
        if market is None or market <= 0:
            self.skip("OCO 0.25% distance and trigger checks", "context.market_price not given")
        else:
            for key, px in (("take_profit_limit_price", tp), ("stop_loss_stop_price", sl)):
                if px is None:
                    continue
                dist = abs(px - market) / market
                if dist < OCO_MIN_DISTANCE:
                    self.error("MIN_DISTANCE_0_25PCT", key,
                               "%s is %s%% from the market price %s; each leg must be at least 0.25%% away"
                               % (px, (dist * 100).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP), market))
            if sl is not None and side in ("buy", "sell"):
                if (side == "sell" and sl >= market) or (side == "buy" and sl <= market):
                    self.warn("STOP_WOULD_TRIGGER", "stop_loss_stop_price",
                              "the stop %s is already through the market price %s and would trigger at once"
                              % (sl, market))
            if tp is not None and side in ("buy", "sell"):
                ref = self.cdec("bid" if side == "sell" else "ask") or market
                if (side == "sell" and tp <= ref) or (side == "buy" and tp >= ref):
                    self.warn("TP_WOULD_FILL", "take_profit_limit_price",
                              "the take-profit %s is marketable now (reference %s) and would fill at once" % (tp, ref))
        self.check_agentic()
        if side == "sell" and qty is not None:
            sellable = self.sellable()
            if sellable is None:
                self.skip("sellable shares", "context.sellable_qty (shares_available_for_sells) not given")
            elif qty > sellable:
                self.error("QTY_EXCEEDS_SELLABLE", "quantity",
                           "OCO quantity %s exceeds the %s shares available to sell" % (qty, sellable))
        self.check_provenance(side, "oco")
        self.notes.append("if review_advanced_order answers 'the tool you requested cannot be found or does not "
                          "exist', the OCO family is not enabled for this account: fall back to a stop order via "
                          "review_equity_order or a price alert; never report that no OCOs exist")
        est = {"usd": None, "basis": "take-profit \u00d7 qty / stop \u00d7 qty (the stop leg is stop-market: a gap "
                                     "can fill below the stop)",
               "worst_case_usd": None, "label": ESTIMATE_LABEL, "outcomes": {"take_profit_usd": None, "stop_usd": None}}
        if qty is not None:
            if tp is not None:
                est["usd"] = est["outcomes"]["take_profit_usd"] = money(qty * tp)
            if sl is not None:
                est["worst_case_usd"] = est["outcomes"]["stop_usd"] = money(qty * sl)
        self.estimate = est

    # -- options ---------------------------------------------------------------------------
    def lint_option(self):
        legs = self.p.get("legs")
        leg_list = legs if isinstance(legs, list) else []
        if legs is not None and not isinstance(legs, list):
            self.error("BAD_VALUE_TYPE", "legs", "legs must be a list")
        if isinstance(legs, list) and not 1 <= len(legs) <= 4:
            self.error("LEGS_1_TO_4", "legs", "an option order has 1 to 4 legs (got %d)" % len(legs))
        ids, ratios, sides, effects = [], [], [], []
        for i, leg in enumerate(leg_list):
            where = "legs[%d]" % i
            if not isinstance(leg, dict):
                self.error("BAD_VALUE_TYPE", where, "each leg is {option_id, side, position_effect, ratio_quantity}")
                continue
            for key in leg:
                if key not in LEG_KEYS:
                    self.error("UNKNOWN_PARAM", "%s.%s" % (where, key), "legs take option_id, side, position_effect, "
                                                                        "ratio_quantity only")
            oid = leg.get("option_id")
            if not isinstance(oid, str) or not oid.strip():
                self.error("MISSING_REQUIRED", where + ".option_id", "option_id from get_option_instruments")
            else:
                ids.append(oid.strip().lower())
            for key, allowed, bucket in (("side", ("buy", "sell"), sides),
                                         ("position_effect", ("open", "close"), effects)):
                val = leg.get(key)
                if val is None:
                    self.error("MISSING_REQUIRED", "%s.%s" % (where, key), "%s is required on every leg" % key)
                    bucket.append(None)
                elif not isinstance(val, str) or val.strip().lower() not in allowed:
                    self.error("BAD_ENUM", "%s.%s" % (where, key), "%s must be %s" % (key, " or ".join(allowed)))
                    bucket.append(None)
                else:
                    bucket.append(val.strip().lower())
            ratio = leg.get("ratio_quantity", 1)
            if isinstance(ratio, bool) or not isinstance(ratio, int) or ratio < 1:
                self.error("BAD_VALUE_TYPE", where + ".ratio_quantity", "ratio_quantity is a positive integer "
                                                                         "(JSON number, not a string)")
                ratios.append(1)
            else:
                ratios.append(ratio)
        if len(ids) != len(set(ids)):
            self.error("DUPLICATE_CONTRACT", "legs", "each leg must be a different contract; use ratio_quantity instead")
        n = len(leg_list)
        if n == 1 and ratios and ratios[0] != 1:
            self.error("SINGLE_LEG_RATIO_MUST_BE_1", "legs[0].ratio_quantity", "a single-leg order uses ratio 1")
        if n >= 2 and ratios and reduce(gcd, ratios) > 1:
            self.error("RATIO_NOT_LOWEST_TERMS", "legs", "leg ratios must be in lowest terms (1:2, not 2:4)")
        if n >= 2 and not self.has("direction"):
            self.error("DIRECTION_REQUIRED_MULTI", "direction", "direction (debit or credit) is required with 2+ legs")
        if n == 1 and self.has("direction"):
            self.error("DIRECTION_OMIT_SINGLE", "direction", "omit direction on a single leg; it is derived from the side")
        direction = self.enum("direction", ("debit", "credit")) if self.has("direction") else None
        otype = self.enum("type", ("limit", "market", "stop_limit", "stop_market"), "limit")
        tif = self.enum("time_in_force", ("gfd", "gtc"), "gfd")
        raw_mh = self.p.get("market_hours")
        mh = "regular_hours" if raw_mh is None else (raw_mh.strip().lower() if isinstance(raw_mh, str) else raw_mh)
        if mh in ("extended_hours", "all_day_hours"):
            self.error("EQUITY_SESSION_ON_OPTION", "market_hours",
                       "%s is an equity session; options take regular_hours, regular_curb_hours or "
                       "regular_curb_overnight_hours" % mh)
            mh = None
        elif mh not in ("regular_hours",) + CURB:
            self.error("BAD_ENUM", "market_hours", "market_hours must be regular_hours, regular_curb_hours or "
                                                   "regular_curb_overnight_hours")
            mh = None
        if n >= 2 and otype and otype != "limit":
            self.error("MULTI_LEG_LIMIT_ONLY", "type", "only limit orders are available with 2 or more legs")
        if otype in ("limit", "stop_limit") and not self.has("price"):
            self.error("PRICE_REQUIRED", "price", "price is required for %s" % otype)
        if otype in ("market", "stop_market") and self.has("price"):
            self.error("PRICE_MUST_BE_OMITTED", "price", "omit price for %s" % otype)
        if otype in ("stop_limit", "stop_market") and not self.has("stop_price"):
            self.error("STOP_PRICE_RULES", "stop_price", "stop_price is required for %s" % otype)
        if otype in ("limit", "market") and self.has("stop_price"):
            self.error("STOP_PRICE_RULES", "stop_price", "omit stop_price for %s" % otype)
        if otype in ("market", "stop_market") and (tif not in (None, "gfd") or (mh and mh != "regular_hours")):
            self.error("MARKET_STOP_GFD_REGULAR_ONLY", "time_in_force" if tif != "gfd" else "market_hours",
                       "%s option orders must be gfd and regular_hours" % otype)
        if otype == "stop_limit" and mh in CURB:
            self.error("NON_LIMIT_SINGLE_LEG_ONLY", "market_hours",
                       "non-limit types are single-leg, regular_hours orders; CURB sessions take limit orders only")
        price = self.dec("price") if self.has("price") else None
        stop = self.dec("stop_price") if self.has("stop_price") else None
        if otype == "stop_market" and n == 1:
            if sides and effects and (sides[0] != "sell" or effects[0] != "close"):
                self.error("STOP_MARKET_SELL_TO_CLOSE_ONLY", "legs[0]",
                           "stop_market option orders are sell-to-close only")
            ask = self.cdec("ask")
            if stop is not None:
                if ask is None:
                    self.skip("stop below ask", "context.ask (the contract's ask) not given")
                elif stop >= ask:
                    self.error("STOP_BELOW_ASK", "stop_price",
                               "a sell stop_market needs stop_price below the current ask (%s)" % ask)
        if mh in CURB:
            utype = self.p.get("underlying_type") or self.ctx.get("underlying_type")
            state = self.ctx.get("chain_extended_hours_state")
            if utype != "index" or state != "enabled":
                self.error("CURB_REQUIRES_INDEX_ENABLED", "market_hours",
                           "CURB sessions need an index chain with extended_hours_state 'enabled' (from "
                           "get_option_chains); otherwise the order is rejected at place time")
        if self.has("underlying_type"):
            ut = self.p["underlying_type"]
            if not isinstance(ut, str) or ut.strip().lower() not in ("equity", "index"):
                self.error("BAD_ENUM", "underlying_type", "underlying_type must be equity or index")
        q_raw = self.p.get("quantity")
        qty = None
        if q_raw is not None:
            if not isinstance(q_raw, str) or not re.match(r"^\s*[1-9]\d*\s*$", q_raw):
                self.error("QTY_POSITIVE_INT_STRING", "quantity",
                           "quantity is a positive whole number of contracts, sent as a string (\"2\")")
            else:
                qty = int(q_raw)
        if "option_level" in self.ctx:
            level = self.ctx.get("option_level") or ""
            need = "option_level_3" if n >= 2 else "option_level_2"
            ok_levels = ("option_level_3",) if n >= 2 else ("option_level_2", "option_level_3")
            if level not in ok_levels:
                self.error("LEVEL_INSUFFICIENT", "account_number",
                           "this order needs %s; the account has %s. Route the user to enrollment; do not call the "
                           "review" % (need, level or "no options level"))
        else:
            self.skip("options level", "context.option_level not given (re-fetch get_accounts)")
        if n >= 2:
            acct_type, retirement = self.ctx.get("account_type"), self.ctx.get("retirement")
            if acct_type == "cash" or retirement is True:
                self.error("MULTI_LEG_CASH_OR_RETIREMENT", "legs",
                           "multi-leg orders are not available on cash or retirement accounts through these tools")
            elif acct_type is None or retirement is None:
                self.skip("multi-leg account type", "context.account_type and context.retirement not both given")
        self.check_agentic()
        if self.tool == "review_option_order" and not (self.has("chain_symbol") and self.has("underlying_type")):
            self.warn("SEND_CHAIN_SYMBOL_AND_UNDERLYING_TYPE", "chain_symbol",
                      "send chain_symbol and underlying_type so the review returns fees and collateral")
        self.check_session_queue(otype, mh)
        self.check_provenance(sides[0] if n == 1 and sides else None, otype)
        self.estimate = self.estimate_option(n, otype, qty, price, stop, direction, sides, ratios)

    def estimate_option(self, n, otype, qty, price, stop, direction, sides, ratios):
        mult = self.cdec("multiplier") or Decimal(100)
        if mult != 100:
            self.warn("NON_STANDARD_MULTIPLIER", "legs",
                      "contract multiplier is %s, not 100 (adjusted contract); check the deliverable" % mult)
        est = {"usd": None, "basis": None, "worst_case_usd": None, "label": ESTIMATE_LABEL,
               "contracts_per_leg": [str(qty * r) for r in ratios] if qty else []}
        if qty is None:
            est["basis"] = "not computable (no valid quantity)"
        elif n >= 2:
            if price is not None:
                paid = "debit paid" if direction == "debit" else "credit received" if direction == "credit" else "net"
                est.update(usd=money(price * mult * qty), basis="net \u00d7 100 \u00d7 qty (%s)" % paid)
            else:
                est["basis"] = "not computable (no net price)"
        elif otype in ("limit", "stop_limit") and price is not None:
            est.update(usd=money(price * mult * qty), basis="price \u00d7 100 \u00d7 qty")
        elif otype == "market" and sides:
            key = "ask" if sides[0] == "buy" else "bid"
            px = self.cdec(key)
            if px is None:
                est["basis"] = "%s \u00d7 100 \u00d7 qty (%s not in context)" % (key, key)
            else:
                est.update(usd=money(px * mult * qty), basis="%s \u00d7 100 \u00d7 qty" % key)
        elif otype == "stop_market" and stop is not None:
            est.update(usd=money(stop * mult * qty), basis="stop \u00d7 100 \u00d7 qty (trigger basis; gap risk)")
        else:
            est["basis"] = "not computable"
        est["note"] = "with chain_symbol and underlying_type the review returns fees and collateral; show the " \
                      "broker's figure when it does"
        return est

    # -- crypto ----------------------------------------------------------------------------
    def lint_crypto(self):
        side = self.enum("side", ("buy", "sell"))
        otype = None
        if self.has("type"):
            raw = self.p["type"]
            low = raw.strip().lower() if isinstance(raw, str) else None
            if low in ("market", "limit", "stop_loss", "stop_limit"):
                otype = low
                if raw != low:
                    self.warn("NOT_LOWERCASE", "type", "send type in lowercase (%r)" % low)
            else:
                hint = " (crypto calls a stop-market order stop_loss)" if low in ("stop_market", "stop") else ""
                self.error("BAD_TYPE", "type", "crypto type must be market, limit, stop_loss or stop_limit%s" % hint)
        has_q, has_d = self.has("quantity"), self.has("dollar_amount")
        if has_q == has_d:
            self.error("ONE_OF_QTY_OR_DOLLAR", "quantity", "provide exactly one of quantity or dollar_amount")
        qty = self.dec("quantity") if has_q else None
        dollars = self.dec("dollar_amount") if has_d else None
        limit = self.dec("limit_price") if self.has("limit_price") else None
        stop = self.dec("stop_price") if self.has("stop_price") else None
        if otype in ("limit", "stop_limit") and not self.has("limit_price"):
            self.error("LIMIT_PRICE_REQUIRED", "limit_price", "limit_price is required for %s" % otype)
        if otype in ("stop_loss", "stop_limit") and not self.has("stop_price"):
            self.error("STOP_PRICE_REQUIRED", "stop_price", "stop_price is required for %s" % otype)
        if self.has("time_in_force"):
            raw = self.p["time_in_force"]
            tif = raw.strip().lower() if isinstance(raw, str) else None
            if tif == "ioc":
                self.error("IOC_NEVER", "time_in_force", "ioc is never supported for crypto orders")
            elif otype in ("market", "limit") and tif != "gtc":
                self.error("TIF_MARKET_LIMIT_GTC_ONLY", "time_in_force",
                           "crypto market and limit orders take gtc only (limit orders last 90 days); omit it or send gtc")
            elif otype in ("stop_loss", "stop_limit") and tif not in ("gtc", "gfd", "gfw", "gfm"):
                self.error("TIF_STOP_VALUES", "time_in_force", "crypto stop orders take gtc (90 days), gfd, gfw or gfm")
            elif tif and raw != tif:
                self.warn("UPPERCASE_TIF", "time_in_force", "send time_in_force in lowercase (%r)" % tif)
        elif otype in ("stop_loss", "stop_limit"):
            self.warn("STOP_TIF_DEFAULTS_TO_DAY", "time_in_force",
                      "with no time_in_force a crypto stop is good for the day only (gfd); state gtc, gfw or gfm if "
                      "the user wants it to last")
        if self.has("tax_lots"):
            self.error("TAX_LOTS_UNAVAILABLE", "tax_lots",
                       "get_crypto_tax_lots is not exposed, so specific-lot crypto sells cannot be built; the user "
                       "does them in the app")
        if qty is not None:
            inc = self.cdec("min_order_quantity_increment")
            if inc is None or inc <= 0:
                self.skip("quantity increment", "context.min_order_quantity_increment (get_currency_pairs) not given")
            elif qty % inc != 0:
                self.error("OFF_INCREMENT", "quantity",
                           "quantity %s is not a multiple of the pair's increment %s" % (qty, inc))
        halted = self.ctx.get("pair_halted")
        if halted is True:
            self.error("PAIR_HALTED", "symbol", "this currency pair has an active trading halt")
        elif halted is None:
            self.skip("trading halt", "context.pair_halted (get_currency_pairs) not given")
        if otype == "market" and side == "sell" and has_d:
            self.warn("SELL_COLLAR_5PCT", "dollar_amount",
                      "a market sell sized in dollars can come back up to about 5% light (the sell collar)")
        self.check_agentic()
        self.check_provenance(side, otype)
        self.estimate = self.estimate_crypto(side, otype, qty, dollars, limit, stop)

    def estimate_crypto(self, side, otype, qty, dollars, limit, stop):
        est = {"usd": None, "basis": None, "worst_case_usd": None, "label": ESTIMATE_LABEL}
        up, down = Decimal("1.01"), Decimal("0.95")
        if otype == "market":
            key = "ask" if side == "buy" else "bid"
            px = self.cdec(key)
            base = dollars if dollars is not None else (qty * px if qty is not None and px is not None else None)
            if base is not None:
                worst = base * (up if side == "buy" else down)
                est.update(usd=money(base), worst_case_usd=money(worst),
                           basis="%s; worst case %s (the %s collar)" % (
                               "dollar_amount" if dollars is not None else "qty" + X + key,
                               "+1%" if side == "buy" else "\u22125%", "buy" if side == "buy" else "sell"))
            else:
                est["basis"] = "not computable (%s not in context)" % key
        elif otype == "stop_loss":
            base = dollars if dollars is not None else (qty * stop if qty is not None and stop is not None else None)
            if base is not None:
                worst = base * (up if side == "buy" else down)
                est.update(usd=money(base), worst_case_usd=money(worst),
                           basis="sized at the stop; after the trigger it fills as a market order, worst case %s"
                                 % ("+1%" if side == "buy" else "\u22125%"))
        elif otype in ("limit", "stop_limit"):
            base = dollars if dollars is not None else (qty * limit if qty is not None and limit is not None else None)
            if base is not None:
                est.update(usd=money(base), basis="sized at the limit (fills at the limit or better)")
        if est["basis"] is None:
            est["basis"] = "not computable"
        est["note"] = "preview_crypto_order returns the broker's estimated cost or credit and fees; that figure wins"
        return est

    # -- run -------------------------------------------------------------------------------
    def run(self):
        self.check_params_known()
        self.check_required()
        getattr(self, "lint_" + self.family)()
        return self.errors, self.warnings


def place_params(tool, params):
    out = {}
    for key, val in params.items():
        if key == "ref_id" or (key in REVIEW_ONLY and FAMILY[tool] == "option") or val is None:
            continue
        if key in ("account_number", "rhs_account_number") and isinstance(val, str):
            out[key] = _mask(val)
        elif key in ("side", "type", "time_in_force", "market_hours", "direction") and isinstance(val, str):
            out[key] = val.strip().lower()
        else:
            out[key] = copy.deepcopy(val)
    return out


def op_lint(data):
    tool = data.get("tool")
    if not isinstance(tool, str) or not tool:
        raise InputError("MISSING_FIELD", "tool", "give the order tool name, e.g. review_equity_order")
    bare = tool.rsplit("__", 1)[-1]
    if bare == "exercise_option":
        raise InputError("UNKNOWN_TOOL", "tool", "exercise_option has no review twin and this kit never calls it; "
                                                 "prepare an exercise handoff for the app instead")
    if bare not in FAMILY:
        raise InputError("UNKNOWN_TOOL", "tool", "order_lint covers the review/preview/place order tools, not %r" % tool)
    params = data.get("params")
    if not isinstance(params, dict):
        raise InputError("BAD_INPUT", "params", "params must be an object with the exact tool parameters")
    provenance = data.get("provenance") or {}
    context = data.get("context") or {}
    if provenance in USER_SOURCES:
        # Shorthand for callers where a human approved every field (the confirm gate's prompt).
        provenance = {key: provenance for key in PROVENANCE_FIELDS[FAMILY.get(bare, "equity")]}
    if not isinstance(provenance, dict) or not isinstance(context, dict):
        raise InputError("BAD_INPUT", "provenance", "provenance and context must be objects")
    linter = Linter(bare, params, provenance, context)
    errors, warnings = linter.run()
    fp = {"fingerprint": None, "ticket_id": None}
    if _canon is not None:
        try:
            fp = _canon.fingerprint(bare, params)
        except Exception:  # canon never blocks a lint result
            linter.notes.append("fingerprint unavailable for these parameters")
    else:
        linter.notes.append("canon.py not found next to order_lint.py; no fingerprint")
    return {
        "ok": True,
        "valid": not errors,
        "tool": bare,
        "family": FAMILY[bare],
        "errors": errors,
        "warnings": warnings,
        "not_checked": linter.not_checked,
        "notes": linter.notes,
        "estimate": linter.estimate,
        "fingerprint": fp["fingerprint"],
        "ticket_id": fp["ticket_id"],
        "place_tool": PLACE_TWIN.get(bare, bare),
        "place_params": place_params(bare, params),
    }


def lint(data):
    """Library entry point (hooks/lib and the confirm gate import this)."""
    return run("lint", data)


OPS = {"lint": op_lint}


def run(op, data):
    if op not in OPS:
        return _err("UNKNOWN_OP", "op", "unknown op %r; expected lint" % op)
    if not isinstance(data, dict):
        return _err("BAD_INPUT", "", "input must be a JSON object")
    try:
        return OPS[op](data)
    except InputError as exc:
        return _err(exc.code, exc.field, exc.msg)


# Every code this linter can emit, for documentation and tests.
ERROR_CODES = sorted([
    "UNKNOWN_PARAM", "MISSING_REQUIRED", "BAD_ENUM", "BAD_VALUE_TYPE", "NO_USER_SOURCE", "NOT_AGENTIC_ACCOUNT",
    "ONE_OF_QTY_OR_DOLLAR", "DOLLAR_REQUIRES_MARKET", "LIMIT_PRICE_REQUIRED", "STOP_PRICE_REQUIRED",
    "NON_REGULAR_REQUIRES_LIMIT", "FRACTIONAL_REQUIRES_MARKET_REGULAR", "MAX_6_DECIMALS",
    "DOLLAR_OR_FRACTIONAL_REGULAR_ONLY", "TAX_LOTS_SELL_ONLY", "TAX_LOTS_MAX_30", "TAX_LOTS_SUM_MISMATCH",
    "TAX_LOTS_EXCEEDS_AVAILABLE", "TAX_LOTS_NOT_ALLOWED_WITH", "EXCEEDS_SELLABLE",
    "WHOLE_SHARES", "SIDE_ORDER", "PRICES_MUST_DIFFER", "MIN_DISTANCE_0_25PCT", "MIN_GAP_0_10",
    "SESSION_NOT_REGULAR", "BAD_TIF", "QTY_EXCEEDS_SELLABLE",
    "LEGS_1_TO_4", "DUPLICATE_CONTRACT", "RATIO_NOT_LOWEST_TERMS", "SINGLE_LEG_RATIO_MUST_BE_1",
    "DIRECTION_REQUIRED_MULTI", "DIRECTION_OMIT_SINGLE", "MULTI_LEG_LIMIT_ONLY", "PRICE_REQUIRED",
    "PRICE_MUST_BE_OMITTED", "STOP_PRICE_RULES", "MARKET_STOP_GFD_REGULAR_ONLY", "STOP_MARKET_SELL_TO_CLOSE_ONLY",
    "STOP_BELOW_ASK", "NON_LIMIT_SINGLE_LEG_ONLY", "EQUITY_SESSION_ON_OPTION", "CURB_REQUIRES_INDEX_ENABLED",
    "LEVEL_INSUFFICIENT", "MULTI_LEG_CASH_OR_RETIREMENT", "QTY_POSITIVE_INT_STRING",
    "BAD_TYPE", "TIF_MARKET_LIMIT_GTC_ONLY", "TIF_STOP_VALUES", "IOC_NEVER", "TAX_LOTS_UNAVAILABLE",
    "OFF_INCREMENT", "PAIR_HALTED",
])
WARNING_CODES = sorted([
    "QUEUES_NEXT_OPEN", "SELL_LIMIT_NOT_MARKETABLE", "BUY_LIMIT_NOT_MARKETABLE", "UPPERCASE_TIF", "NOT_LOWERCASE",
    "STOP_WOULD_TRIGGER", "TP_WOULD_FILL", "TIF_NOT_STATED", "SEND_CHAIN_SYMBOL_AND_UNDERLYING_TYPE",
    "NON_STANDARD_MULTIPLIER", "STOP_TIF_DEFAULTS_TO_DAY", "SELL_COLLAR_5PCT",
])

# ---------------------------------------------------------------------------------------------
# JSON Schemas and self-test
# ---------------------------------------------------------------------------------------------
_S = {"type": "string"}
_SN = {"type": ["string", "null"]}
_FINDING = {"type": "object", "required": ["code", "field", "msg"], "additionalProperties": False,
            "properties": {"code": _S, "field": _S, "msg": _S}}
_ERR = {"type": "object", "required": ["ok", "errors"],
        "properties": {"ok": {"enum": [False]}, "errors": {"type": "array", "items": _FINDING}}}
_PROV = {"enum": ["user", "user_config", "user_intent_marketable"]}

SCHEMAS = {
    "lint": {
        "input": {
            "type": "object", "required": ["tool", "params"], "additionalProperties": False,
            "properties": {
                "tool": {"type": "string", "pattern": r"^(mcp__.+__)?(%s)$" % "|".join(sorted(FAMILY))},
                "params": {"type": "object"},
                "provenance": {"anyOf": [{"type": "object", "additionalProperties": _PROV},
                                         {"enum": ["user", "user_config"]}]},
                "context": {"type": "object", "additionalProperties": True, "properties": {
                    "agentic_allowed": {"type": "boolean"}, "market_price": _S, "bid": _S, "ask": _S,
                    "session": {"enum": ["regular", "outside_regular_trading_day", "closed_day"]},
                    "user_wants_immediate": {"type": "boolean"}, "option_level": _SN,
                    "account_type": {"enum": ["cash", "margin", "limited_margin"]},
                    "retirement": {"type": "boolean"}, "sellable_qty": _S, "position_qty": _S, "held_qty": _S,
                    "lots_available": {"type": "object", "additionalProperties": _S},
                    "chain_extended_hours_state": _SN, "underlying_type": {"enum": ["equity", "index"]},
                    "multiplier": _S, "min_order_quantity_increment": _SN, "pair_halted": {"type": ["boolean", "null"]},
                }},
            },
        },
        "output": {"anyOf": [{
            "type": "object", "additionalProperties": False,
            "required": ["ok", "valid", "errors", "warnings", "not_checked", "estimate", "fingerprint", "ticket_id",
                         "place_tool", "place_params"],
            "properties": {
                "ok": {"enum": [True]}, "valid": {"type": "boolean"}, "tool": _S,
                "family": {"enum": ["equity", "oco", "option", "crypto"]},
                "errors": {"type": "array", "items": _FINDING}, "warnings": {"type": "array", "items": _FINDING},
                "not_checked": {"type": "array", "items": {"type": "object", "required": ["check", "why"]}},
                "notes": {"type": "array", "items": _S},
                "estimate": {"type": "object", "required": ["usd", "basis", "worst_case_usd", "label"],
                             "properties": {"usd": _SN, "basis": _S, "worst_case_usd": _SN, "label": _S}},
                "fingerprint": {"type": ["string", "null"], "pattern": r"^sha256:[0-9a-f]{64}$"},
                "ticket_id": {"type": ["string", "null"]}, "place_tool": _S, "place_params": {"type": "object"},
            }}, _ERR]},
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
            elif isinstance(extra, dict):
                errs.extend(schema_errors(sub, extra, "%s.%s" % (path, key)))
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errs.extend(schema_errors(item, schema["items"], "%s[%d]" % (path, i)))
    return errs


def full_schema():
    ops = {}
    for name, pair in SCHEMAS.items():
        ops[name] = {k: dict(v, **{"$schema": "https://json-schema.org/draft/2020-12/schema"}) for k, v in pair.items()}
    return {"script": SCRIPT, "version": VERSION, "ops": ops, "error_codes": ERROR_CODES,
            "warning_codes": WARNING_CODES}


_ACCT = "demo-X4F1"
EXAMPLES = [
    # The build spec's overnight example: a marketable buy limit tagged to the 24 Hour Market.
    ("lint", {"tool": "review_equity_order",
              "params": {"account_number": _ACCT, "symbol": "PLTR", "side": "buy", "type": "limit", "quantity": "10",
                         "limit_price": "31.24", "market_hours": "all_day_hours", "time_in_force": "gfd"},
              "provenance": {"quantity": "user", "limit_price": "user_intent_marketable"},
              "context": {"agentic_allowed": True, "market_price": "31.20", "bid": "31.18", "ask": "31.24",
                          "session": "outside_regular_trading_day", "user_wants_immediate": True}},
     {"ok": True, "valid": True, "estimate": {"usd": "312.40", "basis": "qty" + X + "limit"},
      "place_tool": "place_equity_order", "place_params": {"account_number": MASK + "X4F1"}}),
    # A market order sent after the close with an invented quantity.
    ("lint", {"tool": "review_equity_order",
              "params": {"account_number": _ACCT, "symbol": "PLTR", "side": "buy", "type": "market", "quantity": "1"},
              "provenance": {}, "context": {"agentic_allowed": True, "session": "outside_regular_trading_day",
                                            "ask": "31.24"}},
     {"ok": True, "valid": False, "errors": [{"code": "NO_USER_SOURCE", "field": "quantity"}],
      "warnings": [{"code": "QUEUES_NEXT_OPEN"}]}),
    # OCO leg exactly 0.25% from the market passes; the stop 0.249% away fails.
    ("lint", {"tool": "review_advanced_order",
              "params": {"account_number": _ACCT, "symbol": "AMD", "side": "sell", "quantity": "10",
                         "take_profit_limit_price": "100.25", "stop_loss_stop_price": "99.751", "time_in_force": "gtc"},
              "provenance": {"quantity": "user", "take_profit_limit_price": "user", "stop_loss_stop_price": "user"},
              "context": {"agentic_allowed": True, "market_price": "100.00", "sellable_qty": "12"}},
     {"ok": True, "valid": False, "errors": [{"code": "MIN_DISTANCE_0_25PCT", "field": "stop_loss_stop_price"}]}),
    ("lint", {"tool": "preview_crypto_order",
              "params": {"rhs_account_number": "demo-5555", "symbol": "ETH", "side": "sell", "type": "stop_loss",
                         "quantity": "0.5", "stop_price": "2000"},
              "provenance": {"quantity": "user", "stop_price": "user"},
              "context": {"agentic_allowed": True, "min_order_quantity_increment": "0.000001", "pair_halted": False}},
     {"ok": True, "valid": True, "warnings": [{"code": "STOP_TIF_DEFAULTS_TO_DAY"}],
      "estimate": {"usd": "1000.00", "worst_case_usd": "950.00"}}),
    ("lint", {"tool": "exercise_option", "params": {}}, {"ok": False, "errors": [{"code": "UNKNOWN_TOOL"}]}),
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
        problems = [] if expected.get("ok") is False else schema_errors(inp, SCHEMAS[op]["input"])
        out = run(op, inp)
        problems += schema_errors(out, SCHEMAS[op]["output"])
        if not _subset(expected, out):
            problems.append("output mismatch: %s" % json.dumps(out, sort_keys=True))
        if _ACCT in json.dumps(out):
            problems.append("full account number printed")
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
        print(json.dumps(_err("MISSING_OP", "op", "usage: order_lint.py lint < input.json")))
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
