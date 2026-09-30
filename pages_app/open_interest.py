import pandas as pd
import streamlit as st

from db import get_conn


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


def mx_contract(contract):
    """
    Add MX: prefix used in md_snap_moex.

    Examples:
        SiZ6  -> MX:SiZ6
        MXZ6  -> MX:MXZ6
        MX:SiZ6 -> MX:SiZ6
    """

    contract = clean_text(
        contract
    )

    if not contract:
        return ""

    if contract.upper().startswith(
        "MX:"
    ):
        return contract

    return "MX:" + contract


# ============================================================
# INPUT DATA
#
# b1_oi_input is reference/static data, so it is cached.
#
# Expected columns:
#
# und
# contract_front
# contract_back
# front_oi_reset
# back_oi_reset
# ============================================================

@st.cache_resource
def load_oi_inputs():
    conn = get_conn()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                und,
                contract_front,
                contract_back,
                front_oi_reset,
                back_oi_reset
            FROM public.b1_oi_input
            """
        )

        rows = cur.fetchall()

    df = pd.DataFrame(
        rows,
        columns=[
            "und",
            "contract_front",
            "contract_back",
            "front_oi_reset",
            "back_oi_reset",
        ],
    )

    if df.empty:
        return df

    # --------------------------------------------------------
    # TEXT COLUMNS
    # --------------------------------------------------------

    for col in [
        "und",
        "contract_front",
        "contract_back",
    ]:
        df[col] = (
            df[col]
            .fillna("")
            .astype(str)
            .str.strip()
        )

    # --------------------------------------------------------
    # NUMERIC COLUMNS
    # --------------------------------------------------------

    for col in [
        "front_oi_reset",
        "back_oi_reset",
    ]:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    return df


# ============================================================
# LIVE OPEN INTEREST
#
# md_snap_moex is NOT cached.
# It will therefore update with the page refresh.
# ============================================================

def load_live_open_interest(
    oi_inputs,
):
    """
    Reads current open_interest from md_snap_moex.

    Returns:

        {
            "MX:SIZ6": 10838142,
            "MX:SIH7": 96432,
            ...
        }

    Dictionary keys are uppercase to make matching robust.
    """

    if oi_inputs.empty:
        return {}

    required_contracts = set()

    # --------------------------------------------------------
    # FRONT CONTRACTS
    # --------------------------------------------------------

    for contract in (
        oi_inputs[
            "contract_front"
        ]
    ):
        contract = mx_contract(
            contract
        )

        if contract:
            required_contracts.add(
                contract
            )

    # --------------------------------------------------------
    # BACK CONTRACTS
    # --------------------------------------------------------

    for contract in (
        oi_inputs[
            "contract_back"
        ]
    ):
        contract = mx_contract(
            contract
        )

        if contract:
            required_contracts.add(
                contract
            )

    if not required_contracts:
        return {}

    conn = get_conn()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                contract,
                open_interest
            FROM public.md_snap_moex
            WHERE contract = ANY(%s)
            """,
            (
                list(
                    required_contracts
                ),
            ),
        )

        rows = cur.fetchall()

    result = {}

    for (
        contract,
        open_interest,
    ) in rows:

        contract = clean_text(
            contract
        )

        open_interest = to_float(
            open_interest
        )

        result[
            contract.upper()
        ] = open_interest

    return result


# ============================================================
# LIVE OI LOOKUP
# ============================================================

def get_live_oi(
    live_oi,
    contract,
):
    contract = mx_contract(
        contract
    )

    if not contract:
        return None

    return live_oi.get(
        contract.upper()
    )


# ============================================================
# OI CALCULATION
# ============================================================

def calculate_oi_leg(
    current_oi,
    reset_oi,
):
    """
    Calculate one futures leg.

    OI:
        current open interest

    OI Ch:
        current OI - reset OI

    OI Ch %:
        OI Ch / current OI
    """

    current_oi = to_float(
        current_oi
    )

    reset_oi = to_float(
        reset_oi
    )

    oi_change = None
    oi_change_pct = None

    if (
        current_oi is not None
        and reset_oi is not None
    ):
        oi_change = (
            current_oi
            - reset_oi
        )

        if current_oi != 0:
            oi_change_pct = (
                oi_change
                / current_oi
            )

    return (
        current_oi,
        oi_change,
        oi_change_pct,
    )


def calculate_open_interest(
    oi_inputs,
    live_oi,
):
    if oi_inputs.empty:
        return pd.DataFrame()

    rows = []

    for _, row in (
        oi_inputs.iterrows()
    ):

        und = clean_text(
            row["und"]
        )

        contract_front = (
            clean_text(
                row[
                    "contract_front"
                ]
            )
        )

        contract_back = (
            clean_text(
                row[
                    "contract_back"
                ]
            )
        )

        # ====================================================
        # FRONT
        # ====================================================

        front_current = (
            get_live_oi(
                live_oi,
                contract_front,
            )
        )

        (
            front_oi,
            front_oich,
            front_oich_pct,
        ) = calculate_oi_leg(
            current_oi=front_current,
            reset_oi=row[
                "front_oi_reset"
            ],
        )

        # ====================================================
        # BACK
        # ====================================================

        back_current = (
            get_live_oi(
                live_oi,
                contract_back,
            )
        )

        (
            back_oi,
            back_oich,
            back_oich_pct,
        ) = calculate_oi_leg(
            current_oi=back_current,
            reset_oi=row[
                "back_oi_reset"
            ],
        )

        rows.append(
            {
                "und": und,

                "front_contract": (
                    contract_front
                ),

                "front_oi": (
                    front_oi
                ),

                "front_oich": (
                    front_oich
                ),

                "front_oich_pct": (
                    front_oich_pct
                ),

                "back_contract": (
                    contract_back
                ),

                "back_oi": (
                    back_oi
                ),

                "back_oich": (
                    back_oich
                ),

                "back_oich_pct": (
                    back_oich_pct
                ),
            }
        )

    return pd.DataFrame(
        rows
    )


