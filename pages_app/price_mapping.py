import pandas as pd
import streamlit as st

from db import get_conn
from formatters import fmt_auto


# --------------------------------------------------
# Load data
# --------------------------------------------------

@st.cache_data(ttl=1)
def load_price_mapping_data():

    conn = get_conn()

    mapping_sql = """
        SELECT
            contract,
            bid,
            ask,
            pc1,
            r1,
            pc2,
            r2,
            p1_b,
            p2_b,
            map_src_c,
            p1_a,
            p2_a
        FROM public.b1_price_mapping
    """

    md_sql = """
        SELECT
            contract,
            trade_status,
            delayed
        FROM public.md_snap
    """

    with conn.cursor() as cur:

        # b1_price_mapping
        cur.execute(mapping_sql)

        mapping_rows = cur.fetchall()
        mapping_cols = [d[0] for d in cur.description]

        df_mapping = pd.DataFrame(
            mapping_rows,
            columns=mapping_cols
        )

        # md_snap
        cur.execute(md_sql)

        md_rows = cur.fetchall()
        md_cols = [d[0] for d in cur.description]

        df_md = pd.DataFrame(
            md_rows,
            columns=md_cols
        )

    return df_mapping, df_md


# --------------------------------------------------
# Build table
# --------------------------------------------------

def build_price_mapping(
    df_mapping: pd.DataFrame,
    df_md: pd.DataFrame
) -> pd.DataFrame:

    out = df_mapping.copy()
    md = df_md.copy()

    # ----------------------------------------------
    # Clean string columns
    # ----------------------------------------------

    for col in ["contract", "pc1", "pc2", "map_src_c"]:
        out[col] = (
            out[col]
            .fillna("")
            .astype(str)
            .str.strip()
        )

    md["contract"] = (
        md["contract"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    # ----------------------------------------------
    # Numeric safety
    # ----------------------------------------------

    numeric_cols = [
        "bid",
        "ask",
        "r1",
        "r2",
        "p1_b",
        "p2_b",
        "p1_a",
        "p2_a",
    ]

    for col in numeric_cols:
        out[col] = pd.to_numeric(
            out[col],
            errors="coerce"
        )

    # ----------------------------------------------
    # Display contract
    # remove MP:
    # ----------------------------------------------

    out["contract_display"] = (
        out["contract"]
        .str.replace(r"^MP:", "", regex=True)
    )

    # ----------------------------------------------
    # Source
    #
    # Keep original source for lookup,
    # but remove IB: for display.
    # ----------------------------------------------

    out["src_original"] = out["map_src_c"]

    out["src"] = (
        out["map_src_c"]
        .str.replace(r"^IB:", "", regex=True)
    )

    # ----------------------------------------------
    # Is source pc1?
    # ----------------------------------------------

    is_pc1 = (
        out["map_src_c"] == out["pc1"]
    )

    # ----------------------------------------------
    # Ratio
    # ----------------------------------------------

    out["ratio"] = out["r2"]

    out.loc[is_pc1, "ratio"] = (
        out.loc[is_pc1, "r1"]
    )

    # ----------------------------------------------
    # Source bid
    # ----------------------------------------------

    out["src.bid"] = out["p2_b"]

    out.loc[is_pc1, "src.bid"] = (
        out.loc[is_pc1, "p1_b"]
    )

    # ----------------------------------------------
    # Source ask
    # ----------------------------------------------

    out["src.ask"] = out["p2_a"]

    out.loc[is_pc1, "src.ask"] = (
        out.loc[is_pc1, "p1_a"]
    )

    # ----------------------------------------------
    # Build md_snap lookup
    #
    # contract ->
    # trade_status + " " + delayed
    # ----------------------------------------------

    md["trade_status"] = (
        md["trade_status"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    md["delayed"] = (
        md["delayed"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    md["src_ts"] = (
        md["trade_status"] + " " + md["delayed"]
    ).str.strip()

    src_ts_map = dict(
        zip(
            md["contract"],
            md["src_ts"]
        )
    )

    # Lookup uses original source including IB:
    out["src ts"] = (
        out["src_original"]
        .map(src_ts_map)
        .fillna("")
    )

    # ----------------------------------------------
    # Final table
    # ----------------------------------------------

    final = pd.DataFrame({
        "contract": out["contract_display"],
        "bid": out["bid"],
        "ask": out["ask"],
        "src": out["src"],
        "ratio": out["ratio"],
        "src.bid": out["src.bid"],
        "src.ask": out["src.ask"],
        "src ts": out["src ts"],
    })

    return final


# --------------------------------------------------
# Styling
# --------------------------------------------------

def style_price_mapping(df: pd.DataFrame):
    def style_src(value):
        if str(value).strip().lower() == "no mapping":
            return "color: red; text-align: center;"
        return "color: green; text-align: center;"

    styler = (
        df.style
        .format({
            # Main bid / ask: always 2 decimals
            "bid": lambda x: "" if pd.isna(x) else f"{x:,.2f}",
            "ask": lambda x: "" if pd.isna(x) else f"{x:,.2f}",

            # Other numerical columns: keep existing automatic formatting
            "ratio": fmt_auto,
            "src.bid": fmt_auto,
            "src.ask": fmt_auto,
        })
        # Center ALL cells
        .set_properties(
            **{"text-align": "center"}
        )
        # Contract remains bold
        .set_properties(
            subset=["contract"],
            **{
                "font-weight": "bold",
                "text-align": "center",
            }
        )
        # Center column headers as well
        .set_table_styles([
            {
                "selector": "th",
                "props": [
                    ("text-align", "center"),
                ],
            }
        ])
        # src green/red
        .map(style_src, subset=["src"])
    )

    return styler


# --------------------------------------------------
# Render page
# --------------------------------------------------

def render_price_mapping_page():

    df_mapping, df_md = load_price_mapping_data()

    df_price_mapping = build_price_mapping(
        df_mapping,
        df_md
    )

    st.dataframe(
        style_price_mapping(df_price_mapping),
        use_container_width=True,
        hide_index=True,
    )