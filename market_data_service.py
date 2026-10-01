from __future__ import annotations

from datetime import timezone
from typing import Iterable

import pandas as pd
import streamlit as st

from db import get_conn

IB_MAX_AGE_SECONDS = 90
SETTING_NAME = "overwrite_ib_with_a1"


def _clean(v) -> str:
    return "" if v is None else str(v).strip()


def _num(v):
    x = pd.to_numeric(v, errors="coerce")
    return None if pd.isna(x) else float(x)


def _columns(table: str) -> set[str]:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name=%s""",
            (table,),
        )
        return {r[0] for r in cur.fetchall()}


def ensure_settings_table() -> None:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.b1_settings (
                setting text PRIMARY KEY,
                value_bool boolean NOT NULL DEFAULT false,
                updated_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            INSERT INTO public.b1_settings(setting, value_bool)
            VALUES (%s, false)
            ON CONFLICT (setting) DO NOTHING
            """,
            (SETTING_NAME,),
        )


def get_a1_override_enabled() -> bool:
    ensure_settings_table()
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT value_bool FROM public.b1_settings WHERE setting=%s", (SETTING_NAME,))
        row = cur.fetchone()
    return bool(row[0]) if row else False


def set_a1_override_enabled(enabled: bool) -> None:
    ensure_settings_table()
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.b1_settings(setting, value_bool, updated_at)
            VALUES (%s, %s, now())
            ON CONFLICT (setting)
            DO UPDATE SET value_bool=EXCLUDED.value_bool, updated_at=now()
            """,
            (SETTING_NAME, bool(enabled)),
        )


def _ib_problem(row: pd.Series, now_utc: pd.Timestamp) -> str:
    bid, ask = _num(row.get("bid")), _num(row.get("ask"))
    problems = []
    if bid is None or ask is None:
        problems.append("missing bid/ask")
    else:
        if bid <= 0 or ask <= 0:
            problems.append("non-positive bid/ask")
        if bid > ask:
            problems.append("bid > ask")

    if _clean(row.get("delayed")).lower() == "d":
        problems.append("delayed")

    if "snapshot_at" in row.index:
        raw_ts = row.get("snapshot_at")
        if raw_ts is None or pd.isna(raw_ts):
            problems.append("missing snapshot")
        else:
            try:
                ts = pd.Timestamp(raw_ts)
                ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
                age = (now_utc - ts).total_seconds()
                if age > IB_MAX_AGE_SECONDS:
                    problems.append(f"stale {age:.0f}s")
                elif age < -5:
                    problems.append("snapshot in future")
            except Exception:
                problems.append("invalid snapshot")
    return "; ".join(problems)


def _load_raw_ib(contracts: Iterable[str] | None = None) -> pd.DataFrame:
    cols = _columns("md_snap")
    wanted = [c for c in ["contract", "bid", "ask", "trade_status", "delayed", "snapshot_at"] if c in cols]
    if not {"contract", "bid", "ask"}.issubset(cols):
        return pd.DataFrame()
    sql = f"SELECT {', '.join(wanted)} FROM public.md_snap"
    params = ()
    contracts = [str(x).strip() for x in (contracts or []) if str(x).strip()]
    if contracts:
        sql += " WHERE contract = ANY(%s)"
        params = (contracts,)
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        names = [d.name for d in cur.description]
    return pd.DataFrame(rows, columns=names)


def _load_a1(contracts: Iterable[str] | None = None) -> pd.DataFrame:
    cols = _columns("a1_md_all")
    key = "instrument_code" if "instrument_code" in cols else ("contract" if "contract" in cols else None)
    mtm_col = "MTM" if "MTM" in cols else ("mtm" if "mtm" in cols else None)
    if not key or not mtm_col:
        return pd.DataFrame(columns=["contract", "a1_mtm"])
    sql = f'SELECT {key}, "{mtm_col}" FROM public.a1_md_all'
    params = ()
    contracts = [str(x).strip() for x in (contracts or []) if str(x).strip()]
    if contracts:
        # A1 historically may use IBKR: while B1_WEB uses IB:.
        aliases = set(contracts)
        for c in contracts:
            if c.startswith("IB:"):
                aliases.add("IBKR:" + c[3:])
            elif c.startswith("IBKR:"):
                aliases.add("IB:" + c[5:])
        sql += f" WHERE {key} = ANY(%s)"
        params = (list(aliases),)
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    out = pd.DataFrame(rows, columns=["a1_contract", "a1_mtm"])
    if out.empty:
        return pd.DataFrame(columns=["contract", "a1_mtm"])
    out["contract"] = out["a1_contract"].fillna("").astype(str).str.strip().str.replace(r"^IBKR:", "IB:", regex=True)
    out["a1_mtm"] = pd.to_numeric(out["a1_mtm"], errors="coerce")
    return out[["contract", "a1_mtm"]].dropna(subset=["a1_mtm"]).drop_duplicates("contract", keep="last")


def get_effective_ib_data(contracts: Iterable[str] | None = None) -> pd.DataFrame:
    """Return md_snap-shaped IB data with optional A1 fallback.

    Raw md_snap is never changed. If override is ON, only delayed/stale/invalid
    IB rows are replaced, and only when a positive A1 MTM exists. A1 has one
    price, so effective bid=ask=MTM. `delayed='a1'` marks the replacement.
    """
    raw = _load_raw_ib(contracts)
    if raw.empty:
        return raw
    for c in ["trade_status", "delayed", "snapshot_at"]:
        if c not in raw.columns:
            raw[c] = None
    raw["contract"] = raw["contract"].fillna("").astype(str).str.strip()
    now = pd.Timestamp.now(tz="UTC")
    raw["ib_problem"] = raw.apply(lambda r: _ib_problem(r, now), axis=1)
    raw["ib_bad"] = raw["ib_problem"].ne("")
    raw["effective_source"] = "IB"
    raw["replaced_by_a1"] = False

    if not get_a1_override_enabled():
        return raw

    a1 = _load_a1(raw.loc[raw["ib_bad"], "contract"].tolist())
    if a1.empty:
        return raw
    a1_map = dict(zip(a1["contract"], a1["a1_mtm"]))
    for idx in raw.index[raw["ib_bad"]]:
        mtm = _num(a1_map.get(raw.at[idx, "contract"]))
        if mtm is None or mtm <= 0:
            continue
        raw.at[idx, "bid"] = mtm
        raw.at[idx, "ask"] = mtm
        raw.at[idx, "delayed"] = "a1"
        raw.at[idx, "effective_source"] = "A1"
        raw.at[idx, "replaced_by_a1"] = True
        # A1 is accepted as the live fallback; keep IB trade_status for ts flag.
    return raw


def get_ib_override_status() -> dict:
    df = get_effective_ib_data()
    if df.empty:
        return {"enabled": get_a1_override_enabled(), "bad": 0, "replaced": 0, "invalid": 0, "details": df}
    bad = int(df["ib_bad"].sum())
    replaced = int(df["replaced_by_a1"].sum())
    return {
        "enabled": get_a1_override_enabled(),
        "bad": bad,
        "replaced": replaced,
        "invalid": bad - replaced,
        "details": df.loc[df["ib_bad"], [c for c in ["contract", "ib_problem", "effective_source", "replaced_by_a1"] if c in df.columns]].copy(),
    }


def load_market_table(table_name: str, contracts: Iterable[str] | None = None) -> pd.DataFrame:
    """Shared loader. md_snap automatically means effective IB data."""
    if table_name == "md_snap":
        return get_effective_ib_data(contracts)
    cols = _columns(table_name)
    if not cols:
        return pd.DataFrame()
    sql = f'SELECT * FROM public."{table_name}"'
    params = ()
    contracts = [str(x).strip() for x in (contracts or []) if str(x).strip()]
    if contracts and "contract" in cols:
        sql += " WHERE contract = ANY(%s)"
        params = (contracts,)
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        names = [d.name for d in cur.description]
    return pd.DataFrame(rows, columns=names)
