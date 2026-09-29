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


def to_bool(value):
    if isinstance(value, bool):
        return value

    if value is None:
        return False

    if isinstance(value, (int, float)):
        return bool(value)

    text = str(value).strip().lower()

    return text in {
        "true",
        "1",
        "yes",
        "y",
        "t",
    }


def is_section_row(desc):
    """
    Any description starting with *** is treated
    as a section/header row.

    Example:
        *** Precious Metals

    displayed as:
        Precious Metals
    """

    return clean_text(desc).startswith("***")


def get_section_name(desc):
    desc = clean_text(desc)

    if desc.startswith("***"):
        return desc[3:].strip()

    return desc


# ============================================================
# SPREAD FORMATTING
# ============================================================

def normalize_n_dec(value):
    """
    Convert n_dec to a non-negative integer.

    Default = 2 if missing/invalid.
    """

    if value is None:
        return 2

    try:
        value = int(
            float(value)
        )
    except (
        TypeError,
        ValueError,
    ):
        return 2

    return max(
        value,
        0,
    )


def format_spread(
    value,
    n_dec,
):
    """
    Examples:

        n_dec = 0 -> 57
        n_dec = 1 -> 24.1
        n_dec = 2 -> 24.12
    """

    if (
        value is None
        or pd.isna(value)
    ):
        return ""

    n_dec = normalize_n_dec(
        n_dec
    )

    return f"{value:.{n_dec}f}"


def format_price(value):
    if (
        value is None
        or pd.isna(value)
    ):
        return ""

    return f"{value:,.3f}"


# ============================================================
# LOAD SPREAD DEFINITIONS
#
# Cached for the lifetime of the app.
# ============================================================

@st.cache_resource
def load_spreads_input():
    conn = get_conn()

    with conn.cursor() as cur:
        cur.execute(
            """
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
                srcfx,
                n_dec
            FROM public.b1_spreads_inputs
            """
        )

        rows = cur.fetchall()

    df = pd.DataFrame(
        rows,
        columns=[
            "desc",
            "contract_1",
            "contract_2",
            "fx_hedge",
            "contract_fx",
            "mult_1",
            "mult_2",
            "mult_fx",
            "offset",
            "src1",
            "src2",
            "srcfx",
            "n_dec",
        ],
    )

    if df.empty:
        return df

    # --------------------------------------------------------
    # TEXT
    # --------------------------------------------------------

    for col in [
        "desc",
        "contract_1",
        "contract_2",
        "contract_fx",
        "src1",
        "src2",
        "srcfx",
    ]:
        df[col] = (
            df[col]
            .fillna("")
            .astype(str)
            .str.strip()
        )

    # --------------------------------------------------------
    # NUMBERS
    # --------------------------------------------------------

    for col in [
        "mult_1",
        "mult_2",
        "mult_fx",
        "offset",
        "n_dec",
    ]:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    # --------------------------------------------------------
    # BOOLEAN
    # --------------------------------------------------------

    df["fx_hedge"] = (
        df["fx_hedge"]
        .apply(to_bool)
    )

    return df


# ============================================================
# DETERMINE REQUIRED MARKET DATA
# ============================================================

def get_required_contracts(
    spreads_input,
):
    """
    Returns:

        {
            "md_snap": {"IB:GCZ6", ...},
            "md_snap_moex": {"MX:LKOH", ...},
            ...
        }

    Section rows are ignored.
    """

    required = {}

    if spreads_input.empty:
        return required

    for _, row in (
        spreads_input.iterrows()
    ):

        desc = clean_text(
            row["desc"]
        )

        # ----------------------------------------------------
        # SECTION ROW
        # ----------------------------------------------------

        if is_section_row(desc):
            continue

        # ----------------------------------------------------
        # CONTRACT 1
        # ----------------------------------------------------

        src1 = clean_text(
            row["src1"]
        )

        contract_1 = clean_text(
            row["contract_1"]
        )

        if (
            src1 in ALLOWED_SOURCE_TABLES
            and contract_1
        ):
            required.setdefault(
                src1,
                set(),
            ).add(
                contract_1
            )

        # ----------------------------------------------------
        # CONTRACT 2
        # ----------------------------------------------------

        src2 = clean_text(
            row["src2"]
        )

        contract_2 = clean_text(
            row["contract_2"]
        )

        if (
            src2 in ALLOWED_SOURCE_TABLES
            and contract_2
        ):
            required.setdefault(
                src2,
                set(),
            ).add(
                contract_2
            )

        # ----------------------------------------------------
        # FX CONTRACT
        # ----------------------------------------------------

        if to_bool(
            row["fx_hedge"]
        ):

            srcfx = clean_text(
                row["srcfx"]
            )

            contract_fx = clean_text(
                row["contract_fx"]
            )

            if (
                srcfx
                in ALLOWED_SOURCE_TABLES
                and contract_fx
            ):
                required.setdefault(
                    srcfx,
                    set(),
                ).add(
                    contract_fx
                )

    return required


