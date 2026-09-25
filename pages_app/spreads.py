import pandas as pd
import streamlit as st

from db import get_conn


# ============================================================
# SETTINGS
# ============================================================

ALLOWED_SOURCE_TABLES = {
    "md_snap",
    "md_snap_moex",
    "md_snap_bb",
    "md_snap_hl",
    "b1_price_mapping",
}


# ============================================================
# LOAD SPREAD DEFINITIONS
# Loaded once per Streamlit process
# ============================================================

@st.cache_resource
def load_spreads_input():
    """
    Load spread definitions once when the Streamlit app starts.

    If b1_spreads_inputs is changed in Supabase,
    restart Streamlit to reload the definitions.
    """

    conn = get_conn()

    sql = """
        SELECT
            "desc",
            contract_1,
            contract_2,
            fx_hedge,
            contract_fx,
            mult_1,
            mult_2,
            mult_fx,
            "offset",
            src1,
            src2,
            srcfx
        FROM public.b1_spreads_inputs
    """

    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description]

    df = pd.DataFrame(rows, columns=columns)

    # --------------------------------------------------------
    # Clean string columns
    # --------------------------------------------------------

    string_cols = [
        "desc",
        "contract_1",
        "contract_2",
        "contract_fx",
        "src1",
        "src2",
        "srcfx",
    ]

    for col in string_cols:
        df[col] = (
            df[col]
            .fillna("")
            .astype(str)
            .str.strip()
        )

    # --------------------------------------------------------
    # Numeric columns
    # --------------------------------------------------------

    numeric_cols = [
        "mult_1",
        "mult_2",
        "mult_fx",
        "offset",
    ]

    for col in numeric_cols:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    # --------------------------------------------------------
    # Boolean fx_hedge
    # --------------------------------------------------------

    def to_bool(value):

        if isinstance(value, bool):
            return value

        return str(value).strip().lower() in {
            "true",
            "1",
            "yes",
            "y",
            "t",
        }

    df["fx_hedge"] = df["fx_hedge"].apply(to_bool)

    return df


# ============================================================
# GET REQUIRED MARKET-DATA SOURCES
# ============================================================

def get_required_sources(df_inputs):
    """
    Build dictionary:

        {
            "md_snap": {"IB:CCZ6", ...},
            "md_snap_moex": {"MX:CCX6", ...},
            "md_snap_bb": {...},
            ...
        }

    Only instruments actually required for spread calculations
    are requested from Supabase.
    """

    required = {}

    def add_source(table_name, contract):

        table_name = str(table_name).strip()
        contract = str(contract).strip()

        if not table_name:
            return

        if not contract:
            return

        if table_name.lower() in {
            "nan",
            "none",
        }:
            return

        if contract.lower() in {
            "nan",
            "none",
        }:
            return

        if table_name not in required:
            required[table_name] = set()

        required[table_name].add(contract)

    # --------------------------------------------------------
    # Build required source list
    # --------------------------------------------------------

    for _, row in df_inputs.iterrows():

        # Leg 1
        add_source(
            row["src1"],
            row["contract_1"],
        )

        # Leg 2
        add_source(
            row["src2"],
            row["contract_2"],
        )

        # FX source only when FX hedge is enabled
        if row["fx_hedge"]:

            add_source(
                row["srcfx"],
                row["contract_fx"],
            )

    return required


# ============================================================
# VALIDATE SOURCE TABLE
# ============================================================

def validate_table_name(table_name):
    """
    SQL table names cannot be passed as query parameters.

    Therefore only explicitly permitted source tables can
    be referenced.
    """

    if table_name not in ALLOWED_SOURCE_TABLES:

        raise ValueError(
            f"Unsupported market-data source table: "
            f"{table_name}"
        )


# ============================================================
# LOAD LIVE PRICES
# Called again on every Streamlit refresh
# ============================================================

def load_live_prices(df_inputs):
    """
    Load bid/ask for all instruments required by the current
    spread definitions.

    Returns:

        prices[(table_name, contract)] = {
            "bid": ...,
            "ask": ...,
            "mid": ...
        }
    """

    conn = get_conn()

    required = get_required_sources(df_inputs)

    prices = {}

    with conn.cursor() as cur:

        for table_name, contracts in required.items():

            validate_table_name(table_name)

            contracts = list(contracts)

            if not contracts:
                continue

            sql = f"""
                SELECT
                    contract,
                    bid,
                    ask
                FROM public.{table_name}
                WHERE contract = ANY(%s)
            """

            cur.execute(
                sql,
                (contracts,),
            )

            rows = cur.fetchall()

            for contract, bid, ask in rows:

                bid = pd.to_numeric(
                    bid,
                    errors="coerce",
                )

                ask = pd.to_numeric(
                    ask,
                    errors="coerce",
                )

                # --------------------------------------------
                # Mid price
                # --------------------------------------------

                if (
                    pd.notna(bid)
                    and pd.notna(ask)
                ):

                    mid = (
                        float(bid)
                        + float(ask)
                    ) / 2

                else:

                    mid = None

                prices[
                    (table_name, contract)
                ] = {
                    "bid": bid,
                    "ask": ask,
                    "mid": mid,
                }

    return prices


# ============================================================
# GET MID PRICE
# ============================================================

def get_mid(
    prices,
    table_name,
    contract,
):
    """
    Return mid price for a specific
    source-table / contract pair.
    """

    table_name = str(table_name).strip()
    contract = str(contract).strip()

    data = prices.get(
        (
            table_name,
            contract,
        )
    )

    if data is None:
        return None

    return data["mid"]


# ============================================================
# NUMBER FORMATTING
# ============================================================

