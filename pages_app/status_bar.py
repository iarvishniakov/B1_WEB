from __future__ import annotations

from datetime import (
    date,
    datetime,
    time,
)

from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from db import get_conn
from market_data_service import (
    get_a1_override_enabled,
)


# ============================================================
# SETTINGS
# ============================================================

MAX_AGE_SECONDS = 180

MOSCOW_TZ = ZoneInfo(
    "Europe/Moscow"
)

UTC_TZ = ZoneInfo(
    "UTC"
)


# ============================================================
# DB HELPERS
# ============================================================

def _fetch_one(
    sql: str,
    params=(),
):
    conn = get_conn()

    with conn.cursor() as cur:
        cur.execute(
            sql,
            params,
        )

        return cur.fetchone()


def _table_columns(
    table_name: str,
) -> set[str]:

    row_sql = """
        select column_name
        from information_schema.columns
        where table_schema = 'public'
          and table_name = %s
    """

    conn = get_conn()

    with conn.cursor() as cur:

        cur.execute(
            row_sql,
            (table_name,),
        )

        return {
            row[0]
            for row in cur.fetchall()
        }


# ============================================================
# TIME HELPERS
# ============================================================

def _age_from_timestamp(
    value,
    assume_tz=UTC_TZ,
) -> float | None:
    """
    Return age in seconds:

        now_utc - timestamp

    Naive timestamps are interpreted in assume_tz.
    """

    if value is None:
        return None

    try:

        ts = pd.Timestamp(
            value
        )

        if ts.tzinfo is None:

            ts = ts.tz_localize(
                str(assume_tz)
            )

        ts = ts.tz_convert(
            "UTC"
        )

        now = pd.Timestamp.now(
            tz="UTC"
        )

        return (
            now - ts
        ).total_seconds()

    except Exception:

        return None


def _age_from_moscow_trade_time(
    value,
) -> float | None:
    """
    b1_nsa_md_check.time_trade is Moscow time.

    Supports:
        - timestamptz
        - timestamp without timezone
        - date/time
        - time-only value

    Critical condition:

        0 <= age <= 180

    so an incorrectly interpreted future timestamp
    does NOT accidentally show as healthy.
    """

    if value is None:
        return None

    now_moscow = datetime.now(
        MOSCOW_TZ
    )

    # --------------------------------------------------------
    # PostgreSQL TIME
    # --------------------------------------------------------

    if isinstance(
        value,
        time,
    ):

        dt = datetime.combine(
            now_moscow.date(),
            value,
        )

        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=MOSCOW_TZ
            )

        else:

            dt = dt.astimezone(
                MOSCOW_TZ
            )

        return (
            now_moscow - dt
        ).total_seconds()

    # --------------------------------------------------------
    # Timestamp / datetime
    # --------------------------------------------------------

    try:

        ts = pd.Timestamp(
            value
        )

        if ts.tzinfo is None:

            ts = ts.tz_localize(
                "Europe/Moscow"
            )

        else:

            ts = ts.tz_convert(
                "Europe/Moscow"
            )

        now = pd.Timestamp.now(
            tz="Europe/Moscow"
        )

        return (
            now - ts
        ).total_seconds()

    except Exception:

        return None


def _healthy(
    age: float | None,
) -> bool:

    if age is None:
        return False

    return (
        0
        <= age
        <= MAX_AGE_SECONDS
    )


# ============================================================
# MOEX
# ============================================================

def _moex_status() -> dict:
    """
    MOEX status.

    time_trade is stored as Moscow local TIME.

    PostgreSQL calculates:
        current Moscow time - time_trade

    Healthy:
        0 <= age <= 180 seconds
    """

    try:

        row = _fetch_one(
            """
            select
                extract(
                    epoch from (
                        (now() at time zone 'Europe/Moscow')::time
                        - time_trade
                    )
                ) as age_seconds
            from public.b1_msa_md_check
            where contract_code = %s
            limit 1
            """,
            ("IMOEXF",),
        )

        if not row:
            return {
                "ok": False,
                "age": None,
            }

        age = row[0]

        if age is None:
            return {
                "ok": False,
                "age": None,
            }

        age = float(age)

        return {
            "ok": (
                0 <= age <= MAX_AGE_SECONDS
            ),
            "age": age,
        }

    except Exception as e:

        # Useful during debugging:
        print(
            "MOEX STATUS ERROR:",
            repr(e),
        )

        return {
            "ok": False,
            "age": None,
        }
# ============================================================

# IB FIX
# ============================================================

def _ib_fix_status() -> dict:

    try:

        row = _fetch_one(
            """
            select max(snapshot_at)
            from public.md_snap
            """
        )

        value = (
            row[0]
            if row
            else None
        )

        age = _age_from_timestamp(
            value,
            UTC_TZ,
        )

        return {
            "ok": _healthy(age),
            "age": age,
        }

    except Exception:

        return {
            "ok": False,
            "age": None,
        }


# ============================================================
# IB TWS / A1
# ============================================================