# ============================================================
# LOAD LIVE MARKET DATA
# ============================================================

def load_market_table(
    table_name,
    contracts,
):
    """
    Loads bid/ask from one approved Supabase table.

    Returns:
        {
            contract: {
                "bid": ...,
                "ask": ...,
                "mid": ...
            }
        }
    """

    if (
        table_name
        not in ALLOWED_SOURCE_TABLES
    ):
        raise ValueError(
            f"Unsupported source table: {table_name}"
        )

    if not contracts:
        return {}

    conn = get_conn()

    # Table name cannot be passed as a normal SQL parameter,
    # therefore only whitelisted table names are accepted.
    sql = f"""
        SELECT
            contract,
            bid,
            ask
        FROM public.{table_name}
        WHERE contract = ANY(%s)
    """

    with conn.cursor() as cur:
        cur.execute(
            sql,
            (
                list(contracts),
            ),
        )

        rows = cur.fetchall()

    result = {}

    for (
        contract,
        bid,
        ask,
    ) in rows:

        contract = clean_text(
            contract
        )

        bid = to_float(
            bid
        )

        ask = to_float(
            ask
        )

        if (
            bid is not None
            and ask is not None
        ):
            mid = (
                bid + ask
            ) / 2.0

        elif bid is not None:
            mid = bid

        elif ask is not None:
            mid = ask

        else:
            mid = None

        result[
            contract
        ] = {
            "bid": bid,
            "ask": ask,
            "mid": mid,
        }

    return result


def load_all_market_data(
    spreads_input,
):
    """
    Load only the contracts required by the
    current spread definitions.
    """

    required = (
        get_required_contracts(
            spreads_input
        )
    )

    market_data = {}

    for (
        table_name,
        contracts,
    ) in required.items():

        market_data[
            table_name
        ] = load_market_table(
            table_name,
            contracts,
        )

    return market_data


# ============================================================
# MARKET PRICE LOOKUP
# ============================================================

def get_price(
    market_data,
    source,
    contract,
):
    source = clean_text(
        source
    )

    contract = clean_text(
        contract
    )

    if (
        not source
        or not contract
    ):
        return {
            "bid": None,
            "ask": None,
            "mid": None,
        }

    source_data = (
        market_data.get(
            source,
            {},
        )
    )

    return source_data.get(
        contract,
        {
            "bid": None,
            "ask": None,
            "mid": None,
        },
    )


# ============================================================
# CALCULATE SPREADS
# ============================================================

