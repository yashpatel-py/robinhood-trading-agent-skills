#!/usr/bin/env python3
"""harvest_plan.py - lot-level tax-loss harvest candidates across the user's Robinhood accounts.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: Robinhood's automatic tax-loss harvesting exists only for managed (Strategies)
accounts; self-directed users get a manual tax-lot selector and nothing that looks across accounts. A
harvest scan has to combine open lots from every taxable account, live prices, and a wash-sale check for
each symbol against every account including the IRA, then show dollars. This script does the arithmetic
and the bookkeeping: which lots are at a loss, how much is short- versus long-term, how much of it a
sale today would lose to a wash (and whether permanently), when a clean sale is possible, and whether the
agent can simulate the sale (Agentic account only) or has to hand the user a manual ticket.

It never picks what to sell. Candidates are sorted by harvestable loss (disclosed), and there is no
"best" or "recommended" field: whether harvesting helps depends on the user's other gains, income and
plans, which the kit cannot see.

Ops:
  run   candidates per (account, symbol) with their loss lots, term split, wash status and dollars at
        stake; retirement accounts listed separately (their losses are not deductible); YTD context.

Wash results come from wash_sale.py sale checks. The preferred input is ONE combined sweep per symbol,
keyed "SYMBOL": every candidate lot of that symbol from every taxable account in lots_sold (each with its
account_last4), so the candidates share one pool of replacement shares instead of each claiming it. A
per-account check keyed "ACCT:SYMBOL" is also accepted. A sweep prices a candidate's lots only when its
per_lot covers those lot ids (or, without per_lot, when its sale_account_last4 is the candidate's account),
so one account's sweep never prices another account's lots. When separate sweeps of one symbol each claim
the same replacement buy, their figures are not added: each candidate keeps its "if sold alone" figure,
the combined figures are left blank, and the status is UNKNOWN until one combined sweep is run.
Status, worst first: conflict > unknown > possible > clear_in_scope (an account left out of the read scope)
> clear > none; it is never clear unless every account was read. Robinhood's own lot term wins over the
date arithmetic when they differ (TERM_DISAGREES): a wash-sale replacement, inherited or gifted lot can
carry an earlier holding period, and Robinhood's tax documents govern.

Usage:
    python3 harvest_plan.py run < input.json > output.json
    python3 harvest_plan.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network, no file writes; one JSON object out; exit 0 whenever
JSON was printed, exit 1 only on a crash. Account numbers appear only as their last 4 characters.
"""

import json
import os
import re
import sys
from decimal import ROUND_HALF_UP, Decimal

VERSION = "2.0.0"
SCRIPT = "harvest_plan"
RULES_AS_OF = "2026-09-22"
CENT = Decimal("0.01")
ZERO = Decimal(0)
SORTS = {
    "harvestable_usd": "harvestable loss, largest first",
    "symbol": "symbol, A to Z",
    "days_until_long_term": "soonest short-term loss lot to turn long-term first",
}
WASH_RANK = {"conflict": 4, "unknown": 3, "not_checked": 3, "possible": 2, "clear_in_scope": 1, "clear": 0}
READ_STATUSES = ("complete", "partial", "failed", "not_in_scope")
STATUS_LABEL = {"conflict": "CONFLICT", "unknown": "UNKNOWN", "possible": "POSSIBLE", "clear_in_scope": "CLEAR",
                "clear": "CLEAR", "none": "NO ACTION"}
REPLACEMENT_NOT_EVALUATED = ("basis and holding-period adjustments on replacement lots from earlier washes "
                             "(cross-account ones are not in Robinhood's lots)")

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True  # sibling imports must not leave __pycache__ in a skill folder
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import holding_period  # noqa: E402  (same folder; the holding-period rule lives there)

RULES_NOTE = [
    "Short-term losses offset short-term gains first and long-term losses offset long-term gains first; the nets "
    "then offset each other (IRS Pub 550; Schedule D).",
    "If total losses exceed total gains, up to $3,000 of net loss ($1,500 married filing separately) offsets other "
    "income each year and the rest carries forward (IRS Topic 409).",
    "A washed loss is deferred, not lost, when the replacement was bought in a taxable account: it is added to the "
    "replacement shares' basis. A replacement bought in an IRA or Roth loses it permanently (Rev. Rul. 2008-5).",
    "Losses inside an IRA or Roth are not deductible, so those accounts are listed separately and never counted.",
    "A lot bought as the replacement in an earlier wash carries the deferred loss in its basis and the sold shares' "
    "holding period (Pub 550). Robinhood does not track washes across accounts, so for a lot bought within 30 days "
    "of a loss sale of the same stock in another of your accounts, its cost and term (and the loss and "
    "short/long split shown here) can understate the loss or show short-term for a lot that is long-term.",
    "The trade date, not the settlement date, decides the tax year (IRS Pub 550): 2026 ends with the trade on "
    "Thursday 2026-12-31.",
]


class InputError(Exception):
    def __init__(self, code, field, msg):
        Exception.__init__(self, msg)
        self.code, self.field, self.msg = code, field, msg


def _err(code, field, msg):
    return {"ok": False, "errors": [{"code": code, "field": field, "msg": msg}]}


dec = holding_period.dec
money = holding_period.money
usd = holding_period.usd
qty = holding_period.qty


def _dec(value, field, **kw):
    try:
        return dec(value, field, **kw)
    except holding_period.InputError as exc:
        raise InputError(exc.code, exc.field, exc.msg)


def _day(value, field):
    try:
        return holding_period.parse_day(value, field)
    except holding_period.InputError as exc:
        raise InputError(exc.code, exc.field, exc.msg)


