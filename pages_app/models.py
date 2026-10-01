from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import math
import re
from typing import Any, Iterable

import pandas as pd
import streamlit as st

from db import get_conn
from market_data_service import load_market_table


# ============================================================
# SETTINGS
# ============================================================

IB_MAX_AGE_SECONDS = 90
MONTH_CODES = "FGHJKMNQUVXZ"
MODEL_ORDER = ("mirror", "mirror+fx", "curve", "etf")

DISPLAY_COLUMNS = {
    "mirror": [
        "model", "contract_moex", "contract_intl", "ndays_mismatch",
        "rv", "coll %", "MTM", "FV", "ndays", "Edge", "| Edge % |",
        "| Edge Ann. |", "| ROC |", "| RAR |", "ts_tot", "md_tot",
    ],
    "mirror+fx": [
        "model", "contract_moex", "contract_intl", "fx_curve", "px_ratio",
        "ndays_mismatch", "c1", "c2", "p1", "p2", "m1", "m2",
        "w1", "w2", "rv", "coll %", "FX", "MTM_INTL", "MTM", "FV",
        "ndays", "Edge", "| Edge % |", "| Edge Ann. |", "| ROC |",
        "| RAR |", "ts_tot", "md_tot",
    ],
    "curve": [
        "model", "contract_moex", "c1", "c2", "w1", "w2", "rv",
        "coll %", "curve_name", "p1", "p2", "MTM", "FV", "ndays", "Edge",
        "| Edge % |", "| Edge Ann. |", "| ROC |", "| RAR |",
        "ts_tot", "md_tot",
    ],
    "etf": [
        "model", "contract_moex", "ETF", "rate_curve", "dividend_table",
        "px_ratio", "r", "d", "rv", "coll %", "ETF_PX", "MTM", "FV",
        "ndays", "Edge", "| Edge % |", "| Edge Ann. |", "| ROC |",
        "| RAR |", "ts_tot", "md_tot",
    ],
}


# ============================================================
# BASIC HELPERS
# ============================================================

def clean_text(v: Any) -> str:
    if v is None or pd.isna(v):
        return ""
    return str(v).strip()


def lower_text(v: Any) -> str:
    return clean_text(v).lower()


def to_float(v: Any) -> float | None:
    try:
        if v is None or pd.isna(v):
            return None
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def to_int(v: Any, default: int = 0) -> int:
    x = to_float(v)
    return default if x is None else int(x)


def to_date(v: Any) -> date | None:
    if v is None or pd.isna(v):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return pd.Timestamp(v).date()
    except Exception:
        return None


def safe_div(a: Any, b: Any) -> float | None:
    aa, bb = to_float(a), to_float(b)
    if aa is None or bb is None or bb == 0:
        return None
    return aa / bb


def safe_mul(*values: Any) -> float | None:
    out = 1.0
    for v in values:
        x = to_float(v)
        if x is None:
            return None
        out *= x
    return out


def first_value(row: dict | pd.Series | None, *names: str) -> Any:
    if row is None:
        return None
    for name in names:
        if name in row and row[name] is not None and not pd.isna(row[name]):
            return row[name]
    return None


def normalize_model(v: Any) -> str:
    s = lower_text(v).replace(" ", "")
    if s in {"mirror+fx", "mirrorfx", "mirror_fx"}:
        return "mirror+fx"
    return s