def format_price(value):
    """
    Source prices:

    show up to 6 decimals,
    remove unnecessary trailing zeros.
    """

    if value is None:
        return ""

    if pd.isna(value):
        return ""

    return (
        f"{float(value):,.6f}"
        .rstrip("0")
        .rstrip(".")
    )


def format_spread(value):
    """
    Spread always displayed with 2 decimals.
    """

    if value is None:
        return ""

    if pd.isna(value):
        return ""

    return f"{float(value):,.2f}"


# ============================================================
# BUILD SPREAD TABLE
# ============================================================

def build_spreads_table(
    df_inputs,
    prices,
):

    output_rows = []

    for _, row in df_inputs.iterrows():

        # ====================================================
        # LEG 1
        # ====================================================

        mid1 = get_mid(
            prices,
            row["src1"],
            row["contract_1"],
        )

        # ====================================================
        # LEG 2
        # ====================================================

        mid2 = get_mid(
            prices,
            row["src2"],
            row["contract_2"],
        )

        # ====================================================
        # FX
        # ====================================================

        fx_mid = None

        # Default: no FX conversion
        fx_divisor = 1.0

        if row["fx_hedge"]:

            fx_mid = get_mid(
                prices,
                row["srcfx"],
                row["contract_fx"],
            )

            if (
                fx_mid is not None
                and pd.notna(fx_mid)
                and pd.notna(row["mult_fx"])
            ):

                fx_divisor = (
                    float(fx_mid)
                    * float(row["mult_fx"])
                )

            else:

                fx_divisor = None

        # ====================================================
        # CALCULATE SPREAD
        #
        # spread =
        #
        #     mid1 * mult_1
        #
        #     -
        #
        #     mid2 * mult_2
        #     -----------------
        #     FX
        #
        #     +
        #
        #     offset
        #
        #
        # FX =
        #
        #     fx_mid * mult_fx
        #
        # when fx_hedge = TRUE
        #
        # otherwise:
        #
        #     FX = 1
        # ====================================================

        spread = None

        if (
            mid1 is not None
            and mid2 is not None
            and pd.notna(mid1)
            and pd.notna(mid2)
            and pd.notna(row["mult_1"])
            and pd.notna(row["mult_2"])
            and pd.notna(row["offset"])
            and fx_divisor is not None
            and fx_divisor != 0
        ):

            spread = (
                float(mid1)
                * float(row["mult_1"])

                -

                (
                    float(mid2)
                    * float(row["mult_2"])
                    / float(fx_divisor)
                )

                +

                float(row["offset"])
            )

        # ====================================================
        # SOURCE COLUMNS
        # ====================================================

        src1 = row["contract_1"]
        src2 = row["contract_2"]

        if row["fx_hedge"]:

            srcfx = row["contract_fx"]
            fx_display = fx_mid

        else:

            srcfx = ""
            fx_display = None

        # ====================================================
        # OUTPUT ROW
        # ====================================================

        output_rows.append(
            {
                "spread name": row["desc"],
                "spread": spread,

                "src1": src1,
                "src1_px": mid1,

                "src2": src2,
                "src2_px": mid2,

                "srcfx": srcfx,
                "fx": fx_display,
            }
        )

    return pd.DataFrame(
        output_rows
    )


# ============================================================
# TABLE STYLING
# ============================================================

def style_spreads_table(df):

    # --------------------------------------------------------
    # Only format columns that are currently displayed
    # --------------------------------------------------------

    format_dict = {}

    if "spread" in df.columns:
        format_dict["spread"] = format_spread

    if "src1_px" in df.columns:
        format_dict["src1_px"] = format_price

    if "src2_px" in df.columns:
        format_dict["src2_px"] = format_price

    if "fx" in df.columns:
        format_dict["fx"] = format_price

    # --------------------------------------------------------
    # Styling
    # --------------------------------------------------------

    styler = (
        df.style

        .format(
            format_dict
        )

        # Centre all table cells
        .set_properties(
            **{
                "text-align": "center",
            }
        )

        # Spread name bold
        .set_properties(
            subset=["spread name"],
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

def render_spreads_page():

    # ========================================================
    # INPUT DEFINITIONS
    #
    # Cached once per application process
    # ========================================================

    df_inputs = load_spreads_input()

    # ========================================================
    # LIVE PRICES
    #
    # This runs again every time Streamlit refreshes.
    #
    # main.py already controls the refresh interval.
    # ========================================================

    prices = load_live_prices(
        df_inputs
    )

    # ========================================================
    # CALCULATE SPREADS
    # ========================================================

    df = build_spreads_table(
        df_inputs,
        prices,
    )

    # ========================================================
    # SHOW / HIDE SOURCE DETAILS
    # ========================================================

    show_sources = st.toggle(
        "Show sources",
        value=False,
    )

    # ========================================================
    # DISPLAY COLUMNS
    # ========================================================

    if show_sources:

        display_df = df[
            [
                "spread name",
                "spread",

                "src1",
                "src1_px",

                "src2",
                "src2_px",

                "srcfx",
                "fx",
            ]
        ]

    else:

        display_df = df[
            [
                "spread name",
                "spread",
            ]
        ]

    # ========================================================
    # TABLE HEIGHT
    #
    # Dynamically sized so all rows are visible without
    # needing to expand or vertically scroll the table.
    # ========================================================

    table_height = (
        38
        + len(display_df) * 35
    )

    # ========================================================
    # TABLE WIDTH
    #
    # Keep normal view compact.
    # Allow more space when source information is visible.
    # ========================================================

    if show_sources:
        table_width = 1050
    else:
        table_width = 450

    # ========================================================
    # DISPLAY TABLE
    # ========================================================

    st.dataframe(
        style_spreads_table(
            display_df
        ),
        hide_index=True,
        use_container_width=False,
        width=table_width,
        height=table_height,
    )