def mask(l4):
    return "••••" + l4


def _accounts(data):
    out = {}
    raw = data.get("accounts") or []
    if not isinstance(raw, list):
        raise InputError("BAD_VALUE", "accounts", "accounts is a list of {last4, type, agentic}")
    for i, a in enumerate(raw):
        where = "accounts[%d]" % i
        if not isinstance(a, dict):
            raise InputError("BAD_VALUE", where, "each account is an object")
        l4 = str(a.get("last4") or a.get("account_last4") or "").strip()[-4:]
        if not l4:
            raise InputError("MISSING_FIELD", where + ".last4", "last4 is required")
        t = str(a.get("type", "")).strip().lower()
        if t not in ("taxable", "retirement"):
            raise InputError("BAD_VALUE", where + ".type", "type must be taxable or retirement")
        s = str(a.get("read_status") or "complete").strip().lower()
        if s not in READ_STATUSES:
            raise InputError("BAD_VALUE", where + ".read_status",
                             "read_status must be one of %s" % ", ".join(READ_STATUSES))
        out[l4] = {"type": t, "agentic": a.get("agentic") is True, "label": str(a.get("label") or "").strip(),
                   "read_status": s}
    return out


def _wash_index(wash):
    """wash keys are "ACCT:SYMBOL" (one sweep per sale, preferred) or a bare "SYMBOL" -> {(acct|None, SYM): entry}."""
    if not isinstance(wash, dict):
        raise InputError("BAD_VALUE", "wash", 'wash maps "ACCT:SYMBOL" to that sale\'s wash_sale.py run output')
    idx = {}
    for key, entry in wash.items():
        k = str(key).strip()
        acct, sym = k.rsplit(":", 1) if ":" in k else ("", k)
        acct, sym = acct.strip()[-4:] or None, sym.strip().upper()
        if not sym:
            raise InputError("BAD_VALUE", "wash.%s" % k, 'wash keys are "ACCT:SYMBOL" or "SYMBOL"')
        if entry is None:
            continue  # no sweep for that key: the candidate stays not checked
        if not isinstance(entry, dict):
            raise InputError("BAD_VALUE", "wash.%s" % k, "each wash entry is a wash_sale.py run output object")
        idx[(acct, sym)] = entry
    return idx


def _wash_for(idx, acct, sym, loss_ids):
    """The sweep that checked selling THIS account's loss lots of sym -> (entry, key, None), or
    (None, None, why no entry could be used).

    Its wash dollars never carry over to another account's lots: an entry is used when its per_lot covers at
    least one of these lot ids (a combined sweep lists lots from several accounts), or, when it has no
    per_lot, only when its own sale_account_last4 names this account."""
    problem = None
    order = [("%s:%s" % (acct, sym), (acct, sym)), (sym, (None, sym))]
    order += sorted((("%s:%s" % k, k) for k in idx if k[1] == sym and k[0] not in (None, acct)))
    for key, ik in order:
        entry = idx.get(ik)
        if entry is None:
            continue
        sale_acct = str(entry.get("sale_account_last4") or "").strip()[-4:]
        mode = str(entry.get("mode") or "sale").strip().lower()
        esym = str(entry.get("symbol") or sym).strip().upper()
        per = entry.get("per_lot")
        covers = isinstance(per, dict) and any(k in per for k in loss_ids)
        if mode != "sale":
            why = "is a %s check, not a check of selling these lots" % mode
        elif esym != sym:
            why = "is for %s" % esym
        elif covers or sale_acct == acct or (ik == (acct, sym) and not sale_acct):
            return entry, key, None
        elif ik[0] not in (None, acct):
            continue  # another account's own check: silently not ours
        elif sale_acct and sale_acct != acct:
            why = "is for the sale in %s, not in %s" % (mask(sale_acct), mask(acct))
        elif isinstance(per, dict):
            why = "has no per_lot row for these lots"
        else:
            why = "names no sale account, so it cannot be matched to %s" % mask(acct)
        problem = problem or ('the wash result keyed "%s" %s; run one wash sweep for %s with every candidate lot '
                              'in lots_sold (key it "%s"), or one for selling these lots in %s (key it "%s:%s")'
                              % (key, why, sym, sym, mask(acct), acct, sym))
    return None, None, problem


def _read_skipped(w):
    """Accounts a wash_sale.py result says were left out by the user's read scope."""
    rows = w.get("accounts_read") if isinstance(w, dict) else None
    out = []
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and str(r.get("read_status") or "").strip().lower() == "not_in_scope":
            l4 = str(r.get("account_last4") or "").strip()[-4:]
            if l4:
                out.append(l4)
    return out


def _rh_term(term, broker_term):
    """The term Robinhood reports for a lot when it is short or long, else the date arithmetic's."""
    return broker_term if broker_term in ("short", "long") else term


def _loss_sales(data, accounts):
    raw = data.get("loss_sales")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise InputError("BAD_VALUE", "loss_sales", "loss_sales is a list of {account_last4, symbol, date}")
    out = []
    for i, s in enumerate(raw):
        where = "loss_sales[%d]" % i
        if not isinstance(s, dict):
            raise InputError("BAD_VALUE", where, "each loss sale is an object")
        acct = str(s.get("account_last4") or "").strip()[-4:]
        sym = str(s.get("symbol") or "").strip().upper()
        if not acct or not sym:
            raise InputError("MISSING_FIELD", where, "account_last4 and symbol are required")
        realized = _dec(s.get("realized_usd"), where + ".realized_usd", required=False)
        if realized is not None and realized >= 0:
            continue  # not a loss: nothing to defer into a replacement lot
        if accounts.get(acct, {}).get("type") == "retirement":
            continue  # a loss inside an IRA or Roth is not deductible, so it is never washed into another lot
        out.append({"account_last4": acct, "symbol": sym, "date": _day(s.get("date"), where + ".date")})
    return out


