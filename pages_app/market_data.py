import pandas as pd
import streamlit as st

from db import get_conn


# ============================================================
# SETTINGS
# ============================================================

MARKET_DATA_TABLES = [
    "md_snap",
    "md_snap_bb",
    "md_snap_moex",
    "md_snap_hl",
    "b1_price_mapping",
]


# ============================================================
# GET TABLE COLUMNS
# ============================================================

@st.cache_resource
def get_market_data_table_columns():
    """
    Read the column structure once when the app starts.

    This allows tables that don't have trade_status or delayed
    to still be included in the combined Market Data page.
    """

    conn = get_conn()

    sql = """
        SELECT
            table_name,
            column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = ANY(%s)
    """

    with conn.cursor() as cur:
        cur.execute(
            sql,
            (MARKET_DATA_TABLES,),
        )
        rows = cur.fetchall()

    result = {}

    for table_name, column_name in rows:

        if table_name not in result:
            result[table_name] = set()

        result[table_name].add(column_name)

    return result


# ============================================================
# LOAD LIVE MARKET DATA
# ============================================================

def load_market_data():
    """
    Load current data from:

        md_snap
        md_snap_bb
        md_snap_moex
        md_snap_hl
        b1_price_mapping

    This function is deliberately NOT cached because market
    prices should update on every global Streamlit refresh.
    """

    conn = get_conn()

    table_columns = get_market_data_table_columns()

    frames = []

    with conn.cursor() as cur:

        for table_name in MARKET_DATA_TABLES:

            columns = table_columns.get(
                table_name,
                set(),
            )

            # -----------------------------------------------
            # Minimum required fields
            # -----------------------------------------------

            if "contract" not in columns:
                continue

            if "bid" not in columns:
                continue

            if "ask" not in columns:
                continue

            # -----------------------------------------------
            # Some tables may not contain these columns
            # -----------------------------------------------

            if "trade_status" in columns:
                trade_status_sql = "trade_status"
            else:
                trade_status_sql = "NULL::text AS trade_status"

            if "delayed" in columns:
                delayed_sql = "delayed"
            else:
                delayed_sql = "NULL::text AS delayed"

            # -----------------------------------------------
            # Query
            # -----------------------------------------------

            sql = f"""
                SELECT
                    contract,
                    bid,
                    ask,
                    {trade_status_sql},
                    {delayed_sql}
                FROM public.{table_name}
            """

            cur.execute(sql)

            rows = cur.fetchall()

            if not rows:
                continue

            df = pd.DataFrame(
                rows,
                columns=[
                    "contract",
                    "bid",
                    "ask",
                    "trade status",
                    "delayed",
                ],
            )

            frames.append(df)

    # ========================================================
    # COMBINE
    # ========================================================

    if not frames:

        return pd.DataFrame(
            columns=[
                "contract",
                "bid",
                "ask",
                "trade status",
                "delayed",
            ]
        )

    df = pd.concat(
        frames,
        ignore_index=True,
    )

    # ========================================================
    # CLEAN DATA
    # ========================================================

    df["contract"] = (
        df["contract"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    df["bid"] = pd.to_numeric(
        df["bid"],
        errors="coerce",
    )

    df["ask"] = pd.to_numeric(
        df["ask"],
        errors="coerce",
    )

    df["trade status"] = (
        df["trade status"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    df["delayed"] = (
        df["delayed"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    # ========================================================
    # SORT BY CONTRACT
    # ========================================================

    df = (
        df
        .sort_values(
            by="contract",
            key=lambda x: x.str.lower(),
        )
        .reset_index(drop=True)
    )

    return df


# ============================================================
# FILTER
# ============================================================

def filter_market_data(
    df,
    filter_text,
):
    """
    Case-insensitive partial contract match.

    Examples:

        PA
        IB:PA
        gold
        BTC
    """

    filter_text = str(
        filter_text
    ).strip()

    if not filter_text:
        return df

    mask = (
        df["contract"]
        .str.contains(
            filter_text,
            case=False,
            na=False,
            regex=False,
        )
    )

    return (
        df.loc[mask]
        .reset_index(drop=True)
    )


# ============================================================
# FORMATTING
# ============================================================

def format_price(value):
    """
    Bid and ask always displayed with exactly 3 decimals.
    """

    if value is None:
        return ""

    if pd.isna(value):
        return ""

    return f"{float(value):,.3f}"


# ============================================================
# STYLING
# ============================================================

def style_market_data_table(df):

    styler = (
        df.style

        # Bid / ask = exactly 3 decimals
        .format(
            {
                "bid": format_price,
                "ask": format_price,
            }
        )

        # Centre all cells
        .set_properties(
            **{
                "text-align": "center",
            }
        )

        # Contract bold
        .set_properties(
            subset=["contract"],
            **{
                "font-weight": "bold",
                "text-align": "center",
            }
        )

        # Centre headers
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
    )

    return styler


# ============================================================
# PAGE
# ============================================================

def render_market_data_page():

    # ========================================================
    # FILTER
    # ========================================================

    filter_text = st.text_input(
        "Filter contract",
        value="",
        placeholder="Type contract...",
    )

    # ========================================================
    # LOAD LIVE DATA
    # ========================================================

    df = load_market_data()

    # ========================================================
    # APPLY FILTER
    # ========================================================

    display_df = filter_market_data(
        df,
        filter_text,
    )

    # ========================================================
    # RESULT COUNT
    # ========================================================

    if filter_text:

        st.caption(
            f"{len(display_df)} matching contracts"
        )

    # ========================================================
    # TABLE HEIGHT
    #
    # Don't let a huge unfiltered market-data table make the
    # entire web page excessively long.
    #
    # Up to ~20 rows are shown without internal scrolling.
    # ========================================================

    visible_rows = min(
        len(display_df),
        20,
    )

    table_height = (
        38
        + visible_rows * 35
    )

    # Avoid tiny empty table
    table_height = max(
        table_height,
        100,
    )

    # ========================================================
    # DISPLAY
    # ========================================================

    st.dataframe(
        style_market_data_table(
            display_df
        ),
        hide_index=True,
        use_container_width=False,
        width=750,
        height=table_height,
    )