def calculate_spreads(
    spreads_input,
    market_data,
):
    """
    Spread:

        mid1 * mult_1
        - mid2 * mult_2 / FX
        + offset

    where:

        FX = fx_mid * mult_fx

    if fx_hedge = False:

        FX = 1

    Section rows are returned as structural rows
    and are not calculated.
    """

    output_rows = []

    if spreads_input.empty:
        return pd.DataFrame()

    for _, row in (
        spreads_input.iterrows()
    ):

        desc = clean_text(
            row["desc"]
        )

        # ====================================================
        # SECTION ROW
        # ====================================================

        if is_section_row(desc):

            output_rows.append(
                {
                    "desc": (
                        get_section_name(
                            desc
                        )
                    ),
                    "spread": "",
                    "n_dec": None,
                    "is_section": True,

                    "contract_1": "",
                    "src1": "",
                    "bid1": None,
                    "ask1": None,
                    "mid1": None,

                    "contract_2": "",
                    "src2": "",
                    "bid2": None,
                    "ask2": None,
                    "mid2": None,

                    "contract_fx": "",
                    "srcfx": "",
                    "fx_bid": None,
                    "fx_ask": None,
                    "fx_mid": None,
                }
            )

            continue

        # ====================================================
        # NORMAL SPREAD ROW
        # ====================================================

        contract_1 = clean_text(
            row["contract_1"]
        )

        contract_2 = clean_text(
            row["contract_2"]
        )

        contract_fx = clean_text(
            row["contract_fx"]
        )

        src1 = clean_text(
            row["src1"]
        )

        src2 = clean_text(
            row["src2"]
        )

        srcfx = clean_text(
            row["srcfx"]
        )

        fx_hedge = to_bool(
            row["fx_hedge"]
        )

        mult_1 = to_float(
            row["mult_1"]
        )

        mult_2 = to_float(
            row["mult_2"]
        )

        mult_fx = to_float(
            row["mult_fx"]
        )

        offset = to_float(
            row["offset"]
        )

        n_dec = normalize_n_dec(
            row["n_dec"]
        )

        # Defaults
        if mult_1 is None:
            mult_1 = 1.0

        if mult_2 is None:
            mult_2 = 1.0

        if mult_fx is None:
            mult_fx = 1.0

        if offset is None:
            offset = 0.0

        # ----------------------------------------------------
        # CONTRACT 1
        # ----------------------------------------------------

        p1 = get_price(
            market_data,
            src1,
            contract_1,
        )

        mid1 = p1["mid"]

        # ----------------------------------------------------
        # CONTRACT 2
        # ----------------------------------------------------

        p2 = get_price(
            market_data,
            src2,
            contract_2,
        )

        mid2 = p2["mid"]

        # ----------------------------------------------------
        # FX
        # ----------------------------------------------------

        fx_bid = None
        fx_ask = None
        fx_mid = None

        fx_value = 1.0

        if fx_hedge:

            pfx = get_price(
                market_data,
                srcfx,
                contract_fx,
            )

            fx_bid = pfx[
                "bid"
            ]

            fx_ask = pfx[
                "ask"
            ]

            fx_mid = pfx[
                "mid"
            ]

            if fx_mid is not None:
                fx_value = (
                    fx_mid
                    * mult_fx
                )
            else:
                fx_value = None

        # ----------------------------------------------------
        # SPREAD
        # ----------------------------------------------------

        spread = None

        if (
            mid1 is not None
            and mid2 is not None
            and fx_value is not None
            and fx_value != 0
        ):
            spread = (
                mid1
                * mult_1
                - (
                    mid2
                    * mult_2
                    / fx_value
                )
                + offset
            )

        output_rows.append(
            {
                "desc": desc,

                # Keep the actual number here.
                "spread": spread,

                "n_dec": n_dec,
                "is_section": False,

                "contract_1": contract_1,
                "src1": src1,
                "bid1": p1["bid"],
                "ask1": p1["ask"],
                "mid1": mid1,

                "contract_2": contract_2,
                "src2": src2,
                "bid2": p2["bid"],
                "ask2": p2["ask"],
                "mid2": mid2,

                "contract_fx": (
                    contract_fx
                    if fx_hedge
                    else ""
                ),
                "srcfx": (
                    srcfx
                    if fx_hedge
                    else ""
                ),
                "fx_bid": fx_bid,
                "fx_ask": fx_ask,
                "fx_mid": fx_mid,
            }
        )

    return pd.DataFrame(
        output_rows
    )


# ============================================================
# DISPLAY TABLE
# ============================================================