def op_run(data):
    as_of = _day(data.get("as_of"), "as_of")
    accounts = _accounts(data)
    prices = data.get("prices") or {}
    if not isinstance(prices, dict):
        raise InputError("BAD_VALUE", "prices", "prices maps SYMBOL to a decimal string")
    raw_min = data.get("min_loss_usd", "UNSET")
    min_loss = None if raw_min in (None, "", "UNSET") else _dec(raw_min, "min_loss_usd", nonneg=True)
    sort_by = str(data.get("sort_by") or "harvestable_usd")
    if sort_by not in SORTS:
        raise InputError("BAD_VALUE", "sort_by", "sort_by must be one of %s" % ", ".join(sorted(SORTS)))
    lots = data.get("lots")
    if not isinstance(lots, list):
        raise InputError("MISSING_FIELD", "lots", "give lots from get_equity_tax_lots for every position in scope")

    loss_sales = _loss_sales(data, accounts)
    wash_idx = _wash_index(data.get("wash") or {})

    groups, retirement, pending, unpriced, replacement_flags = {}, {}, [], [], []
    for i, lot in enumerate(lots):
        where = "lots[%d]" % i
        if not isinstance(lot, dict):
            raise InputError("BAD_VALUE", where, "each lot is an object")
        acct = str(lot.get("account_last4") or "").strip()[-4:]
        if not acct:
            raise InputError("MISSING_FIELD", where + ".account_last4", "account_last4 is required")
        info = accounts.get(acct)
        atype = str(lot.get("account_type") or (info or {}).get("type") or "").strip().lower()
        if atype not in ("taxable", "retirement"):
            raise InputError("UNKNOWN_ACCOUNT", where + ".account_last4",
                             "account ending %s needs a type (taxable or retirement) in accounts" % acct)
        sym = str(lot.get("symbol") or "").strip().upper()
        if not sym:
            raise InputError("MISSING_FIELD", where + ".symbol", "symbol is required")
        lot_id = str(lot.get("open_lot_id") or lot.get("lot_id") or "%s-%d" % (sym, i))
        acquired = _day(lot.get("acquired") or lot.get("open_date"), where + ".acquired")
        q = _dec(lot.get("quantity", lot.get("shares")), where + ".quantity", positive=True)
        cost = _dec(lot.get("cost_per_share"), where + ".cost_per_share", required=False, nonneg=True)
        px = prices.get(sym)
        px = _dec(px, "prices.%s" % sym, required=False, positive=True)
        if cost is None:
            pending.append({"account_last4": acct, "symbol": sym, "open_lot_id": lot_id,
                            "why": "cost basis not reported yet (never treated as zero)"})
            continue
        if px is None:
            if not any(u["symbol"] == sym for u in unpriced):
                unpriced.append({"symbol": sym, "why": "no price given; get_equity_quotes (20 symbols per call)"})
            continue
        u = (px - cost) * q
        lt_on, _ = holding_period.long_term_on(acquired)
        date_term = "long" if as_of >= lt_on else "short"
        broker_term = holding_period.normalize_term(lot.get("term"))
        # Robinhood's own term wins (a wash replacement, inherited or gifted lot can carry an earlier holding
        # period that the acquisition date does not show); the date arithmetic is kept next to it.
        term = _rh_term(date_term, broker_term)
        row = {"open_lot_id": lot_id, "acquired": acquired.isoformat(), "quantity": qty(q),
               "cost_per_share": money(cost), "unrealized_usd": money(u), "term": term}
        if term == "short" and date_term == "short":
            row["long_term_on"] = lt_on.isoformat()
            row["days_until_long_term"] = (lt_on - as_of).days
        if broker_term:
            row["broker_term"] = broker_term
            row["date_term"] = date_term
            row["term_agrees"] = broker_term == date_term
        if lot.get("is_selectable") is False:
            row["is_selectable"] = False
        if atype == "retirement":
            r = retirement.setdefault((acct, sym), ZERO)
            retirement[(acct, sym)] = r + u
            continue
        near = [s for s in loss_sales or [] if s["symbol"] == sym and s["account_last4"] != acct
                and abs((acquired - s["date"]).days) <= 30]
        if near:
            row["possible_replacement_lot"] = True
            replacement_flags.append({
                "account_last4": acct, "symbol": sym, "open_lot_id": lot_id, "acquired": acquired.isoformat(),
                "unrealized_usd": money(u), "term": term,
                "loss_sales": [{"account_last4": s["account_last4"], "date": s["date"].isoformat()} for s in near],
                "why": ("bought within 30 days of a loss sale of %s in %s: if that loss was washed into this lot, its "
                        "basis is higher and its holding period longer than Robinhood's lot shows (Robinhood does "
                        "not track washes across accounts), so the unrealized figure and term here may be wrong"
                        % (sym, ", ".join(sorted(set(mask(s["account_last4"]) for s in near)))))})
        g = groups.setdefault((acct, sym), {"price": px, "loss": [], "gain_count": 0, "all": []})
        g["all"].append((acquired, lot_id, u < 0))
        if u < 0:
            g["loss"].append((row, u, term, date_term))
        else:
            g["gain_count"] += 1

    candidates, below, skipped_by_wash, wash_not_evaluated = [], [], [], []
    for (acct, sym), g in groups.items():
        if not g["loss"]:
            continue
        harvest = -sum((u for _, u, _, _ in g["loss"]), ZERO)
        short = -sum((u for _, u, t, _ in g["loss"] if t == "short"), ZERO)
        long_ = -sum((u for _, u, t, _ in g["loss"] if t == "long"), ZERO)
        if min_loss is not None and harvest < min_loss:
            below.append({"account_last4": acct, "symbol": sym, "harvestable_loss_usd": money(harvest)})
            continue
        info = accounts.get(acct, {})
        loss_ids = [row["open_lot_id"] for row, _, _, _ in g["loss"]]
        cand = {"account_last4": acct, "account_label": info.get("label") or None, "symbol": sym,
                "price": money(g["price"]), "harvestable_loss_usd": money(harvest),
                "short_term_loss_usd": money(short), "long_term_loss_usd": money(long_),
                "loss_lots": [row for row, _, _, _ in sorted(g["loss"], key=lambda x: x[1])],
                "gain_lots_count": g["gain_count"], "term_split_disputed": False}
        notes = []

        # Robinhood's own term per lot wins; the date arithmetic's split is kept beside it (tax-rules-2026.md
        # section 4).
        disputed = [(row, t, dt) for row, _, t, dt in g["loss"] if dt != t]
        if disputed:
            cand["term_split_disputed"] = True
            cand["term_disputed_lots"] = [row["open_lot_id"] for row, _, _ in disputed]
            date_split = {"short": ZERO, "long": ZERO}
            for _, u, _, dt in g["loss"]:
                date_split[dt] -= u
            cand["date_term_split"] = {"short_term_loss_usd": money(date_split["short"]),
                                       "long_term_loss_usd": money(date_split["long"])}
            for row, t, dt in disputed:
                notes.append("TERM_DISAGREES: lot %s: Robinhood reports %s-term but the date arithmetic says %s-term "
                             "on %s; the %s-term figure is used, and Robinhood's tax documents govern (a wash-sale "
                             "replacement, inherited or gifted lot can carry an earlier holding period)"
                             % (row["open_lot_id"], t, dt, as_of.isoformat(), t))
        replaced = [row["open_lot_id"] for row, _, _, _ in g["loss"] if row.get("possible_replacement_lot")]
        if replaced:
            cand["replacement_lot_ids"] = replaced
            notes.append("lot(s) %s were bought within 30 days of a loss sale of %s in another account: if that loss "
                         "was washed into them, the loss here is understated and a short-term lot may be long-term"
                         % (", ".join(replaced), sym))

        w, wkey, why = _wash_for(wash_idx, acct, sym, loss_ids)
        if w is not None and (w.get("ok") is False or str(w.get("status") or "") not in WASH_RANK):
            why = ('the wash result for "%s:%s" has %s; rerun the sweep for selling these lots'
                   % (acct, sym, "ok: false" if w.get("ok") is False else "no sale-check status (%r)"
                      % (w.get("status"),)))
            w = None
        dis = perm = None
        uncovered = []
        if w is not None:
            field = "wash.%s" % wkey
            per = w.get("per_lot")
            if per is not None:
                if not isinstance(per, dict):
                    raise InputError("BAD_VALUE", field + ".per_lot", "per_lot maps lot ids to wash_sale.py rows")
                covered = [k for k in loss_ids if k in per]
                uncovered = [k for k in loss_ids if k not in per]
                if not covered:
                    why = ("the wash result for %s in %s has no per_lot row for lot(s) %s; run the sweep with these "
                           "lots in lots_sold (same open_lot_id values)" % (sym, mask(acct), ", ".join(uncovered)))
                    w = None
                else:
                    for k in covered:
                        if not isinstance(per[k], dict):
                            raise InputError("BAD_VALUE", "%s.per_lot.%s" % (field, k), "each per_lot row is an object")
                    dis = sum((_dec(per[k].get("disallowed_usd") or "0", "%s.per_lot.%s.disallowed_usd" % (field, k))
                               for k in covered), ZERO)
                    perm = sum((_dec(per[k].get("permanent_usd") or "0", "%s.per_lot.%s.permanent_usd" % (field, k))
                                for k in covered), ZERO)
            else:
                dis = _dec(w.get("disallowed_total_usd") or "0", field + ".disallowed_total_usd")
                perm = _dec(w.get("permanent_total_usd") or "0", field + ".permanent_total_usd")
        if w is not None:
            if dis < 0 or dis > harvest or perm < 0 or perm > dis:
                notes.append("the wash result's figures (%s disallowed, %s permanent) do not fit this candidate's %s "
                             "harvestable loss, so they were capped; the sweep probably used other lots or another "
                             "price: rerun it with these lots at today's price" % (usd(dis), usd(perm), usd(harvest)))
                dis = min(max(dis, ZERO), harvest)
                perm = min(max(perm, ZERO), dis)
            wstatus = str(w["status"])
            if uncovered:
                wstatus = "conflict" if wstatus == "conflict" else "unknown"
                cand["wash_lots_not_checked"] = uncovered
                notes.append("the wash sweep did not include lot(s) %s, so their wash status is unknown and the "
                             "disallowed figure covers only the other lots; rerun the sweep with every loss lot in "
                             "lots_sold" % ", ".join(uncovered))
            text = lambda f: w.get(f) if isinstance(w.get(f), str) else None  # noqa: E731
            cand.update({"wash_status": wstatus, "wash_status_line": text("status_line"),
                         "disallowed_if_sold_now_usd": money(dis), "permanent_if_sold_now_usd": money(perm),
                         "earliest_clean_sale_date": text("earliest_clean_sale_date"),
                         "do_not_buy_until": text("do_not_buy_until"),
                         "first_trading_day_after": text("first_trading_day_after"),
                         "possible_items": len(w.get("possible") or [])})
            ne = w.get("not_evaluated")
            if isinstance(ne, list):
                cand["not_evaluated"] = [str(x) for x in ne]
                wash_not_evaluated.extend(cand["not_evaluated"])
            skipped_by_wash.extend(_read_skipped(w))
            cand["_sweep"] = (id(w), w)
        else:
            dis = perm = None
            cand.update({"wash_status": "not_checked", "disallowed_if_sold_now_usd": None,
                         "permanent_if_sold_now_usd": None, "earliest_clean_sale_date": None,
                         "do_not_buy_until": None,
                         "wash_note": why or ('run the wash sweep for selling these lots in %s (key it "%s:%s") before '
                                              "treating this loss as usable" % (mask(acct), acct, sym))})
        cand["net_loss_after_wash_usd"] = money(harvest - dis) if dis is not None else None
        agentic = info.get("agentic") is True
        cand["reviewable"] = agentic
        cand["ticket_route"] = "review_equity_order" if agentic else "manual_ticket"
        oldest = min(g["all"])
        cand["fifo_sells_first"] = {"open_lot_id": oldest[1], "acquired": oldest[0].isoformat(),
                                    "at_a_loss": oldest[2]}
        if not oldest[2]:
            notes.append("Robinhood's default (FIFO) would sell the oldest lot, which is at a gain; realizing these "
                         "losses means naming the loss lots (tax_lots on review, or the app's tax-lot selector)")
        if any(row.get("is_selectable") is False for row, _, _, _ in g["loss"]):
            notes.append("a loss lot is still syncing and cannot be named in an order yet")
        if not agentic:
            notes.append("agents cannot simulate or place orders in %s; the ticket is manual" % mask(acct))
        cand["notes"] = notes
        candidates.append(cand)

    # Separate sweeps of one symbol that each matched the same replacement buy count it twice: each figure is
    # right only if that candidate alone is sold. Keep those as "if sold alone" and blank the combined figures.
    by_sym = {}
    for c in candidates:
        sweep = c.pop("_sweep", None)
        if sweep is not None:
            by_sym.setdefault(c["symbol"], []).append((c, sweep))
    for sym, items in by_sym.items():
        claims = {}
        for c, (sid, w) in items:
            for conf in w.get("conflicts") or []:
                if not isinstance(conf, dict):
                    continue
                bkey = (str(conf.get("account_last4") or ""), str(conf.get("date") or ""),
                        str(conf.get("order_id_short") or conf.get("lot_id") or ""), str(conf.get("shares") or ""))
                claims.setdefault(bkey, set()).add(sid)
        shared = sorted(k for k, sids in claims.items() if len(sids) > 1)
        if not shared:
            continue
        for c, _ in items:
            for f in ("disallowed_if_sold_now_usd", "permanent_if_sold_now_usd", "net_loss_after_wash_usd"):
                c[f.replace("_now_usd", "_alone_usd").replace("after_wash_usd", "after_wash_if_sold_alone_usd")] = c[f]
                c[f] = None
            if c["wash_status"] != "conflict":
                c["wash_status"] = "unknown"
            c["shared_replacement_buys"] = [{"account_last4": a, "date": d, "shares": n} for a, d, _, n in shared]
            c["notes"].append("separate wash sweeps for %s each matched the same replacement buy(s) (%s), so their "
                              "disallowed figures cannot be added: each \"if sold alone\" figure holds only if this "
                              "candidate alone is sold. Run one combined sweep for %s with every candidate lot in "
                              "lots_sold (key it \"%s\") for the figures if several are sold"
                              % (sym, "; ".join("%s bought %s on %s" % (mask(a), n, d) for a, d, _, n in shared),
                                 sym, sym))

    def key(c):
        if sort_by == "symbol":
            return (c["symbol"], c["account_last4"])
        if sort_by == "days_until_long_term":
            soon = [r.get("days_until_long_term") for r in c["loss_lots"] if r.get("days_until_long_term") is not None]
            return (min(soon) if soon else 10 ** 6, c["symbol"])
        return (-Decimal(c["harvestable_loss_usd"]), c["symbol"], c["account_last4"])

    candidates.sort(key=key)
    tot = lambda f: sum((Decimal(c[f]) for c in candidates if c.get(f) is not None), ZERO)  # noqa: E731
    totals = {"harvestable_loss_usd": money(tot("harvestable_loss_usd")),
              "short_term_loss_usd": money(tot("short_term_loss_usd")),
              "long_term_loss_usd": money(tot("long_term_loss_usd")),
              "disallowed_if_sold_now_usd": money(tot("disallowed_if_sold_now_usd")),
              "permanent_if_sold_now_usd": money(tot("permanent_if_sold_now_usd")),
              "term_disagreements": sum(len(c.get("term_disputed_lots") or []) for c in candidates)}
    totals["net_loss_after_wash_usd"] = money(Decimal(totals["harvestable_loss_usd"]) -
                                              Decimal(totals["disallowed_if_sold_now_usd"]))
    overlap = sorted(set(c["symbol"] for c in candidates if c.get("shared_replacement_buys")))
    if overlap:
        # Adding separate sweeps' figures would count a shared buy twice; leaving those candidates out would
        # overstate the usable loss. Neither is a number to show.
        totals.update({"disallowed_if_sold_now_usd": None, "permanent_if_sold_now_usd": None,
                       "net_loss_after_wash_usd": None, "wash_figures_overlap": overlap})

    ytd_in = data.get("ytd_realized") or {}
    if not isinstance(ytd_in, dict):
        raise InputError("BAD_VALUE", "ytd_realized", "ytd_realized maps account_last4 to realized $ this year")
    ytd = None
    if ytd_in:
        by_acct, taxable = {}, ZERO
        for acct, amount in sorted(ytd_in.items()):
            l4 = str(acct).strip()[-4:]
            a = _dec(amount, "ytd_realized.%s" % l4)
            t = accounts.get(l4, {}).get("type")
            by_acct[l4] = {"realized_usd": money(a), "type": t or "unknown"}
            if t == "taxable":
                taxable += a
        net = totals["net_loss_after_wash_usd"]
        ytd = {"realized_taxable_usd": money(taxable), "by_account": by_acct,
               "if_every_candidate_were_sold_usd": money(taxable - Decimal(net)) if net is not None else None,
               "arithmetic_note": ("arithmetic only: taxable realized so far plus the candidates' losses net of "
                                   "washes, at today's prices; the connector does not split realized gains into short "
                                   "and long term, so same-term netting is not shown")}

    incomplete = [a for a, v in accounts.items() if v["read_status"] in ("partial", "failed")]
    out_of_scope = []
    for a in [a for a, v in accounts.items() if v["read_status"] == "not_in_scope"] + skipped_by_wash:
        if a not in out_of_scope:
            out_of_scope.append(a)
    statuses = [c["wash_status"] for c in candidates]
    # conflict > unknown > possible > clear_in_scope > clear: never clear unless every account was read.
    if any(s == "conflict" for s in statuses):
        status = "conflict"
    elif incomplete or pending or unpriced or any(s in ("unknown", "not_checked") for s in statuses):
        status = "unknown"
    elif any(s == "possible" for s in statuses):
        status = "possible"
    elif candidates and (out_of_scope or any(s == "clear_in_scope" for s in statuses)):
        status = "clear_in_scope"
    elif candidates:
        status = "clear"
    else:
        status = "none"
    top = []
    for c in sorted(candidates, key=lambda c: -Decimal(c["harvestable_loss_usd"]))[:3]:
        bit = "%s %s: %s harvestable" % (c["symbol"], mask(c["account_last4"]), usd(Decimal(c["harvestable_loss_usd"])))
        if c.get("disallowed_if_sold_now_usd") and Decimal(c["disallowed_if_sold_now_usd"]) > 0:
            bit += " (%s disallowed if sold now%s)" % (
                usd(Decimal(c["disallowed_if_sold_now_usd"])),
                ", permanently" if Decimal(c["permanent_if_sold_now_usd"] or "0") > 0 else "")
        elif c.get("shared_replacement_buys"):
            bit += " (wash figures overlap with another sweep; run one combined sweep)"
        elif c["wash_status"] in ("unknown", "not_checked"):
            bit += " (wash status unknown)"
        top.append(bit)
    not_read = ", ".join(mask(a) for a in out_of_scope)
    line = "%s: Dollars at stake: %s" % (STATUS_LABEL[status], " · ".join(top) if top else "none found")
    if status == "clear_in_scope":
        line += (" (wash checked only in the accounts read%s)"
                 % ("; not read, by your choice: %s" % not_read if not_read else ""))
    elif status == "none":
        taxable_out = [a for a in out_of_scope if accounts.get(a, {}).get("type") != "retirement"]
        line = ("NO ACTION: Dollars at stake: none found (no taxable lot is at a loss%s%s%s)"
                % ("" if min_loss is None else " of at least %s" % usd(min_loss),
                   " in the accounts read" if taxable_out else "",
                   "; not read, by your choice: %s" % not_read if not_read else ""))
    elif not_read:
        line += " · not read, by your choice: %s" % not_read
    not_evaluated = [] if loss_sales is not None else [REPLACEMENT_NOT_EVALUATED]
    for item in wash_not_evaluated:
        if item not in not_evaluated:
            not_evaluated.append(item)
    return {
        "ok": True,
        "as_of": as_of.isoformat(),
        "status": status,
        "status_line": line,
        "dollars_at_stake": top or ["none found"],
        "candidates": candidates,
        "below_threshold": below,
        "basis_pending": pending,
        "unpriced": unpriced,
        "accounts_not_fully_read": incomplete,
        "accounts_not_in_scope": out_of_scope,
        "excluded_retirement": [{"account_last4": a, "symbol": s, "unrealized_usd": money(v),
                                 "why": "losses inside an IRA or Roth are not deductible"}
                                for (a, s), v in sorted(retirement.items())],
        "possible_replacement_lots": replacement_flags,
        "totals": totals,
        "ytd": ytd,
        "min_loss_usd": "UNSET" if min_loss is None else money(min_loss),
        "sort": SORTS[sort_by] + " (a display order, not a recommendation)",
        "term_split": ("short/long by Robinhood's own lot term, else the date arithmetic on as_of (Pub 550; Rev. Rul. "
                       "66-7); a lot where the two differ is listed in term_disputed_lots, with the date arithmetic's "
                       "split in date_term_split, and Robinhood's tax documents govern"),
        "not_evaluated": not_evaluated,
        "rules_note": RULES_NOTE,
        "rules_as_of": RULES_AS_OF,
    }