def make_lookup(df: pd.DataFrame, key: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if df.empty or key not in df.columns:
        return out
    for _, r in df.iterrows():
        k = clean_text(r.get(key))
        if k:
            out[k] = r.to_dict()
    return out


def sql_table_name(name: Any) -> str:
    s = clean_text(name)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", s):
        raise ValueError(f"Unsafe table name: {s!r}")
    return s


def read_query(query: str, params: tuple = ()) -> pd.DataFrame:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
        cols = [d.name for d in cur.description] if cur.description else []
    return pd.DataFrame(rows, columns=cols)


def read_table(name: str) -> pd.DataFrame:
    name = sql_table_name(name)
    return read_query(f'SELECT * FROM public."{name}"')


def read_table_optional(name: str) -> pd.DataFrame:
    try:
        return read_table(name)
    except Exception:
        return pd.DataFrame()


# ============================================================
# DATA LOADERS
# ============================================================

@st.cache_resource
def load_reference_data() -> dict[str, pd.DataFrame]:
    return {
        "fv_input": read_table("b1_fv_input"),
        "model_set": read_table("b1_model_set"),
        "und_set": read_table("b1_und_settings"),
        "risk_vol": read_table("b1_risk_vol"),
        "static_moex": read_table("b1_static_data_moex"),
        "static_intl": read_table("b1_static_data_intl"),
    }


def load_live_data() -> dict[str, pd.DataFrame]:
    return {
        "moex": load_market_table("md_snap_moex"),
        "ib": load_market_table("md_snap"),
        "bb": load_market_table("md_snap_bb"),
        "hl": load_market_table("md_snap_hl"),
        "mapping": load_market_table("b1_price_mapping"),
    }


# ============================================================
# MARKET DATA VALIDATION
# ============================================================

@dataclass
class MarketPrice:
    contract: str
    source: str
    bid: float | None = None
    ask: float | None = None
    mtm: float | None = None
    ts_ok: bool = True
    md_ok: bool = True
    ts_reason: str = ""
    md_reason: str = ""
    raw: dict | None = None

    @property
    def usable(self) -> bool:
        return self.ts_ok and self.md_ok and self.mtm is not None


def is_futures_spread(asset_class: Any) -> bool:
    s = lower_text(asset_class)
    return "spread" in s and ("future" in s or "fut" in s)


def bid_ask_check(bid: Any, ask: Any, allow_zero: bool = False) -> tuple[bool, str, float | None, float | None]:
    b, a = to_float(bid), to_float(ask)
    if b is None or a is None:
        return False, "missing bid/ask", b, a
    if b < 0 or a < 0:
        return False, "negative bid/ask", b, a
    if not allow_zero and (b == 0 or a == 0):
        return False, "zero bid/ask", b, a
    if b > a:
        return False, "bid > ask", b, a
    return True, "", b, a


def status_is_open(v: Any) -> bool:
    s = lower_text(v)
    if not s:
        return True
    return s in {"o", "open", "opened", "trading", "active", "normal"}


def ib_snapshot_is_fresh(v: Any) -> tuple[bool, str]:
    if v is None or pd.isna(v):
        return False, "missing snapshot_at"
    try:
        ts = pd.Timestamp(v)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        now = pd.Timestamp.now(tz="UTC")
        age = (now - ts).total_seconds()
        if age < -5:
            return False, "snapshot_at is in future"
        if age > IB_MAX_AGE_SECONDS:
            return False, f"stale snapshot ({age:.0f}s)"
        return True, ""
    except Exception:
        return False, "invalid snapshot_at"


class MarketDataBook:
    def __init__(self, live: dict[str, pd.DataFrame], static_lookup: dict[str, dict], und_lookup: dict[str, dict]):
        self.live = live
        self.static_lookup = static_lookup
        self.und_lookup = und_lookup
        self.lookups = {}
        for source, df in live.items():
            key = "contract" if "contract" in df.columns else ("id" if "id" in df.columns else "")
            self.lookups[source] = make_lookup(df, key) if key else {}

    def asset_class(self, contract: str) -> str:
        sd = self.static_lookup.get(contract, {})
        und = clean_text(first_value(sd, "und", "und_moex"))
        us = self.und_lookup.get(und, {})
        return clean_text(first_value(us, "assets_class", "asset_class"))

    def get(self, contract: str, _seen: set[str] | None = None) -> MarketPrice:
        contract = clean_text(contract)
        if not contract:
            return MarketPrice(contract, "", md_ok=False, md_reason="empty contract")
        seen = set() if _seen is None else set(_seen)
        if contract in seen:
            return MarketPrice(contract, "mapping", md_ok=False, md_reason="mapping loop")
        seen.add(contract)

        # Price mapping has priority for MP:* and any explicit mapped contract.
        if contract in self.lookups.get("mapping", {}):
            return self._from_mapping(contract, seen)
        if contract in self.lookups.get("moex", {}):
            return self._from_moex(contract)
        if contract in self.lookups.get("ib", {}):
            return self._from_ib(contract)
        if contract in self.lookups.get("bb", {}):
            return self._from_simple(contract, "bb")
        if contract in self.lookups.get("hl", {}):
            return self._from_simple(contract, "hl")
        return MarketPrice(contract, "", md_ok=False, md_reason="contract not found")

    def _from_ib(self, contract: str) -> MarketPrice:
        r = self.lookups["ib"][contract]
        ok, reason, b, a = bid_ask_check(r.get("bid"), r.get("ask"))
        md_ok, reasons = ok, ([] if ok else [reason])

        delayed = lower_text(r.get("delayed"))
        is_a1 = delayed == "a1" or lower_text(r.get("effective_source")) == "a1"
        if delayed == "d":
            md_ok = False
            reasons.append("delayed")

        # A1 is the accepted live fallback and does not inherit the stale IB timestamp.
        if not is_a1:
            fresh, fresh_reason = ib_snapshot_is_fresh(r.get("snapshot_at"))
            if not fresh:
                md_ok = False
                reasons.append(fresh_reason)

        status = first_value(r, "trade_status", "trading_status")
        ts_ok = status_is_open(status)
        ts_reason = "" if ts_ok else f"trade_status={clean_text(status)}"
        mtm = (b + a) / 2 if b is not None and a is not None else None
        return MarketPrice(contract, "ib", b, a, mtm, ts_ok, md_ok, ts_reason, "; ".join(reasons), r)

    def _from_moex(self, contract: str) -> MarketPrice:
        r = self.lookups["moex"][contract]
        allow_zero = is_futures_spread(self.asset_class(contract))
        ok, reason, b, a = bid_ask_check(r.get("bid"), r.get("ask"), allow_zero=allow_zero)
        status = first_value(r, "trade_status", "trading_status")
        ts_ok = status_is_open(status)
        ts_reason = "" if ts_ok else f"trade_status={clean_text(status)}"
        mtm = (b + a) / 2 if b is not None and a is not None else None
        return MarketPrice(contract, "moex", b, a, mtm, ts_ok, ok, ts_reason, "" if ok else reason, r)

    def _from_simple(self, contract: str, source: str) -> MarketPrice:
        r = self.lookups[source][contract]
        ok, reason, b, a = bid_ask_check(r.get("bid"), r.get("ask"))
        mtm = (b + a) / 2 if b is not None and a is not None else None
        return MarketPrice(contract, source, b, a, mtm, True, ok, "", "" if ok else reason, r)

    def _from_mapping(self, contract: str, seen: set[str]) -> MarketPrice:
        r = self.lookups["mapping"][contract]
        ok, reason, b, a = bid_ask_check(r.get("bid"), r.get("ask"))
        source_contract = clean_text(first_value(r, "map_src_c", "src_contract"))
        src = self.get(source_contract, seen) if source_contract else MarketPrice("", "", md_ok=False, md_reason="missing map_src_c")
        md_ok = ok and src.md_ok
        reasons = []
        if not ok:
            reasons.append(reason)
        if not src.md_ok:
            reasons.append(f"source {source_contract}: {src.md_reason}")
        mtm = (b + a) / 2 if b is not None and a is not None else None
        return MarketPrice(
            contract, "mapping", b, a, mtm,
            src.ts_ok, md_ok,
            src.ts_reason,
            "; ".join(reasons), r,
        )


# ============================================================
# INTERPOLATION
# ============================================================

@dataclass
class InterpolationResult:
    c1: str = ""
    c2: str = ""
    x1: Any = None
    x2: Any = None
    y1: float | None = None
    y2: float | None = None
    w1: float | None = None
    w2: float | None = None
    value: float | None = None
    exact: bool = False


def interpolate_points(target: Any, points: Iterable[tuple[Any, float, str]]) -> InterpolationResult:
    """Linear interpolation with safe exact-point handling.

    Requires the target to be covered by the curve. We deliberately do not
    silently extrapolate outside the first/last point.
    """
    pts = [(x, to_float(y), clean_text(c)) for x, y, c in points if x is not None and to_float(y) is not None]
    pts.sort(key=lambda z: z[0])
    if not pts:
        return InterpolationResult()

    for x, y, c in pts:
        if target == x:
            return InterpolationResult(c, c, x, x, y, y, 1.0, 0.0, y, True)

    lower = [p for p in pts if p[0] < target]
    upper = [p for p in pts if p[0] > target]
    if not lower or not upper:
        return InterpolationResult()

    x1, y1, c1 = lower[-1]
    x2, y2, c2 = upper[0]
    denom = x2 - x1
    if hasattr(denom, "days"):
        denom = denom.days
        num = (target - x1).days
    else:
        num = target - x1
    if denom == 0:
        return InterpolationResult(c1, c1, x1, x1, y1, y1, 1.0, 0.0, y1, True)
    w2 = num / denom
    w1 = 1.0 - w2
    value = y1 * w1 + y2 * w2
    return InterpolationResult(c1, c2, x1, x2, y1, y2, w1, w2, value, False)


# ============================================================
# MODEL CONTEXT
# ============================================================

class ModelContext:
    def __init__(self, ref: dict[str, pd.DataFrame], live: dict[str, pd.DataFrame]):
        self.ref = ref
        self.live = live
        self.model_lookup = make_lookup(ref["model_set"], "und_moex")
        self.und_lookup = make_lookup(ref["und_set"], "und_moex")
        self.risk_lookup = make_lookup(ref["risk_vol"], "und_moex")

        static_frames = [x for x in (ref["static_moex"], ref["static_intl"]) if not x.empty]
        self.static = pd.concat(static_frames, ignore_index=True, sort=False) if static_frames else pd.DataFrame()
        self.static_lookup = make_lookup(self.static, "contract")
        self.md = MarketDataBook(live, self.static_lookup, self.und_lookup)
        self.dynamic_tables: dict[str, pd.DataFrame] = {}

    def table(self, name: Any) -> pd.DataFrame:
        n = clean_text(name)
        if not n:
            return pd.DataFrame()
        if n not in self.dynamic_tables:
            self.dynamic_tables[n] = read_table_optional(n)
        return self.dynamic_tables[n]

    def base(self, contract: str) -> dict | None:
        sd = self.static_lookup.get(contract)
        if not sd:
            return None
        und = clean_text(first_value(sd, "und", "und_moex"))
        ms = self.model_lookup.get(und, {})
        us = self.und_lookup.get(und, {})
        rv_row = self.risk_lookup.get(und, {})
        expiration = to_date(sd.get("expiration"))
        ndays_adj = to_int(first_value(us, "moex_n_days_adj"), 0)
        expiration_adj = expiration + pd.Timedelta(days=ndays_adj) if expiration else None
        if isinstance(expiration_adj, pd.Timestamp):
            expiration_adj = expiration_adj.date()
        return {
            "contract_moex": contract,
            "sd": sd,
            "und_moex": und,
            "settings": ms,
            "und_settings": us,
            "expiration": expiration,
            "expiration_adj": expiration_adj,
            "model": normalize_model(ms.get("model")),
            "rv": to_float(first_value(rv_row, "risk_vol")),
            "asset_class": clean_text(first_value(us, "assets_class", "asset_class")),
        }


# ============================================================
# COMMON MODEL CALCULATIONS
# ============================================================

def shift_expiry_code(code: str, shift: int) -> str:
    code = clean_text(code)
    if len(code) < 2 or code[0] not in MONTH_CODES or not code[-1].isdigit():
        return ""
    i = MONTH_CODES.index(code[0])
    total = i + int(shift)
    new_month = MONTH_CODES[total % 12]
    year = (int(code[-1]) + total // 12) % 10
    return f"{new_month}{year}"


def build_intl_contract(base: dict) -> str:
    contract = base["contract_moex"]
    settings, us = base["settings"], base["und_settings"]
    und_intl = clean_text(first_value(us, "und_intl", settings.get("und_intl")))
    if lower_text(base["asset_class"]) == "crypto":
        exp = base["expiration"]
        return f"{und_intl}USDT-{exp.strftime('%d%b%y').upper()}" if exp else ""
    exp_code = contract[-2:]
    shifted = shift_expiry_code(exp_code, to_int(settings.get("month_shift"), 0))
    return f"IB:{und_intl}{shifted}" if und_intl and shifted else ""


def collateral_pct(ctx: ModelContext, base: dict, moex_px: MarketPrice) -> float | None:
    if not moex_px.raw or moex_px.mtm in (None, 0):
        return None
    buy = to_float(first_value(moex_px.raw, "buycollateral"))
    sell = to_float(first_value(moex_px.raw, "sellcollateral"))
    price_step_value = to_float(first_value(moex_px.raw, "price_step_value"))
    price_step = to_float(first_value(base["sd"], "price_step"))
    if None in (buy, sell, price_step_value, price_step) or price_step == 0:
        return None
    contract_value = moex_px.mtm / price_step * price_step_value
    return safe_div((buy + sell) * 0.5, contract_value)


def common_metrics(base: dict, mtm: float | None, fv: float | None, coll: float | None) -> dict:
    out = {"Edge": None, "| Edge % |": None, "| Edge Ann. |": None, "| ROC |": None, "| RAR |": None}
    if mtm is None or fv is None:
        return out
    edge = mtm - fv
    edge_pct = abs(edge / mtm) if mtm != 0 else None
    exp = base.get("expiration_adj")
    ndays = (exp - date.today()).days if exp else None
    edge_ann = abs(edge_pct * 365 / ndays) if edge_pct is not None and ndays and ndays > 0 else None
    roc = abs(edge_pct / (2 * coll) * 365 / ndays) if edge_pct is not None and coll not in (None, 0) and ndays and ndays > 0 else None
    rv = base.get("rv")
    rar = edge_ann / (2 * rv * math.sqrt(0.25)) if edge_ann is not None and rv not in (None, 0) else None
    out.update({"Edge": edge, "| Edge % |": edge_pct, "| Edge Ann. |": edge_ann, "| ROC |": roc, "| RAR |": rar})
    return out


def combined_flags(prices: Iterable[MarketPrice]) -> tuple[bool, bool, str]:
    pp = list(prices)
    ts = all(p.ts_ok for p in pp)
    md = all(p.md_ok for p in pp)
    reasons = []
    for p in pp:
        if not p.ts_ok:
            reasons.append(f"{p.contract}: {p.ts_reason or 'market closed'}")
        if not p.md_ok:
            reasons.append(f"{p.contract}: {p.md_reason or 'bad market data'}")
    return ts, md, "; ".join(reasons)


def finalize(base: dict, row: dict, required_prices: Iterable[MarketPrice], coll: float | None) -> dict:
    ts_tot, md_tot, reason = combined_flags(required_prices)
    row["ts_tot"] = ts_tot
    row["md_tot"] = md_tot
    row["status_reason"] = reason
    row["rv"] = base["rv"]
    row["coll %"] = coll
    exp = base.get("expiration_adj")
    row["ndays"] = (exp - date.today()).days if exp else None
    if not (ts_tot and md_tot):
        row["FV"] = None
        row.update(common_metrics(base, row.get("MTM"), None, coll))
    else:
        row.update(common_metrics(base, row.get("MTM"), row.get("FV"), coll))
    return row


# ============================================================
# CURVE HELPERS
# ============================================================

def curve_date_points(ctx: ModelContext, table_name: str, target: date) -> tuple[InterpolationResult, list[MarketPrice]]:
    df = ctx.table(table_name)
    points = []
    px_by_contract: dict[str, MarketPrice] = {}
    if df.empty:
        return InterpolationResult(), []
    for _, r in df.iterrows():
        contract = clean_text(r.get("contract"))
        if not contract:
            continue
        kind = lower_text(first_value(r, "type", "kind"))
        exp = to_date(r.get("expiration"))
        if kind in {"spot", "perpetual"}:
            exp = date.today()
        if exp is None:
            sd = ctx.static_lookup.get(contract, {})
            exp = date.today() if lower_text(sd.get("kind")) in {"spot", "perpetual"} else to_date(sd.get("expiration"))
        px = ctx.md.get(contract)
        px_by_contract[contract] = px
        if exp is not None and px.mtm is not None:
            points.append((exp, px.mtm, contract))
    ir = interpolate_points(target, points)
    req = []
    for c in dict.fromkeys([ir.c1, ir.c2]):
        if c:
            req.append(px_by_contract[c])
    return ir, req


def fx_curve_points(ctx: ModelContext, table_name: str, target: date) -> tuple[InterpolationResult, list[MarketPrice], dict[str, dict[str, float | None]]]:
    """Build the FX forward curve from raw prices and contract multipliers.

    For each curve contract we retain both the raw market price p and the
    multiplier m used by the Excel model.  The interpolated curve value is p/m.
    Returning p and m separately makes the Mirror+FX page fully auditable.
    """
    df = ctx.table(table_name)
    points = []
    px_by_contract: dict[str, MarketPrice] = {}
    details: dict[str, dict[str, float | None]] = {}
    if df.empty:
        return InterpolationResult(), [], details

    for _, r in df.iterrows():
        contract = clean_text(r.get("contract"))
        if not contract:
            continue

        kind = lower_text(first_value(r, "type", "kind"))
        exp = to_date(r.get("expiration"))
        sd = ctx.static_lookup.get(contract, {})
        if kind in {"spot", "perpetual"} or lower_text(sd.get("kind")) in {"spot", "perpetual"}:
            exp = date.today()
        if exp is None:
            exp = to_date(sd.get("expiration"))

        px = ctx.md.get(contract)
        px_by_contract[contract] = px
        p_raw = px.mtm

        # FX quotation multiplier. Same formula for every c1/c2 contract:
        #     m = lot * price_step / prcstep_in_curr
        # Values are taken from b1_static_data_moex for the curve contract.
        lot = to_float(sd.get("lot"))
        price_step = to_float(sd.get("price_step"))
        prcstep_in_curr = to_float(first_value(sd, "prcstep_in_curr", "curr_step_price"))
        m = safe_div(safe_mul(lot, price_step), prcstep_in_curr)

        details[contract] = {"p": p_raw, "m": m}
        norm_px = safe_div(p_raw, m)
        if exp is not None and norm_px is not None:
            points.append((exp, norm_px, contract))

    ir = interpolate_points(target, points)
    req = []
    for c in dict.fromkeys([ir.c1, ir.c2]):
        if c:
            req.append(px_by_contract[c])
    return ir, req, details


def rate_at_days(ctx: ModelContext, table_name: str, ndays: int) -> InterpolationResult:
    df = ctx.table(table_name)
    points = []
    if df.empty:
        return InterpolationResult()
    for _, r in df.iterrows():
        x = to_float(r.get("ndays"))
        y = to_float(r.get("rate"))
        if x is not None and y is not None:
            points.append((x, y, str(int(x))))
    return interpolate_points(float(ndays), points)


def dividend_sum(ctx: ModelContext, table_name: str, end_date: date) -> float:
    if not clean_text(table_name):
        return 0.0
    df = ctx.table(table_name)
    if df.empty:
        return 0.0
    total = 0.0
    for _, r in df.iterrows():
        rd = to_date(first_value(r, "r_dt", "rec_date", "record_date"))
        div = to_float(first_value(r, "div_net", "dividend", "div"))
        if rd and div is not None and date.today() < rd <= end_date:
            total += div
    return total


# ============================================================
# FOUR MODEL ENGINES
# ============================================================

def calc_mirror(ctx: ModelContext, base: dict) -> dict:
    contract_intl = build_intl_contract(base)
    intl_sd = ctx.static_lookup.get(contract_intl, {})
    expiration_intl = to_date(intl_sd.get("expiration"))
    ndays_mismatch = (expiration_intl - base["expiration_adj"]).days if expiration_intl and base["expiration_adj"] else None
    fx_mult = to_float(first_value(base["und_settings"], "fx_mult_intl"))
    if fx_mult is None:
        fx_mult = 1.0

    moex = ctx.md.get(base["contract_moex"])
    intl = ctx.md.get(contract_intl)
    coll = collateral_pct(ctx, base, moex)
    ts_tot, md_tot, _ = combined_flags([moex, intl])
    fv = intl.mtm * fx_mult if intl.mtm is not None and ts_tot and md_tot else None
    row = {
        "model": "mirror", "contract_moex": base["contract_moex"],
        "contract_intl": contract_intl, "ndays_mismatch": ndays_mismatch,
        "MTM": moex.mtm, "FV": fv,
    }
    return finalize(base, row, [moex, intl], coll)


def calc_mirror_fx(ctx: ModelContext, base: dict) -> dict:
    settings, us = base["settings"], base["und_settings"]
    contract_intl = build_intl_contract(base)
    intl_sd = ctx.static_lookup.get(contract_intl, {})
    expiration_intl = to_date(intl_sd.get("expiration"))
    ndays_mismatch = (expiration_intl - base["expiration_adj"]).days if expiration_intl and base["expiration_adj"] else None
    fx_curve = clean_text(settings.get("fx_curve"))
    px_ratio = to_float(first_value(us, "px_r_moex_intl", "price_ratio"))

    moex = ctx.md.get(base["contract_moex"])
    intl = ctx.md.get(contract_intl)
    ir, curve_prices, fx_details = fx_curve_points(ctx, fx_curve, base["expiration_adj"])
    required = [moex, intl] + curve_prices
    coll = collateral_pct(ctx, base, moex)
    ts_tot, md_tot, _ = combined_flags(required)
    fv = safe_div(safe_mul(intl.mtm, ir.value), px_ratio) if ts_tot and md_tot else None
    row = {
        "model": "mirror+fx", "contract_moex": base["contract_moex"],
        "contract_intl": contract_intl, "fx_curve": fx_curve,
        "px_ratio": px_ratio, "ndays_mismatch": ndays_mismatch,
        "c1": ir.c1, "c2": ir.c2,
        "p1": fx_details.get(ir.c1, {}).get("p"),
        "p2": fx_details.get(ir.c2, {}).get("p"),
        "m1": fx_details.get(ir.c1, {}).get("m"),
        "m2": fx_details.get(ir.c2, {}).get("m"),
        "w1": ir.w1, "w2": ir.w2,
        "FX": ir.value, "MTM_INTL": intl.mtm, "MTM": moex.mtm, "FV": fv,
    }
    return finalize(base, row, required, coll)


def calc_curve(ctx: ModelContext, base: dict) -> dict:
    curve_name = clean_text(base["settings"].get("curve_name"))
    moex = ctx.md.get(base["contract_moex"])
    ir, curve_prices = curve_date_points(ctx, curve_name, base["expiration_adj"])
    required = [moex] + curve_prices
    coll = collateral_pct(ctx, base, moex)
    ts_tot, md_tot, _ = combined_flags(required)
    fv = ir.value if ts_tot and md_tot else None
    row = {
        "model": "curve", "contract_moex": base["contract_moex"],
        "c1": ir.c1, "c2": ir.c2, "w1": ir.w1, "w2": ir.w2,
        "curve_name": curve_name, "p1": ir.y1, "p2": ir.y2,
        "MTM": moex.mtm, "FV": fv,
    }
    return finalize(base, row, required, coll)


def calc_etf(ctx: ModelContext, base: dict) -> dict:
    settings, us = base["settings"], base["und_settings"]
    etf = clean_text(settings.get("ETF"))
    rate_curve = clean_text(settings.get("rate_curve"))
    dividend_table = clean_text(settings.get("dividend_table"))
    px_ratio = to_float(first_value(us, "px_r_moex_intl", "price_ratio"))
    ndays = (base["expiration_adj"] - date.today()).days if base["expiration_adj"] else None
    rate_ir = rate_at_days(ctx, rate_curve, ndays) if ndays is not None else InterpolationResult()
    d = dividend_sum(ctx, dividend_table, base["expiration_adj"]) if base["expiration_adj"] else 0.0

    moex = ctx.md.get(base["contract_moex"])
    etf_px = ctx.md.get(etf)
    required = [moex, etf_px]
    coll = collateral_pct(ctx, base, moex)
    ts_tot, md_tot, _ = combined_flags(required)

    fv = None
    if ts_tot and md_tot and etf_px.mtm is not None and rate_ir.value is not None and ndays is not None and px_ratio is not None:
        fv = (etf_px.mtm * (1 + rate_ir.value * ndays / 365.0) - d) * px_ratio

    row = {
        "model": "etf", "contract_moex": base["contract_moex"], "ETF": etf,
        "rate_curve": rate_curve, "dividend_table": dividend_table,
        "px_ratio": px_ratio, "ndays": ndays, "r": rate_ir.value, "d": d,
        "ETF_PX": etf_px.mtm, "MTM": moex.mtm, "FV": fv,
    }
    return finalize(base, row, required, coll)


# ============================================================
# BUILD ALL MODELS
# ============================================================

def calculate_all_models() -> dict[str, pd.DataFrame]:
    ref = load_reference_data()
    live = load_live_data()
    ctx = ModelContext(ref, live)

    results: dict[str, list[dict]] = {m: [] for m in MODEL_ORDER}
    inp = ref["fv_input"]
    if inp.empty or "contract_moex" not in inp.columns:
        return {m: pd.DataFrame(columns=DISPLAY_COLUMNS[m]) for m in MODEL_ORDER}

    for contract in inp["contract_moex"].tolist():
        contract = clean_text(contract)
        if not contract:
            continue
        base = ctx.base(contract)
        if base is None:
            continue
        model = base["model"]
        try:
            if model == "mirror":
                results[model].append(calc_mirror(ctx, base))
            elif model == "mirror+fx":
                results[model].append(calc_mirror_fx(ctx, base))
            elif model == "curve":
                results[model].append(calc_curve(ctx, base))
            elif model == "etf":
                results[model].append(calc_etf(ctx, base))
        except Exception as exc:
            results.setdefault(model, []).append({
                "model": model,
                "contract_moex": contract,
                "ts_tot": False,
                "md_tot": False,
                "status_reason": f"calculation error: {exc}",
            })

    out = {}
    for model in MODEL_ORDER:
        df = pd.DataFrame(results[model])
        for col in DISPLAY_COLUMNS[model]:
            if col not in df.columns:
                df[col] = None
        out[model] = df
    return out


# ============================================================
# DISPLAY
# ============================================================

def fmt_num(v: Any, decimals: int = 2) -> str:
    x = to_float(v)
    return "" if x is None else f"{x:,.{decimals}f}"


def fmt_pct(v: Any, decimals: int = 1) -> str:
    x = to_float(v)
    return "" if x is None else f"{x:.{decimals}%}"


def display_frame(df: pd.DataFrame, model: str) -> pd.DataFrame:
    cols = DISPLAY_COLUMNS[model]
    d = df.reindex(columns=cols).copy()

    pct_cols = {"rv", "coll %", "| Edge % |", "| Edge Ann. |", "| ROC |", "| RAR |"}
    four_cols = {"w1", "w2", "r"}
    two_cols = {"FX", "MTM_INTL", "ETF_PX", "MTM", "FV", "Edge", "p1", "p2", "m1", "m2", "px_ratio"}

    for c in d.columns:
        if c in pct_cols:
            d[c] = d[c].map(lambda x: fmt_pct(x, 1))
        elif c in four_cols:
            d[c] = d[c].map(lambda x: fmt_num(x, 4))
        elif c in two_cols:
            d[c] = d[c].map(lambda x: fmt_num(x, 2))
    return d


def style_frame(df: pd.DataFrame):
    def color_bool(v):
        if v is True:
            return "color: green; font-weight: bold"
        if v is False:
            return "color: red; font-weight: bold"
        return ""

    styler = df.style
    for c in ["ts_tot", "md_tot"]:
        if c in df.columns:
            styler = styler.map(color_bool, subset=[c])
    for c in ["contract_moex", "contract_intl", "FV", "Edge"]:
        if c in df.columns:
            styler = styler.set_properties(subset=[c], **{"font-weight": "bold"})
    return styler


def render_models_page():
    st.title("Models")
    st.caption(f"IB market data stale after {IB_MAX_AGE_SECONDS} seconds. All timestamp checks are UTC-aware.")

    try:
        models = calculate_all_models()
    except Exception as exc:
        st.error(f"Models calculation failed: {exc}")
        return

    tabs = st.tabs(["Mirror", "Mirror + FX", "Curve", "ETF"])
    for tab, model in zip(tabs, MODEL_ORDER):
        with tab:
            raw = models[model]
            if raw.empty:
                st.info(f"No {model} contracts in b1_fv_input.")
                continue
            shown = display_frame(raw, model)
            st.dataframe(style_frame(shown), use_container_width=True, hide_index=True)

            bad = raw[(raw.get("ts_tot") == False) | (raw.get("md_tot") == False)]
            if not bad.empty and "status_reason" in bad.columns:
                with st.expander("Market-data diagnostics"):
                    diag_cols = [c for c in ["contract_moex", "ts_tot", "md_tot", "status_reason"] if c in bad.columns]
                    st.dataframe(bad[diag_cols], use_container_width=True, hide_index=True)