# ============================================================
# FORMATTERS
# ============================================================

def fmt_oi(value):
    """
    10838142 -> 10,838,142
    """

    if (
        value is None
        or pd.isna(value)
    ):
        return ""

    return f"{value:,.0f}"


def fmt_oi_change(value):
    """
    Positive values have explicit + sign.

    1000  -> +1,000
    -1000 -> -1,000
    """

    if (
        value is None
        or pd.isna(value)
    ):
        return ""

    return f"{value:+,.0f}"


def fmt_pct(value):
    """
    0.1445 -> +14.45%
    """

    if (
        value is None
        or pd.isna(value)
    ):
        return ""

    return f"{value:+.2%}"


# ============================================================
# BUILD DISPLAY TABLE
#
# Uses a Pandas MultiIndex for TRUE grouped headers:
#
#                  Front                       Back
# und      oi      oich    oi ch %     oi      oich    oi ch %
#
# This means Front / Back are part of the table itself,
# rather than separate Streamlit objects.
# ============================================================

def build_oi_display(
    calculated,
):
    columns = pd.MultiIndex.from_tuples(
        [
            (
                "",
                "und",
            ),

            (
                "Front",
                "oi",
            ),
            (
                "Front",
                "oich",
            ),
            (
                "Front",
                "oi ch %",
            ),

            (
                "Back",
                "oi",
            ),
            (
                "Back",
                "oich",
            ),
            (
                "Back",
                "oi ch %",
            ),
        ]
    )

    if calculated.empty:
        return pd.DataFrame(
            columns=columns
        )

    data = []

    for _, row in (
        calculated.iterrows()
    ):

        data.append(
            [
                clean_text(
                    row["und"]
                ),

                # --------------------------------------------
                # FRONT
                # --------------------------------------------

                fmt_oi(
                    row[
                        "front_oi"
                    ]
                ),

                fmt_oi_change(
                    row[
                        "front_oich"
                    ]
                ),

                fmt_pct(
                    row[
                        "front_oich_pct"
                    ]
                ),

                # --------------------------------------------
                # BACK
                # --------------------------------------------

                fmt_oi(
                    row[
                        "back_oi"
                    ]
                ),

                fmt_oi_change(
                    row[
                        "back_oich"
                    ]
                ),

                fmt_pct(
                    row[
                        "back_oich_pct"
                    ]
                ),
            ]
        )

    return pd.DataFrame(
        data,
        columns=columns,
    )


# ============================================================
# TABLE STYLE
# ============================================================

def style_oi_table(
    df,
):
    styler = (
        df.style

        # ----------------------------------------------------
        # ALL CELLS CENTERED
        # ----------------------------------------------------

        .set_properties(
            **{
                "text-align": (
                    "center"
                ),
            }
        )

        # ----------------------------------------------------
        # HEADERS CENTERED
        # ----------------------------------------------------

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

    # --------------------------------------------------------
    # UNDERLYING BOLD
    # --------------------------------------------------------

    if (
        "",
        "und",
    ) in df.columns:

        styler = (
            styler
            .set_properties(
                subset=[
                    (
                        "",
                        "und",
                    )
                ],
                **{
                    "font-weight": (
                        "bold"
                    ),
                },
            )
        )

    return styler


# ============================================================
# PAGE
# ============================================================

def render_open_interest_page():

    st.subheader(
        "Open Interest"
    )

    # ========================================================
    # INPUT DEFINITIONS
    #
    # Cached reference data.
    # ========================================================

    oi_inputs = (
        load_oi_inputs()
    )

    if oi_inputs.empty:

        st.warning(
            "No rows found in b1_oi_input."
        )

        return

    # ========================================================
    # LIVE OPEN INTEREST
    #
    # NOT cached.
    # ========================================================

    live_oi = (
        load_live_open_interest(
            oi_inputs
        )
    )

    # ========================================================
    # CALCULATE
    # ========================================================

    calculated = (
        calculate_open_interest(
            oi_inputs,
            live_oi,
        )
    )

    # ========================================================
    # DISPLAY
    # ========================================================

    display_df = (
        build_oi_display(
            calculated
        )
    )

    st.dataframe(
        style_oi_table(
            display_df
        ),
        hide_index=True,
        use_container_width=False,
        width=750,
        height=(
            70
            + len(
                display_df
            )
            * 35
        ),
    )