OPS = {"run": op_run}


def run(op, data):
    if op not in OPS:
        return _err("UNKNOWN_OP", "op", "unknown op %r; expected run" % op)
    if not isinstance(data, dict):
        return _err("BAD_INPUT", "", "input must be a JSON object")
    try:
        return OPS[op](data)
    except (InputError, holding_period.InputError) as exc:
        return _err(exc.code, exc.field, exc.msg)
    except (TypeError, AttributeError, ValueError, KeyError, IndexError, ArithmeticError) as exc:
        # A field of the wrong JSON type (a list where an object belongs, a number where a string belongs).
        return _err("BAD_INPUT", "", "input has an unexpected shape (%s: %s); compare it with --schema"
                    % (type(exc).__name__, exc))


# ---------------------------------------------------------------------------------------------
# JSON Schemas and self-test
# ---------------------------------------------------------------------------------------------
_S = {"type": "string"}
_SN = {"type": ["string", "null"]}
_M = {"type": "string", "pattern": r"^-?\d+\.\d{2}$"}
_MN = {"type": ["string", "null"], "pattern": r"^-?\d+\.\d{2}$"}
_DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}"}
_ERR = {"type": "object", "required": ["ok", "errors"],
        "properties": {"ok": {"enum": [False]},
                       "errors": {"type": "array", "items": {"type": "object", "required": ["code", "field", "msg"],
                                                             "properties": {"code": _S, "field": _S, "msg": _S}}}}}