def build_spreads_display(
    calculated,
    show_sources,
):
    if calculated.empty:
        return pd.DataFrame()

    rows = []

    for _, row in (
        calculated.iterrows()
    ):

        is_section = bool(
            row["is_section"]
        )

        # ====================================================
        # SECTION ROW
        # ====================================================

        if is_section:

            item = {
                "description": (
                    clean_text(
                        row["desc"]
                    )
                ),
                "spread": "",
            }

            if show_sources:

                item.update(
                    {
                        "contract 1": "",
                        "src 1": "",
                        "mid 1": "",
                        "contract 2": "",
                        "src 2": "",
                        "mid 2": "",
                        "FX contract": "",
                        "FX src": "",
                        "FX mid": "",
                    }
                )

            rows.append(
                item
            )

            continue

        # ====================================================
        # NORMAL ROW
        # ====================================================

        item = {
            "description": (
                clean_text(
                    row["desc"]
                )
            ),

            "spread": (
                format_spread(
                    row["spread"],
                    row["n_dec"],
                )
            ),
        }

        if show_sources:

            item.update(
                {
                    "contract 1": (
                        clean_text(
                            row[
                                "contract_1"
                            ]
                        )
                    ),

                    "src 1": (
                        clean_text(
                            row[
                                "src1"
                            ]
                        )
                    ),

                    "mid 1": (
                        format_price(
                            row[
                                "mid1"
                            ]
                        )
                    ),

                    "contract 2": (
                        clean_text(
                            row[
                                "contract_2"
                            ]
                        )
                    ),

                    "src 2": (
                        clean_text(
                            row[
                                "src2"
                            ]
                        )
                    ),

                    "mid 2": (
                        format_price(
                            row[
                                "mid2"
                            ]
                        )
                    ),

                    "FX contract": (
                        clean_text(
                            row[
                                "contract_fx"
                            ]
                        )
                    ),

                    "FX src": (
                        clean_text(
                            row[
                                "srcfx"
                            ]
                        )
                    ),

                    "FX mid": (
                        format_price(
                            row[
                                "fx_mid"
                            ]
                        )
                    ),
                }
            )

        rows.append(
            item
        )

    return pd.DataFrame(
        rows
    )


# ============================================================
# STYLE
# ============================================================

def style_spreads_table(
    display_df,
    calculated_df,
):
    if display_df.empty:
        return display_df.style

    section_indexes = []

    for idx, row in (
        calculated_df
        .reset_index(drop=True)
        .iterrows()
    ):
        if bool(
            row["is_section"]
        ):
            section_indexes.append(
                idx
            )

    def style_row(row):

        if (
            row.name
            in section_indexes
        ):
            return [
                (
                    "font-weight: bold; "
                    "text-align: left;"
                    if col
                    == "description"
                    else ""
                )
                for col
                in row.index
            ]

        return [
            ""
            for _ in row.index
        ]

    styler = (
        display_df.style
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
        .apply(
            style_row,
            axis=1,
        )
    )

    # --------------------------------------------------------
    # NORMAL DESCRIPTION COLUMN
    # --------------------------------------------------------

    if (
        "description"
        in display_df.columns
    ):
        styler = (
            styler
            .set_properties(
                subset=[
                    "description"
                ],
                **{
                    "text-align": (
                        "left"
                    ),
                },
            )
        )

    # --------------------------------------------------------
    # SPREAD BOLD
    # --------------------------------------------------------

    if (
        "spread"
        in display_df.columns
    ):
        styler = (
            styler
            .set_properties(
                subset=[
                    "spread"
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

def render_spreads_page():

    st.subheader(
        "Spreads"
    )

    # --------------------------------------------------------
    # INPUT DEFINITIONS
    #
    # Cached once.
    # --------------------------------------------------------

    df_inputs = (
        load_spreads_input()
    )

    if df_inputs.empty:

        st.warning(
            "No spread definitions found in b1_spreads_inputs."
        )

        return

    # --------------------------------------------------------
    # LIVE MARKET DATA
    #
    # Not cached here, so it refreshes with the page.
    # --------------------------------------------------------

    market_data = (
        load_all_market_data(
            df_inputs
        )
    )

    # --------------------------------------------------------
    # CALCULATE
    # --------------------------------------------------------

    calculated = (
        calculate_spreads(
            df_inputs,
            market_data,
        )
    )

    # --------------------------------------------------------
    # OPTIONS
    # --------------------------------------------------------

    show_sources = (
        st.toggle(
            "Show sources",
            value=False,
            key=(
                "spreads_"
                "show_sources"
            ),
        )
    )

    # --------------------------------------------------------
    # DISPLAY
    # --------------------------------------------------------

    display_df = (
        build_spreads_display(
            calculated,
            show_sources,
        )
    )

    styled = (
        style_spreads_table(
            display_df,
            calculated,
        )
    )

    table_width = (
        1250
        if show_sources
        else 500
    )

    table_height = min(
        38
        + len(
            display_df
        )
        * 35,
        900,
    )

    st.dataframe(
        styled,
        hide_index=True,
        use_container_width=False,
        width=table_width,
        height=table_height,
    )