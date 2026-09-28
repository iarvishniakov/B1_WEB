import pandas as pd
import streamlit as st

from db import get_conn


# ============================================================
# SETTINGS
# ============================================================

INDEX_LIST = ["IMOEX2", "RTSI", "RGBI"]

DAYS_IN_YEAR = 365.0


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
    Convert:
        LKOH          -> MX:LKOH
        SU26212RMFS9  -> MX:SU26212RMFS9
        MX:LKOH       -> MX:LKOH
    """

    contract = clean_text(contract)

    if not contract:
        return ""

    if contract.upper().startswith("MX:"):
        return contract

    return "MX:" + contract


# ============================================================
# REFERENCE DATA
#
# Loaded once per application launch.
# ============================================================

@st.cache_resource
def load_index_reference_data():

    conn = get_conn()

    # ========================================================
    # IMOEX MEMBERS
    # ========================================================

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT
                contract,
                n_shares,
                ff,
                k_cap
            FROM public.b1_imoex_memb
            """
        )

        rows = cur.fetchall()

    imoex = pd.DataFrame(
        rows,
        columns=[
            "contract",
            "n_shares",
            "ff",
            "k_cap",
        ],
    )

    # ========================================================
    # RGBI MEMBERS
    # ========================================================

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT
                contract,
                n_shares,
                ff,
                w
            FROM public.b1_rgbi_memb
            """
        )

        rows = cur.fetchall()

    rgbi = pd.DataFrame(
        rows,
        columns=[
            "contract",
            "n_shares",
            "ff",
            "w",
        ],
    )

    # ========================================================
    # DIVISORS
    #
    # Actual table:
    #
    # index | dev
    # IMOEX | ...
    # RGBI  | ...
    # RTSI  | ...
    # ========================================================

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT
                index,
                dev
            FROM public.b1_devisors
            """
        )

        rows = cur.fetchall()

    divisors = pd.DataFrame(
        rows,
        columns=[
            "index",
            "dev",
        ],
    )

    # ========================================================
    # CLEAN IMOEX
    # ========================================================

    if not imoex.empty:

        imoex["contract"] = (
            imoex["contract"]
            .fillna("")
            .astype(str)
            .str.strip()
        )

        for col in [
            "n_shares",
            "ff",
            "k_cap",
        ]:

            imoex[col] = pd.to_numeric(
                imoex[col],
                errors="coerce",
            )

    # ========================================================
    # CLEAN RGBI
    # ========================================================

    if not rgbi.empty:

        rgbi["contract"] = (
            rgbi["contract"]
            .fillna("")
            .astype(str)
            .str.strip()
        )

        for col in [
            "n_shares",
            "ff",
            "w",
        ]:

            rgbi[col] = pd.to_numeric(
                rgbi[col],
                errors="coerce",
            )

    # ========================================================
    # CLEAN DIVISORS
    # ========================================================

    if not divisors.empty:

        divisors["index"] = (
            divisors["index"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.upper()
        )

        divisors["dev"] = pd.to_numeric(
            divisors["dev"],
            errors="coerce",
        )

    return {
        "imoex": imoex,
        "rgbi": rgbi,
        "divisors": divisors,
    }


# ============================================================
# DIVISOR LOOKUP
# ============================================================

def get_divisor(
    divisors,
    index_name,
):

    if divisors.empty:
        return None

    target = (
        clean_text(index_name)
        .upper()
    )

    matched = divisors[
        divisors["index"]
        .eq(target)
    ]

    if matched.empty:
        return None

    return to_float(
        matched.iloc[0]["dev"]
    )


# ============================================================
# LIVE DATA
#
# NOT CACHED.
#
# Prices, duration, yield and CBR USD update on every
# dashboard refresh.
# ============================================================

def load_live_index_data(
    reference,
):

    conn = get_conn()

    imoex = reference["imoex"]
    rgbi = reference["rgbi"]

    required_contracts = set()

    # ========================================================
    # IMOEX MEMBERS
    # ========================================================

    for contract in imoex["contract"]:

        contract = mx_contract(
            contract
        )

        if contract:
            required_contracts.add(
                contract
            )

    # ========================================================
    # RGBI MEMBERS
    # ========================================================

    for contract in rgbi["contract"]:

        contract = mx_contract(
            contract
        )

        if contract:
            required_contracts.add(
                contract
            )

    # ========================================================
    # OFFICIAL INDICES
    # ========================================================

    required_contracts.update(
        [
            "MX:IMOEX2",
            "MX:RTSI",
            "MX:RGBI",
        ]
    )

    # ========================================================
    # md_snap_moex
    #
    # No MTM column.
    #
    # MTM = midpoint of bid / ask.
    # ========================================================

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT
                contract,
                bid,
                ask,
                duration,
                yield
            FROM public.md_snap_moex
            WHERE contract = ANY(%s)
            """,
            (
                list(required_contracts),
            ),
        )

        rows = cur.fetchall()

    market = {}

    for (
        contract,
        bid,
        ask,
        duration,
        yield_value,
    ) in rows:

        bid = to_float(
            bid
        )

        ask = to_float(
            ask
        )

        # ----------------------------------------------------
        # MTM
        # ----------------------------------------------------

        if (
            bid is not None
            and ask is not None
        ):

            mtm = (
                bid + ask
            ) / 2.0

        elif bid is not None:

            mtm = bid

        elif ask is not None:

            mtm = ask

        else:

            mtm = None

        market[
            clean_text(contract)
        ] = {
            "bid": bid,
            "ask": ask,
            "mtm": mtm,

            "duration": (
                to_float(duration)
            ),

            "yield": (
                to_float(
                    yield_value
                )
            ),
        }

    # ========================================================
    # CBR USD
    # ========================================================

    with conn.cursor() as cur:

        cur.execute(
            """
            SELECT *
            FROM public.b1_cbr
            """
        )

        rows = cur.fetchall()

        columns = [
            d.name
            for d in cur.description
        ]

    cbr = pd.DataFrame(
        rows,
        columns=columns,
    )

    usd_cbr = None

    # ========================================================
    # FIND CURRENCY COLUMN
    # ========================================================

    code_col = None

    for candidate in [
        "contract",
        "ccy",
        "currency",
        "code",
    ]:

        if candidate in cbr.columns:

            code_col = candidate
            break

    # ========================================================
    # USD MTM
    # ========================================================

    if (
        code_col is not None
        and "mtm" in cbr.columns
    ):

        mask = (
            cbr[code_col]
            .astype(str)
            .str.strip()
            .str.upper()
            == "USD"
        )

        usd_rows = cbr[
            mask
        ]

        if not usd_rows.empty:

            usd_cbr = to_float(
                usd_rows.iloc[0]["mtm"]
            )

    return (
        market,
        usd_cbr,
    )


# ============================================================
# MARKET LOOKUPS
# ============================================================

def get_market_row(
    market,
    contract,
):

    return market.get(
        mx_contract(contract)
    )


def get_mtm(
    market,
    contract,
):

    row = get_market_row(
        market,
        contract,
    )

    if row is None:
        return None

    return row["mtm"]


# ============================================================
# IMOEX
#
# contribution =
#
# MTM × n_shares × ff × k_cap
#
# IMOEX =
#
# SUM(contribution) / divisor
#
# Member weight =
#
# contribution / total contribution
# ============================================================

def calculate_imoex(
    members,
    market,
    divisor,
):

    rows = []

    total_contribution = 0.0

    for _, member in members.iterrows():

        contract = clean_text(
            member["contract"]
        )

        mtm = get_mtm(
            market,
            contract,
        )

        n_shares = to_float(
            member["n_shares"]
        )

        ff = to_float(
            member["ff"]
        )

        k_cap = to_float(
            member["k_cap"]
        )

        contribution = None

        if (
            mtm is not None
            and n_shares is not None
            and ff is not None
            and k_cap is not None
        ):

            contribution = (
                mtm
                * n_shares
                * ff
                * k_cap
            )

            total_contribution += (
                contribution
            )

        rows.append(
            {
                "contract": contract,
                "mtm": mtm,
                "contribution": (
                    contribution
                ),
            }
        )

    members_live = pd.DataFrame(
        rows
    )

    # ========================================================
    # MEMBER WEIGHTS
    # ========================================================

    if (
        not members_live.empty
        and total_contribution != 0
    ):

        members_live["weight"] = (
            members_live[
                "contribution"
            ]
            / total_contribution
        )

    else:

        members_live["weight"] = None

    # ========================================================
    # INDEX VALUE
    # ========================================================

    calculated = None

    if (
        divisor is not None
        and divisor != 0
        and total_contribution != 0
    ):

        calculated = (
            total_contribution
            / divisor
        )

    return (
        calculated,
        total_contribution,
        members_live,
    )


# ============================================================
# RGBI
#
# contribution =
#
# MTM × n_shares × ff × w
#
# RGBI =
#
# SUM(contribution) / divisor
#
# IMPORTANT:
#
# w in b1_rgbi_memb is a calculation coefficient.
# It is NOT the displayed index weight.
#
# Display weight =
#
# contribution / total contribution
# ============================================================

def calculate_rgbi(
    members,
    market,
    divisor,
):

    rows = []

    total_contribution = 0.0

    for _, member in members.iterrows():

        contract = clean_text(
            member["contract"]
        )

        market_row = get_market_row(
            market,
            contract,
        )

        if market_row is None:

            mtm = None
            duration_days = None
            yield_value = None

        else:

            mtm = (
                market_row["mtm"]
            )

            duration_days = (
                market_row[
                    "duration"
                ]
            )

            yield_value = (
                market_row["yield"]
            )

        n_shares = to_float(
            member["n_shares"]
        )

        ff = to_float(
            member["ff"]
        )

        w = to_float(
            member["w"]
        )

        # ====================================================
        # CONTRIBUTION
        # ====================================================

        contribution = None

        if (
            mtm is not None
            and n_shares is not None
            and ff is not None
            and w is not None
        ):

            contribution = (
                mtm
                * n_shares
                * ff
                * w
            )

            total_contribution += (
                contribution
            )

        # ====================================================
        # DURATION
        #
        # md_snap_moex duration is DAYS.
        #
        # Convert to YEARS.
        # ====================================================

        duration_years = None

        if duration_days is not None:

            duration_years = (
                duration_days
                / DAYS_IN_YEAR
            )

        rows.append(
            {
                "contract": contract,

                "mtm": mtm,

                "duration": (
                    duration_years
                ),

                "yield": (
                    yield_value
                ),

                "contribution": (
                    contribution
                ),
            }
        )

    members_live = pd.DataFrame(
        rows
    )

    # ========================================================
    # ACTUAL INDEX WEIGHTS
    # ========================================================

    if (
        not members_live.empty
        and total_contribution != 0
    ):

        members_live["weight"] = (
            members_live[
                "contribution"
            ]
            / total_contribution
        )

    else:

        members_live["weight"] = None

    # ========================================================
    # RGBI VALUE
    # ========================================================

    calculated = None

    if (
        divisor is not None
        and divisor != 0
        and total_contribution != 0
    ):

        calculated = (
            total_contribution
            / divisor
        )

    # ========================================================
    # WEIGHTED DURATION
    # ========================================================

    weighted_duration = None

    if not members_live.empty:

        valid = members_live[
            members_live["weight"].notna()
            & members_live[
                "duration"
            ].notna()
        ]

        if not valid.empty:

            valid_weight = (
                valid["weight"].sum()
            )

            if valid_weight != 0:

                weighted_duration = (
                    (
                        valid["weight"]
                        * valid["duration"]
                    ).sum()
                    / valid_weight
                )

    # ========================================================
    # WEIGHTED YIELD
    # ========================================================

    weighted_yield = None

    if not members_live.empty:

        valid = members_live[
            members_live["weight"].notna()
            & members_live[
                "yield"
            ].notna()
        ]

        if not valid.empty:

            valid_weight = (
                valid["weight"].sum()
            )

            if valid_weight != 0:

                weighted_yield = (
                    (
                        valid["weight"]
                        * valid["yield"]
                    ).sum()
                    / valid_weight
                )

    return (
        calculated,
        weighted_yield,
        weighted_duration,
        members_live,
    )


# ============================================================
# FORMATTING
# ============================================================

def fmt_index(value):

    if (
        value is None
        or pd.isna(value)
    ):
        return ""

    return f"{value:,.2f}"


def fmt_diff(value):

    if (
        value is None
        or pd.isna(value)
    ):
        return ""

    return f"{value * 100:.2f}%"


def fmt_weight(value):

    if (
        value is None
        or pd.isna(value)
    ):
        return ""

    return f"{value * 100:.1f}%"


def fmt_equity_mtm(value):

    if (
        value is None
        or pd.isna(value)
    ):
        return ""

    return f"{value:,.2f}"


def fmt_bond_mtm(value):

    if (
        value is None
        or pd.isna(value)
    ):
        return ""

    return f"{value:,.3f}"


def fmt_duration(value):

    if (
        value is None
        or pd.isna(value)
    ):
        return ""

    return f"{value:.2f}"


def fmt_yield(value):

    if (
        value is None
        or pd.isna(value)
    ):
        return ""

    # --------------------------------------------------------
    # Supports:
    #
    # 0.154  -> 15.40%
    # 15.4   -> 15.40%
    # --------------------------------------------------------

    if abs(value) <= 1:
        value *= 100

    return f"{value:.2f}%"


# ============================================================
# CASH INDEX TABLE
# ============================================================

def build_cash_index_table(
    imoex_calc,
    rtsi_calc,
    rgbi_calc,
    rgbi_yield,
    rgbi_duration,
    market,
):

    calculations = {
        "IMOEX2": imoex_calc,
        "RTSI": rtsi_calc,
        "RGBI": rgbi_calc,
    }

    rows = []

    for contract in INDEX_LIST:

        calculated = (
            calculations[
                contract
            ]
        )

        official = get_mtm(
            market,
            contract,
        )

        diff = None

        if (
            calculated is not None
            and official is not None
            and official != 0
        ):

            diff = (
                calculated
                / official
                - 1.0
            )

        rows.append(
            {
                "contract": contract,

                "calculated": (
                    fmt_index(
                        calculated
                    )
                ),

                "official": (
                    fmt_index(
                        official
                    )
                ),

                "diff_%": (
                    fmt_diff(
                        diff
                    )
                ),

                "yield": (
                    fmt_yield(
                        rgbi_yield
                    )
                    if contract == "RGBI"
                    else ""
                ),

                "dur": (
                    fmt_duration(
                        rgbi_duration
                    )
                    if contract == "RGBI"
                    else ""
                ),
            }
        )

    return pd.DataFrame(
        rows
    )


# ============================================================
# IMOEX MEMBER TABLE
# ============================================================

def build_imoex_members_table(
    members_live,
):

    if members_live.empty:

        return pd.DataFrame(
            columns=[
                "contract",
                "weight",
                "mtm",
            ]
        )

    display = (
        members_live
        .copy()
        .sort_values(
            "weight",
            ascending=False,
            na_position="last",
        )
    )

    return pd.DataFrame(
        {
            "contract": (
                display["contract"]
            ),

            "weight": (
                display["weight"]
                .apply(
                    fmt_weight
                )
            ),

            "mtm": (
                display["mtm"]
                .apply(
                    fmt_equity_mtm
                )
            ),
        }
    )


# ============================================================
# RGBI MEMBER TABLE
# ============================================================

def build_rgbi_members_table(
    members_live,
):

    if members_live.empty:

        return pd.DataFrame(
            columns=[
                "contract",
                "weight",
                "mtm",
                "duration",
                "yield",
            ]
        )

    display = (
        members_live
        .copy()
        .sort_values(
            "weight",
            ascending=False,
            na_position="last",
        )
    )

    return pd.DataFrame(
        {
            "contract": (
                display["contract"]
            ),

            "weight": (
                display["weight"]
                .apply(
                    fmt_weight
                )
            ),

            "mtm": (
                display["mtm"]
                .apply(
                    fmt_bond_mtm
                )
            ),

            "duration": (
                display["duration"]
                .apply(
                    fmt_duration
                )
            ),

            "yield": (
                display["yield"]
                .apply(
                    fmt_yield
                )
            ),
        }
    )


# ============================================================
# TABLE STYLE
# ============================================================

def style_table(
    df,
    bold_columns=None,
):

    if bold_columns is None:
        bold_columns = []

    styler = (
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
    )

    for column in bold_columns:

        if column in df.columns:

            styler = (
                styler
                .set_properties(
                    subset=[
                        column
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

def render_index_arb_page():

    # ========================================================
    # REFERENCE DATA
    # ========================================================

    reference = (
        load_index_reference_data()
    )

    imoex_members = (
        reference["imoex"]
    )

    rgbi_members = (
        reference["rgbi"]
    )

    divisors = (
        reference["divisors"]
    )

    # ========================================================
    # LIVE DATA
    # ========================================================

    (
        market,
        usd_cbr,
    ) = load_live_index_data(
        reference
    )

    # ========================================================
    # DIVISORS
    # ========================================================

    imoex_divisor = (
        get_divisor(
            divisors,
            "IMOEX",
        )
    )

    rtsi_divisor = (
        get_divisor(
            divisors,
            "RTSI",
        )
    )

    rgbi_divisor = (
        get_divisor(
            divisors,
            "RGBI",
        )
    )

    # ========================================================
    # IMOEX
    # ========================================================

    (
        imoex_calc,
        imoex_rub_basket,
        imoex_members_live,
    ) = calculate_imoex(
        imoex_members,
        market,
        imoex_divisor,
    )

    # ========================================================
    # RTSI
    #
    # Same basket as IMOEX.
    #
    # RTSI =
    #
    # RUB basket
    # ------------------
    # USD CBR × divisor
    # ========================================================

    rtsi_calc = None

    if (
        imoex_rub_basket is not None
        and imoex_rub_basket != 0
        and usd_cbr is not None
        and usd_cbr != 0
        and rtsi_divisor is not None
        and rtsi_divisor != 0
    ):

        rtsi_calc = (
            imoex_rub_basket
            / usd_cbr
            / rtsi_divisor
        )

    # ========================================================
    # RGBI
    # ========================================================

    (
        rgbi_calc,
        rgbi_yield,
        rgbi_duration,
        rgbi_members_live,
    ) = calculate_rgbi(
        rgbi_members,
        market,
        rgbi_divisor,
    )

    # ========================================================
    # SECTION 1
    # CASH INDEX
    # ========================================================

    st.subheader(
        "Cash Index"
    )

    cash_table = (
        build_cash_index_table(
            imoex_calc=imoex_calc,
            rtsi_calc=rtsi_calc,
            rgbi_calc=rgbi_calc,
            rgbi_yield=rgbi_yield,
            rgbi_duration=rgbi_duration,
            market=market,
        )
    )

    st.dataframe(
        style_table(
            cash_table,

            bold_columns=[
                "contract",
                "calculated",
            ],
        ),

        hide_index=True,

        use_container_width=False,

        width=650,

        height=(
            38
            + len(cash_table)
            * 35
        ),
    )

    # ========================================================
    # SECTION 2
    # INDEX FUTURES FAIR VALUES
    # ========================================================

    st.divider()

    st.subheader(
        "Index Futures Fair Values"
    )

    st.caption(
        "To be added."
    )

    # ========================================================
    # SECTION 3
    # MIX - RTS BOX ARBITRAGE
    # ========================================================

    st.divider()

    st.subheader(
        "MIX - RTS Box Arbitrage"
    )

    st.caption(
        "To be added."
    )

    # ========================================================
    # SECTION 4
    # INDEX MEMBERS
    # ========================================================

    st.divider()

    st.subheader(
        "Index Members"
    )

    # ========================================================
    # IMOEX
    # ========================================================

    st.markdown(
        "**iMOEX**"
    )

    imoex_display = (
        build_imoex_members_table(
            imoex_members_live
        )
    )

    st.dataframe(
        style_table(
            imoex_display,

            bold_columns=[
                "contract",
                "weight",
            ],
        ),

        hide_index=True,

        use_container_width=False,

        width=420,

        height=min(
            38
            + len(imoex_display)
            * 35,

            600,
        ),
    )

    # ========================================================
    # RGBI
    # ========================================================

    st.markdown(
        "**RGBI**"
    )

    rgbi_display = (
        build_rgbi_members_table(
            rgbi_members_live
        )
    )

    st.dataframe(
        style_table(
            rgbi_display,

            bold_columns=[
                "contract",
                "weight",
            ],
        ),

        hide_index=True,

        use_container_width=False,

        width=650,

        height=min(
            38
            + len(rgbi_display)
            * 35,

            600,
        ),
    )