_CAND = {"type": "object", "required": ["account_last4", "symbol", "harvestable_loss_usd", "short_term_loss_usd",
                                        "long_term_loss_usd", "loss_lots", "wash_status",
                                        "disallowed_if_sold_now_usd", "permanent_if_sold_now_usd",
                                        "earliest_clean_sale_date", "reviewable", "ticket_route",
                                        "term_split_disputed"],
         "properties": {"harvestable_loss_usd": _M, "short_term_loss_usd": _M, "long_term_loss_usd": _M,
                        "wash_status": {"enum": sorted(WASH_RANK)}, "disallowed_if_sold_now_usd": _MN,
                        "permanent_if_sold_now_usd": _MN, "earliest_clean_sale_date": _SN,
                        "reviewable": {"type": "boolean"}, "term_split_disputed": {"type": "boolean"},
                        "ticket_route": {"enum": ["review_equity_order", "manual_ticket"]}}}
SCHEMAS = {
    "run": {
        "input": {"type": "object", "required": ["as_of", "lots"], "additionalProperties": False, "properties": {
            "as_of": _DATE,
            "accounts": {"type": "array", "items": {"type": "object", "required": ["last4", "type"], "properties": {
                "last4": _S, "type": {"enum": ["taxable", "retirement"]}, "agentic": {"type": "boolean"},
                "label": _S, "read_status": {"enum": ["complete", "partial", "failed", "not_in_scope"]}}}},
            "lots": {"type": "array", "items": {"type": "object", "required": ["account_last4", "symbol"],
                                                "properties": {"account_last4": _S, "account_type": _S,
                                                               "symbol": _S, "open_lot_id": _S, "acquired": _S,
                                                               "open_date": _S, "quantity": _S,
                                                               "cost_per_share": _SN, "term": _S,
                                                               "is_selectable": {"type": "boolean"}}}},
            "prices": {"type": "object"},
            "wash": {"type": "object", "description": 'keys "SYMBOL" (one combined sweep of every candidate lot of '
                                                      'that symbol, preferred) or "ACCT:SYMBOL"; an entry prices '
                                                      "only the lots its per_lot covers"},
            "loss_sales": {"type": "array", "items": {"type": "object", "required": ["account_last4", "symbol",
                                                                                    "date"],
                                                      "properties": {"account_last4": _S, "symbol": _S, "date": _S,
                                                                     "realized_usd": _S}}},
            "ytd_realized": {"type": "object"}, "min_loss_usd": _S, "sort_by": {"enum": sorted(SORTS)}}},
        "output": {"anyOf": [{"type": "object", "required": [
            "ok", "status", "status_line", "candidates", "excluded_retirement", "totals", "rules_note",
            "dollars_at_stake", "accounts_not_fully_read", "accounts_not_in_scope", "not_evaluated"], "properties": {
            "ok": {"enum": [True]},
            "status": {"enum": ["conflict", "unknown", "possible", "clear_in_scope", "clear", "none"]},
            "status_line": _S, "candidates": {"type": "array", "items": _CAND},
            "accounts_not_fully_read": {"type": "array", "items": _S},
            "accounts_not_in_scope": {"type": "array", "items": _S},
            "possible_replacement_lots": {"type": "array"}, "not_evaluated": {"type": "array", "items": _S},
            "excluded_retirement": {"type": "array"}, "totals": {"type": "object"}}}, _ERR]},
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
    return {"script": SCRIPT, "version": VERSION, "ops": ops}


_ACCTS = [{"last4": "X4F1", "type": "taxable", "agentic": True, "label": "Agentic"},
          {"last4": "M7Q5", "type": "taxable", "agentic": False, "label": "Individual"},
          {"last4": "P0Z9", "type": "retirement", "agentic": False, "label": "Roth IRA"}]
_TSLA_WASH = {"ok": True, "status": "conflict", "status_line": "CONFLICT: ...", "disallowed_total_usd": "390.00",
              "permanent_total_usd": "390.00", "earliest_clean_sale_date": "2026-12-07",
              "do_not_buy_until": "2026-12-17", "first_trading_day_after": "2026-12-17",
              "per_lot": {"L7": {"disallowed_usd": "390.00", "permanent_usd": "390.00"}}, "possible": []}

EXAMPLES = [
    ("run", {"as_of": "2026-11-16", "accounts": _ACCTS,
             "lots": [{"account_last4": "M7Q5", "symbol": "TSLA", "open_lot_id": "L7", "acquired": "2026-06-02",
                       "quantity": "20", "cost_per_share": "340.00"},
                      {"account_last4": "M7Q5", "symbol": "TSLA", "open_lot_id": "L1", "acquired": "2025-03-10",
                       "quantity": "20", "cost_per_share": "280.00"},
                      {"account_last4": "P0Z9", "symbol": "TSLA", "open_lot_id": "R1", "acquired": "2026-11-06",
                       "quantity": "5", "cost_per_share": "270.00"}],
             "prices": {"TSLA": "262.00"}, "wash": {"M7Q5:TSLA": _TSLA_WASH},
             "ytd_realized": {"X4F1": "462.23", "M7Q5": "7950.10", "P0Z9": "1204.00"}},
     {"ok": True, "status": "conflict", "candidates": [
         {"account_last4": "M7Q5", "symbol": "TSLA", "harvestable_loss_usd": "1920.00",
          "short_term_loss_usd": "1560.00", "long_term_loss_usd": "360.00", "wash_status": "conflict",
          "disallowed_if_sold_now_usd": "390.00", "permanent_if_sold_now_usd": "390.00",
          "earliest_clean_sale_date": "2026-12-07", "reviewable": False, "ticket_route": "manual_ticket"}],
      "excluded_retirement": [{"account_last4": "P0Z9", "symbol": "TSLA", "unrealized_usd": "-40.00"}],
      "ytd": {"realized_taxable_usd": "8412.33"}}),
    ("run", {"as_of": "2026-11-16", "accounts": _ACCTS, "prices": {"PLTR": "31.20"},
             "lots": [{"account_last4": "X4F1", "symbol": "PLTR", "open_lot_id": "P1", "acquired": "2026-08-01",
                       "quantity": "30", "cost_per_share": "28.00"}]},
     {"ok": True, "status": "none", "candidates": []}),
    # The Roth was left out by the read scope: the sweep is clear_in_scope, so the scan is never plain "clear".
    ("run", {"as_of": "2026-11-16",
             "accounts": _ACCTS[:2] + [dict(_ACCTS[2], read_status="not_in_scope")],
             "lots": [{"account_last4": "M7Q5", "symbol": "TSLA", "open_lot_id": "L7", "acquired": "2026-06-02",
                       "quantity": "20", "cost_per_share": "340.00"}],
             "prices": {"TSLA": "262.00"},
             "wash": {"M7Q5:TSLA": {"ok": True, "mode": "sale", "symbol": "TSLA", "sale_account_last4": "M7Q5",
                                    "status": "clear_in_scope", "disallowed_total_usd": "0.00",
                                    "permanent_total_usd": "0.00",
                                    "per_lot": {"L7": {"disallowed_usd": "0.00", "permanent_usd": "0.00"}}}}},
     {"ok": True, "status": "clear_in_scope", "accounts_not_in_scope": ["P0Z9"],
      "status_line": "CLEAR: Dollars at stake: TSLA %s: $1,560.00 harvestable (wash checked only in the accounts "
                     "read; not read, by your choice: %s)" % (mask("M7Q5"), mask("P0Z9"))}),
    # A sweep keyed by symbol only belongs to the account that sold: another account's lots stay not checked.
    ("run", {"as_of": "2026-11-16", "accounts": _ACCTS,
             "lots": [{"account_last4": "M7Q5", "symbol": "TSLA", "open_lot_id": "L7", "acquired": "2026-06-02",
                       "quantity": "20", "cost_per_share": "340.00"},
                      {"account_last4": "X4F1", "symbol": "TSLA", "open_lot_id": "A1", "acquired": "2026-10-01",
                       "quantity": "1", "cost_per_share": "338.00"}],
             "prices": {"TSLA": "262.00"}, "wash": {"TSLA": dict(_TSLA_WASH, sale_account_last4="M7Q5")}},
     {"ok": True, "status": "conflict", "candidates": [
         {"account_last4": "M7Q5", "wash_status": "conflict", "disallowed_if_sold_now_usd": "390.00"},
         {"account_last4": "X4F1", "wash_status": "not_checked", "disallowed_if_sold_now_usd": None,
          "net_loss_after_wash_usd": None}],
      "totals": {"permanent_if_sold_now_usd": "390.00"}}),
]


def _subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) <= len(actual) and all(
            _subset(e, a) for e, a in zip(expected, actual))
    return expected == actual and type(expected) is type(actual)


def _has_key(obj, keys):
    if isinstance(obj, dict):
        return any(k in obj for k in keys) or any(_has_key(v, keys) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_key(v, keys) for v in obj)
    return False


def selftest():
    failures = []
    for i, (op, inp, expected) in enumerate(EXAMPLES):
        problems = schema_errors(inp, SCHEMAS[op]["input"])
        out = run(op, inp)
        problems += schema_errors(out, SCHEMAS[op]["output"])
        if not _subset(expected, out):
            problems.append("output mismatch: %s" % json.dumps(out, sort_keys=True))
        if _has_key(out, ("best", "recommended", "recommendation", "suggested")):
            problems.append("output has a ranking key; this script never picks what to sell")
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
        print(json.dumps(_err("MISSING_OP", "op", "usage: harvest_plan.py run < input.json")))
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