def _ib_tws_status() -> dict:
    """
    ON/OFF is the existing:

        b1_settings
        overwrite_ib_with_a1

    OFF:
        gray X
        no health circle

    ON:
        green tick
        health determined from newest IBKR update
        in a1_md_all.
    """

    try:

        enabled = (
            get_a1_override_enabled()
        )

    except Exception:

        enabled = False

    if not enabled:

        return {
            "enabled": False,
            "ok": None,
            "age": None,
        }

    try:

        columns = _table_columns(
            "a1_md_all"
        )

        # Support either naming convention.

        if "source" in columns:

            source_col = "source"

        elif "src" in columns:

            source_col = "src"

        else:

            return {
                "enabled": True,
                "ok": False,
                "age": None,
            }

        if "updated_at" not in columns:

            return {
                "enabled": True,
                "ok": False,
                "age": None,
            }

        sql = f"""
            select max(updated_at)
            from public.a1_md_all
            where upper(trim("{source_col}")) = 'IBKR'
        """

        row = _fetch_one(
            sql
        )

        value = (
            row[0]
            if row
            else None
        )

        age = _age_from_timestamp(
            value,
            UTC_TZ,
        )

        return {
            "enabled": True,
            "ok": _healthy(age),
            "age": age,
        }

    except Exception:

        return {
            "enabled": True,
            "ok": False,
            "age": None,
        }


# ============================================================
# GENERIC UPDATED_AT STATUS
# ============================================================

def _updated_table_status(
    table_name: str,
) -> dict:

    try:

        row = _fetch_one(
            f"""
            select max(updated_at)
            from public."{table_name}"
            """
        )

        value = (
            row[0]
            if row
            else None
        )

        age = _age_from_timestamp(
            value,
            UTC_TZ,
        )

        return {
            "ok": _healthy(age),
            "age": age,
        }

    except Exception:

        return {
            "ok": False,
            "age": None,
        }


# ============================================================
# TOOLTIP
# ============================================================

def _age_text(
    age,
) -> str:

    if age is None:

        return (
            "No valid timestamp"
        )

    return (
        f"Age: {age:.0f} sec"
    )


# ============================================================
# STATUS BAR
# ============================================================

def render_market_status_bar():

    moex = _moex_status()
    ib_fix = _ib_fix_status()
    ib_tws = _ib_tws_status()

    bybit = _updated_table_status(
        "md_snap_bb"
    )

    hyper = _updated_table_status(
        "md_snap_hl"
    )

    # ========================================================
    # HELPERS
    # ========================================================

    def circle(
        ok: bool,
        title: str,
    ) -> str:

        css_class = (
            "md-good"
            if ok
            else "md-bad"
        )

        return (
            f'<span class="md-dot {css_class}" '
            f'title="{title}"></span>'
        )

    # ========================================================
    # IB TWS
    # ========================================================

    if ib_tws["enabled"]:

        tws_html = (
            'ib_tws '
            '<span class="md-tws-on" '
            'title="IB TWS enabled">✓</span> '
            + circle(
                bool(ib_tws["ok"]),
                _age_text(
                    ib_tws["age"]
                ),
            )
        )

    else:

        # OFF = gray X only.
        # No market-data health circle.
        tws_html = (
            'ib_tws '
            '<span class="md-tws-off" '
            'title="IB TWS disabled">✕</span>'
        )

    # ========================================================
    # CSS
    # ========================================================

    css = """
    <style>
    .md-status-bar {
        display: flex;
        align-items: center;
        justify-content: flex-start;
        gap: 10px;
        width: 100%;
        margin-top: -8px;
        margin-bottom: 5px;
        padding: 2px 4px;
        font-size: 13px;
        line-height: 18px;
        white-space: nowrap;
    }

    .md-status-item {
        display: inline-flex;
        align-items: center;
        gap: 5px;
    }

    .md-status-sep {
        opacity: 0.35;
    }

    .md-dot {
        display: inline-block;
        width: 9px;
        height: 9px;
        border-radius: 50%;
    }

    .md-good {
        background: #21c55d;
    }

    .md-bad {
        background: #ef4444;
    }

    .md-tws-on {
        color: #21c55d;
        font-weight: 800;
        font-size: 15px;
    }

    .md-tws-off {
        color: #8b8b8b;
        font-weight: 800;
        font-size: 15px;
    }
    </style>
    """

    # ========================================================
    # IMPORTANT:
    # Keep HTML itself on ONE LINE.
    #
    # Otherwise Markdown can interpret indented HTML as code.
    # ========================================================

    html = (
        '<div class="md-status-bar">'

        '<span class="md-status-item">'
        'moex '
        + circle(
            moex["ok"],
            _age_text(
                moex["age"]
            ),
        )
        + '</span>'

        '<span class="md-status-sep">|</span>'

        '<span class="md-status-item">'
        'ib_fix '
        + circle(
            ib_fix["ok"],
            _age_text(
                ib_fix["age"]
            ),
        )
        + '</span>'

        '<span class="md-status-sep">|</span>'

        '<span class="md-status-item">'
        + tws_html
        + '</span>'

        '<span class="md-status-sep">|</span>'

        '<span class="md-status-item">'
        'bybit '
        + circle(
            bybit["ok"],
            _age_text(
                bybit["age"]
            ),
        )
        + '</span>'

        '<span class="md-status-sep">|</span>'

        '<span class="md-status-item">'
        'hyper '
        + circle(
            hyper["ok"],
            _age_text(
                hyper["age"]
            ),
        )
        + '</span>'

        '</div>'
    )

    st.markdown(
        css + html,
        unsafe_allow_html=True,
    )