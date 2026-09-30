import altair as alt
import pandas as pd
import streamlit as st

from db import get_conn


# ============================================================
# SETTINGS
# ============================================================

BOND_PREFIX = "OFZ-PD "


# ============================================================
# BASIC HELPERS
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    return str(value).strip()


def to_float(value):
    value = pd.to_numeric(
        value,
        errors="coerce",
    )

    if pd.isna(value):
        return None

    return float(value)


# ============================================================
# LOAD STATIC OFZ UNIVERSE
#
# b1_static_data_moex is reference/static data.
#
# Select every instrument where:
#
#     instrument_sn starts with "OFZ-PD "
#
# ============================================================

@st.cache_resource
def load_ofz_universe():
    conn = get_conn()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                instrument_code,
                instrument_sn
            FROM public.b1_static_data_moex
            WHERE instrument_sn LIKE 'OFZ-PD %'
            ORDER BY instrument_sn
            """
        )

        rows = cur.fetchall()

    df = pd.DataFrame(
        rows,
        columns=[
            "instrument_code",
            "instrument_sn",
        ],
    )

    if df.empty:
        return df

    df["instrument_code"] = (
        df["instrument_code"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    df["instrument_sn"] = (
        df["instrument_sn"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    # --------------------------------------------------------
    # GRAPH LABEL
    #
    # OFZ-PD 26243 -> 243
    # OFZ-PD 26252 -> 252
    # --------------------------------------------------------

    df["label"] = (
        df["instrument_sn"]
        .str[-3:]
    )

    return df


# ============================================================
# LOAD LIVE DATA
#
# Current duration / yield comes from md_snap_moex.
#
# IMPORTANT:
# Matching is:
#
#     b1_static_data_moex.instrument_code
#           =
#     md_snap_moex.id
#
# ============================================================

def load_live_ofz_data(
    universe,
):
    if universe.empty:
        return pd.DataFrame(
            columns=[
                "instrument_code",
                "instrument_sn",
                "label",
                "duration",
                "yield",
            ]
        )

    instrument_codes = (
        universe[
            "instrument_code"
        ]
        .dropna()
        .astype(str)
        .str.strip()
        .tolist()
    )

    instrument_codes = [
        code
        for code
        in instrument_codes
        if code
    ]

    if not instrument_codes:
        return pd.DataFrame(
            columns=[
                "instrument_code",
                "instrument_sn",
                "label",
                "duration",
                "yield",
            ]
        )

    conn = get_conn()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id,
                duration,
                yield
            FROM public.md_snap_moex
            WHERE id = ANY(%s)
            """,
            (
                instrument_codes,
            ),
        )

        rows = cur.fetchall()

    live = pd.DataFrame(
        rows,
        columns=[
            "instrument_code",
            "duration",
            "yield",
        ],
    )

    if live.empty:
        return pd.DataFrame(
            columns=[
                "instrument_code",
                "instrument_sn",
                "label",
                "duration",
                "yield",
            ]
        )

    live["instrument_code"] = (
        live["instrument_code"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    live["duration"] = pd.to_numeric(
        live["duration"],
        errors="coerce",
    )

    live["yield"] = pd.to_numeric(
        live["yield"],
        errors="coerce",
    )

    # --------------------------------------------------------
    # MERGE STATIC + LIVE
    #
    # Inner join means:
    # if instrument_code is not present in md_snap_moex.id,
    # the bond is NOT included.
    # --------------------------------------------------------

    result = (
        universe.merge(
            live,
            on="instrument_code",
            how="inner",
        )
    )

    # --------------------------------------------------------
    # REMOVE ROWS WITHOUT CURVE DATA
    # --------------------------------------------------------

    # --------------------------------------------------------
    # REMOVE ROWS WITHOUT CURVE DATA
    # --------------------------------------------------------

    result = (
        result
        .dropna(
            subset=[
                "duration",
                "yield",
            ]
        )
        .copy()
    )

    # --------------------------------------------------------
    # FILTER BONDS
    #
    # 1. Exclude duration < 30 days
    # 2. Exclude instrument_sn ending with "V"
    # --------------------------------------------------------

    result = (
        result[
            (result["duration"] >= 366)
            &
            (
                ~result["instrument_sn"]
                .astype(str)
                .str.strip()
                .str.upper()
                .str.endswith("V")
            )
            ]
        .copy()
    )

    # --------------------------------------------------------
    # DURATION
    #
    # md_snap_moex duration is stored in days.
    # Convert to years for the curve AFTER filtering.
    # --------------------------------------------------------

    result["duration"] = (
            result["duration"]
            / 365.0
    )

    # --------------------------------------------------------
    # YIELD
    #
    # Handle either:
    #
    # 0.1595 -> 15.95%
    # or
    # 15.95  -> 15.95%
    #
    # We keep an actual percentage-point number for Altair:
    #
    # 15.95
    # --------------------------------------------------------

    def normalize_yield(value):
        value = to_float(
            value
        )

        if value is None:
            return None

        if abs(value) <= 1:
            return value * 100.0

        return value

    result["yield"] = (
        result["yield"]
        .apply(
            normalize_yield
        )
    )

    result = (
        result
        .dropna(
            subset=[
                "duration",
                "yield",
            ]
        )
        .sort_values(
            "duration"
        )
        .reset_index(
            drop=True
        )
    )

    return result


# ============================================================
# BUILD CURVE CHART
# ============================================================

def build_rflb_chart(
    df,
):
    if df.empty:
        return None

    chart_data = (
        df[
            [
                "instrument_sn",
                "label",
                "duration",
                "yield",
            ]
        ]
        .copy()
    )

    # ========================================================
    # POINTS
    # ========================================================

    points = (
        alt.Chart(
            chart_data
        )
        .mark_circle(
            size=140,
            opacity=0.75,
            strokeWidth=1,
        )
        .encode(
            x=alt.X(
                "duration:Q",
                title="Duration, years",
                scale=alt.Scale(
                    zero=False
                ),
                axis=alt.Axis(
                    format=".1f",
                ),
            ),

            y=alt.Y(
                "yield:Q",
                title="Yield",
                scale=alt.Scale(
                    zero=False
                ),
                axis=alt.Axis(
                    labelExpr=(
                        "format(datum.value, '.2f') + '%'"
                    ),
                ),
            ),

            tooltip=[
                alt.Tooltip(
                    "instrument_sn:N",
                    title="Bond",
                ),
                alt.Tooltip(
                    "duration:Q",
                    title="Duration",
                    format=".2f",
                ),
                alt.Tooltip(
                    "yield:Q",
                    title="Yield",
                    format=".2f",
                ),
            ],
        )
    )

    # ========================================================
    # LABELS
    #
    # Last 3 characters of instrument_sn.
    #
    # Slight offset prevents label from sitting directly
    # on top of the point.
    # ========================================================

    labels = (
        alt.Chart(
            chart_data
        )
        .mark_text(
            align="left",
            baseline="middle",
            dx=8,
            dy=-7,
            fontSize=12,
        )
        .encode(
            x=alt.X(
                "duration:Q"
            ),
            y=alt.Y(
                "yield:Q"
            ),
            text=alt.Text(
                "label:N"
            ),
        )
    )

    chart = (
        points
        + labels
    ).properties(
        width=850,
        height=560,
    )

    return chart


# ============================================================
# BUILD DISPLAY TABLE
# ============================================================

def build_rflb_table(
    df,
):
    if df.empty:
        return pd.DataFrame(
            columns=[
                "instrument_sn",
                "duration",
                "yield",
            ]
        )

    display = (
        df[
            [
                "instrument_sn",
                "duration",
                "yield",
            ]
        ]
        .copy()
    )

    display["duration"] = (
        display["duration"]
        .apply(
            lambda x: (
                ""
                if pd.isna(x)
                else f"{x:.2f}"
            )
        )
    )

    display["yield"] = (
        display["yield"]
        .apply(
            lambda x: (
                ""
                if pd.isna(x)
                else f"{x:.2f}%"
            )
        )
    )

    return display


# ============================================================
# TABLE STYLE
# ============================================================

def style_rflb_table(
    df,
):
    return (
        df.style
        .set_properties(
            **{
                "text-align": "center",
            }
        )
        .set_table_styles(
            [
                {
                    "selector": "th",
                    "props": [
                        (
                            "text-align",
                            "center",
                        ),
                    ],
                }
            ]
        )
        .set_properties(
            subset=[
                "instrument_sn"
            ],
            **{
                "font-weight": "bold",
            },
        )
    )


# ============================================================
# PAGE
# ============================================================

def render_rflb_curve_page():

    st.subheader(
        "RFLB Curve"
    )

    # ========================================================
    # STATIC OFZ UNIVERSE
    # ========================================================

    universe = (
        load_ofz_universe()
    )

    if universe.empty:

        st.warning(
            "No OFZ-PD instruments found in b1_static_data_moex."
        )

        return

    # ========================================================
    # LIVE DATA
    # ========================================================

    curve_data = (
        load_live_ofz_data(
            universe
        )
    )

    if curve_data.empty:

        st.warning(
            "No matching OFZ-PD instruments with duration/yield "
            "were found in md_snap_moex."
        )

        return

    # ========================================================
    # CHART
    # ========================================================

    chart = (
        build_rflb_chart(
            curve_data
        )
    )

    if chart is not None:

        st.altair_chart(
            chart,
            use_container_width=False,
        )

    # ========================================================
    # SHOW / HIDE DATA TABLE
    # ========================================================

    show_data = (
        st.toggle(
            "Show data",
            value=False,
            key="rflb_show_data",
        )
    )

    if show_data:

        display_df = (
            build_rflb_table(
                curve_data
            )
        )

        st.dataframe(
            style_rflb_table(
                display_df
            ),
            hide_index=True,
            use_container_width=False,
            width=500,
            height=min(
                38
                + len(
                    display_df
                )
                * 35,
                700,
            ),
        )