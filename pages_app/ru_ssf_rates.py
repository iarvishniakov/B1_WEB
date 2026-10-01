import datetime as dt

import numpy as np
import pandas as pd
import streamlit as st

from db import get_conn
from market_data_service import load_market_table as load_effective_market_table


# ============================================================
# SETTINGS
# ============================================================

DIVIDEND_TAX_RATE = 0.15
DAYS_IN_YEAR = 365.0

MARKET_DATA_TABLES = [
    "md_snap",
    "md_snap_bb",
    "md_snap_moex",
    "md_snap_hl",
    "b1_price_mapping",
]


# ============================================================
# HELPERS
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


def market_contract(code):
    """
    Convert SSF / MOEX code to market-data contract.

    Example:
        LKOH  -> MX:LKOH
        LKZ6  -> MX:LKZ6
    """

    code = clean_text(code)

    if not code:
        return ""

    if ":" in code:
        return code

    return f"MX:{code}"


# ============================================================
# VALUE DATE
#
# User convention:
#
# Monday-Thursday -> today + 1 calendar day
# Friday          -> today + 3 calendar days
#
# Since the application is run on business days, weekend
# handling is not required for normal operation.
# ============================================================

def calculate_value_date(today):

    weekday = today.weekday()

    # Friday
    if weekday == 4:
        return today + dt.timedelta(days=3)

    return today + dt.timedelta(days=1)


# ============================================================
# RUB CURVE INTERPOLATION / EXTRAPOLATION
#
# Linear interpolation between nodes.
# Linear extrapolation outside the available range.
#
# b1_curve_rub:
#     ndays
#     rate
#
# rate is expected as decimal:
#     0.15 = 15%
# ============================================================

def interpolate_rate(
    curve_df,
    ndays,
):

    if ndays is None:
        return None

    if pd.isna(ndays):
        return None

    if curve_df.empty:
        return None

    curve = (
        curve_df[
            ["ndays", "rate"]
        ]
        .dropna()
        .sort_values("ndays")
        .drop_duplicates(
            subset=["ndays"],
            keep="last",
        )
        .reset_index(drop=True)
    )

    if curve.empty:
        return None

    x = curve["ndays"].to_numpy(
        dtype=float
    )

    y = curve["rate"].to_numpy(
        dtype=float
    )

    target = float(ndays)

    # --------------------------------------------------------
    # Only one curve point
    # --------------------------------------------------------

    if len(x) == 1:
        return float(y[0])

    # --------------------------------------------------------
    # Left extrapolation
    # --------------------------------------------------------

    if target <= x[0]:

        x1 = x[0]
        x2 = x[1]

        y1 = y[0]
        y2 = y[1]

        if x2 == x1:
            return float(y1)

        return float(
            y1
            + (target - x1)
            * (y2 - y1)
            / (x2 - x1)
        )

    # --------------------------------------------------------
    # Right extrapolation
    # --------------------------------------------------------

    if target >= x[-1]:

        x1 = x[-2]
        x2 = x[-1]

        y1 = y[-2]
        y2 = y[-1]

        if x2 == x1:
            return float(y2)

        return float(
            y1
            + (target - x1)
            * (y2 - y1)
            / (x2 - x1)
        )

    # --------------------------------------------------------
    # Interpolation
    # --------------------------------------------------------

    return float(
        np.interp(
            target,
            x,
            y,
        )
    )


# ============================================================
# LOAD ALL REFERENCE DATA
#
# CACHED ONCE PER APPLICATION PROCESS.
#
# This includes:
#
# - SSF definitions
# - RUB curve
# - dividends
# - MOEX static data
# - today's value date
# - expiration information
# - lots
# - dividend calculations
#
# ONLY live market prices are NOT cached.
# ============================================================

@st.cache_resource
def load_ssf_reference_data():

    conn = get_conn()

    # ========================================================
    # TODAY / VALUE DATE
    # ========================================================

    today = dt.date.today()

    value_date = calculate_value_date(
        today
    )

    # ========================================================
    # 1. SSF INPUTS
    # ========================================================

    sql_inputs = """
        SELECT
            und_moex,
            und_fut,
            fut_front,
            fut_back
        FROM public.b1_ssf_inputs
    """

    with conn.cursor() as cur:

        cur.execute(
            sql_inputs
        )

        rows = cur.fetchall()

        inputs = pd.DataFrame(
            rows,
            columns=[
                "und_moex",
                "und_fut",
                "fut_front",
                "fut_back",
            ],
        )

    for col in [
        "und_moex",
        "und_fut",
        "fut_front",
        "fut_back",
    ]:

        inputs[col] = (
            inputs[col]
            .fillna("")
            .astype(str)
            .str.strip()
        )

    # ========================================================
    # 2. RUB CURVE
    # ========================================================

    sql_curve = """
        SELECT
            ndays,
            rate
        FROM public.b1_curve_rub
        ORDER BY ndays
    """

    with conn.cursor() as cur:

        cur.execute(
            sql_curve
        )

        rows = cur.fetchall()

        rub_curve = pd.DataFrame(
            rows,
            columns=[
                "ndays",
                "rate",
            ],
        )

    rub_curve["ndays"] = pd.to_numeric(
        rub_curve["ndays"],
        errors="coerce",
    )

    rub_curve["rate"] = pd.to_numeric(
        rub_curve["rate"],
        errors="coerce",
    )

    rub_curve = (
        rub_curve
        .dropna(
            subset=[
                "ndays",
                "rate",
            ]
        )
        .sort_values("ndays")
        .reset_index(drop=True)
    )

    # ========================================================
    # 3. DIVIDENDS
    # ========================================================

    sql_divs = """
        SELECT
            contract,
            dividend,
            rec_date
        FROM public.b1_divs_imoex
    """

    with conn.cursor() as cur:

        cur.execute(
            sql_divs
        )

        rows = cur.fetchall()

        dividends = pd.DataFrame(
            rows,
            columns=[
                "contract",
                "dividend",
                "rec_date",
            ],
        )

    dividends["contract"] = (
        dividends["contract"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    dividends["dividend"] = pd.to_numeric(
        dividends["dividend"],
        errors="coerce",
    )

    dividends["rec_date"] = pd.to_datetime(
        dividends["rec_date"],
        errors="coerce",
    )

    # ========================================================
    # 4. STATIC DATA
    #
    # We only need the fields required for this calculation.
    # ========================================================

    sql_static = """
        SELECT
            instrument_code,
            lot,
            expiration
        FROM public.b1_static_data_moex
    """

    with conn.cursor() as cur:

        cur.execute(
            sql_static
        )

        rows = cur.fetchall()

        static_data = pd.DataFrame(
            rows,
            columns=[
                "instrument_code",
                "lot",
                "expiration",
            ],
        )

    static_data["instrument_code"] = (
        static_data["instrument_code"]
        .fillna("")
        .astype(str)
        .str.strip()
    )

    static_data["lot"] = pd.to_numeric(
        static_data["lot"],
        errors="coerce",
    )

    static_data["expiration"] = pd.to_datetime(
        static_data["expiration"],
        errors="coerce",
    )

    # ========================================================
    # STATIC LOOKUP
    # ========================================================

    static_lookup = {}

    for _, row in static_data.iterrows():

        code = row[
            "instrument_code"
        ]

        if not code:
            continue

        static_lookup[code] = {
            "lot": to_float(
                row["lot"]
            ),
            "expiration": (
                row["expiration"]
            ),
        }

    # ========================================================
    # PREPARE EACH SSF ROW
    # ========================================================

    prepared_rows = []

    for _, row in inputs.iterrows():

        und_moex = row[
            "und_moex"
        ]

        und_fut = row[
            "und_fut"
        ]

        fut_front = row[
            "fut_front"
        ]

        fut_back = row[
            "fut_back"
        ]

        front_static = (
            static_lookup.get(
                fut_front
            )
        )

        back_static = (
            static_lookup.get(
                fut_back
            )
        )

        # ----------------------------------------------------
        # Skip row if static futures information is unavailable
        # ----------------------------------------------------

        if (
            front_static is None
            or back_static is None
        ):

            continue

        front_lot = (
            front_static["lot"]
        )

        back_lot = (
            back_static["lot"]
        )

        front_expiration = (
            front_static[
                "expiration"
            ]
        )

        back_expiration = (
            back_static[
                "expiration"
            ]
        )

        if (
            pd.isna(front_expiration)
            or pd.isna(back_expiration)
        ):

            continue

        # ----------------------------------------------------
        # Original MOEX expiration
        # ----------------------------------------------------

        front_expiration = (
            pd.Timestamp(
                front_expiration
            )
            .normalize()
        )

        back_expiration = (
            pd.Timestamp(
                back_expiration
            )
            .normalize()
        )

        # ----------------------------------------------------
        # TRUE futures expiration = expiration + 4 days
        # ----------------------------------------------------

        front_true_exp = (
            front_expiration
            + pd.Timedelta(days=4)
        )

        back_true_exp = (
            back_expiration
            + pd.Timedelta(days=4)
        )

        # ----------------------------------------------------
        # Number of days from VALUE DATE
        # ----------------------------------------------------

        value_ts = pd.Timestamp(
            value_date
        )

        front_ndays = (
            front_true_exp
            - value_ts
        ).days

        back_ndays = (
            back_true_exp
            - value_ts
        ).days

        # ----------------------------------------------------
        # RUB rates for futures maturities
        # ----------------------------------------------------

        front_rub_rate = (
            interpolate_rate(
                rub_curve,
                front_ndays,
            )
        )

        back_rub_rate = (
            interpolate_rate(
                rub_curve,
                back_ndays,
            )
        )

        # ====================================================
        # DIVIDENDS
        #
        # Inclusion rule:
        #
        # today < record date <= original expiration + 1 day
        #
        # Tax:
        #
        # net = gross * 0.85
        #
        # FV is calculated to TRUE expiration using the RUB
        # curve and simple interest.
        # ====================================================

        und_divs = dividends[
            dividends["contract"]
            .eq(und_moex)
        ].copy()

        # ----------------------------------------------------
        # FRONT dividends
        # ----------------------------------------------------

        front_div_limit = (
            front_expiration
            + pd.Timedelta(days=1)
        )

        front_divs = und_divs[
            (
                und_divs["rec_date"]
                > pd.Timestamp(today)
            )
            &
            (
                und_divs["rec_date"]
                <= front_div_limit
            )
        ].copy()

        front_div_gross = 0.0
        front_div_fv = 0.0

        for _, div_row in front_divs.iterrows():

            gross = to_float(
                div_row["dividend"]
            )

            if gross is None:
                continue

            rec_date = (
                pd.Timestamp(
                    div_row["rec_date"]
                )
                .normalize()
            )

            net = (
                gross
                * (
                    1.0
                    - DIVIDEND_TAX_RATE
                )
            )

            div_days = (
                front_true_exp
                - rec_date
            ).days

            div_days = max(
                div_days,
                0,
            )

            div_rate = (
                interpolate_rate(
                    rub_curve,
                    div_days,
                )
            )

            if div_rate is None:
                div_rate = 0.0

            future_value = (
                net
                * (
                    1.0
                    + div_rate
                    * div_days
                    / DAYS_IN_YEAR
                )
            )

            front_div_gross += (
                gross
            )

            front_div_fv += (
                future_value
            )

        # ----------------------------------------------------
        # BACK dividends
        # ----------------------------------------------------

        back_div_limit = (
            back_expiration
            + pd.Timedelta(days=1)
        )

        back_divs = und_divs[
            (
                und_divs["rec_date"]
                > pd.Timestamp(today)
            )
            &
            (
                und_divs["rec_date"]
                <= back_div_limit
            )
        ].copy()

        back_div_gross = 0.0
        back_div_fv = 0.0

        for _, div_row in back_divs.iterrows():

            gross = to_float(
                div_row["dividend"]
            )

            if gross is None:
                continue

            rec_date = (
                pd.Timestamp(
                    div_row["rec_date"]
                )
                .normalize()
            )

            net = (
                gross
                * (
                    1.0
                    - DIVIDEND_TAX_RATE
                )
            )

            div_days = (
                back_true_exp
                - rec_date
            ).days

            div_days = max(
                div_days,
                0,
            )

            div_rate = (
                interpolate_rate(
                    rub_curve,
                    div_days,
                )
            )

            if div_rate is None:
                div_rate = 0.0

            future_value = (
                net
                * (
                    1.0
                    + div_rate
                    * div_days
                    / DAYS_IN_YEAR
                )
            )

            back_div_gross += (
                gross
            )

            back_div_fv += (
                future_value
            )

        # ====================================================
        # PREPARED ROW
        # ====================================================

        prepared_rows.append(
            {
                "und_moex": und_moex,
                "und_fut": und_fut,

                "fut_front": fut_front,
                "fut_back": fut_back,

                "front_lot": front_lot,
                "back_lot": back_lot,

                "front_expiration": (
                    front_expiration
                ),

                "back_expiration": (
                    back_expiration
                ),

                "front_true_expiration": (
                    front_true_exp
                ),

                "back_true_expiration": (
                    back_true_exp
                ),

                "front_ndays": (
                    front_ndays
                ),

                "back_ndays": (
                    back_ndays
                ),

                "front_rub_rate": (
                    front_rub_rate
                ),

                "back_rub_rate": (
                    back_rub_rate
                ),

                "front_div_gross": (
                    front_div_gross
                ),

                "back_div_gross": (
                    back_div_gross
                ),

                "front_div_fv": (
                    front_div_fv
                ),

                "back_div_fv": (
                    back_div_fv
                ),

                # --------------------------------------------
                # Live market-data contracts
                # --------------------------------------------

                "spot_md_contract": (
                    market_contract(
                        und_moex
                    )
                ),

                "front_md_contract": (
                    market_contract(
                        fut_front
                    )
                ),

                "back_md_contract": (
                    market_contract(
                        fut_back
                    )
                ),
            }
        )

    prepared = pd.DataFrame(
        prepared_rows
    )

    return {
        "today": today,
        "value_date": value_date,
        "inputs": inputs,
        "rub_curve": rub_curve,
        "dividends": dividends,
        "static_data": static_data,
        "prepared": prepared,
    }


# ============================================================
# MARKET DATA TABLE COLUMNS
#
# Cached because schemas do not change during the session.
# ============================================================

@st.cache_resource
def get_market_data_columns():

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
            (
                MARKET_DATA_TABLES,
            ),
        )

        rows = cur.fetchall()

    result = {}

    for (
        table_name,
        column_name,
    ) in rows:

        if table_name not in result:
            result[table_name] = set()

        result[
            table_name
        ].add(
            column_name
        )

    return result


# ============================================================
# LOAD LIVE PRICES
#
# THIS FUNCTION IS NOT CACHED.
#
# It is called every global Streamlit refresh.
#
# Only:
#
# - spot
# - front futures
# - back futures
#
# are refreshed.
# ============================================================

def load_live_ssf_prices(
    prepared,
):
    if prepared.empty:
        return {}
    required_contracts = set()
    for col in ["spot_md_contract", "front_md_contract", "back_md_contract"]:
        required_contracts.update(prepared[col].dropna().astype(str).str.strip().tolist())
    required_contracts.discard("")
    prices = {}
    for table_name in MARKET_DATA_TABLES:
        df = load_effective_market_table(table_name, required_contracts)
        if df.empty or not {"contract", "bid", "ask"}.issubset(df.columns):
            continue
        for _, row in df.iterrows():
            bid = to_float(row.get("bid")); ask = to_float(row.get("ask"))
            if bid is not None and ask is not None:
                mtm = (bid + ask) / 2.0
            elif bid is not None:
                mtm = bid
            elif ask is not None:
                mtm = ask
            else:
                mtm = None
            prices[clean_text(row.get("contract"))] = {"bid": bid, "ask": ask, "mtm": mtm}
    return prices


# ============================================================
# GET MTM
# ============================================================

def get_mtm(
    prices,
    contract,
):

    data = prices.get(
        clean_text(
            contract
        )
    )

    if data is None:
        return None

    return data[
        "mtm"
    ]


# ============================================================
# CALCULATE IMPLIED RATE
#
# We normalize the futures price to one share:
#
#       F_unit = F_mtm / lot
#
# Pricing convention:
#
#       F_unit = S * (1 + rT) - FV(dividends)
#
# therefore:
#
#       r =
#       (F_unit + FV(dividends) - S)
#       --------------------------------
#                  S * T
#
# ============================================================

def calculate_implied_rate(
    future_mtm,
    spot_mtm,
    lot,
    ndays,
    dividend_fv,
):

    if (
        future_mtm is None
        or spot_mtm is None
        or lot is None
        or ndays is None
    ):
        return None

    if (
        lot == 0
        or spot_mtm == 0
        or ndays <= 0
    ):
        return None

    future_unit = (
        future_mtm
        / lot
    )

    t = (
        ndays
        / DAYS_IN_YEAR
    )

    return (
        future_unit
        + dividend_fv
        - spot_mtm
    ) / (
        spot_mtm
        * t
    )


# ============================================================
# CALCULATE LIVE TABLE
# ============================================================

def calculate_ssf_table(
    prepared,
    prices,
):

    output = []

    for _, row in prepared.iterrows():

        # ====================================================
        # LIVE PRICES
        # ====================================================

        spot_mtm = get_mtm(
            prices,
            row[
                "spot_md_contract"
            ],
        )

        front_fut_mtm = get_mtm(
            prices,
            row[
                "front_md_contract"
            ],
        )

        back_fut_mtm = get_mtm(
            prices,
            row[
                "back_md_contract"
            ],
        )

        front_lot = to_float(
            row[
                "front_lot"
            ]
        )

        back_lot = to_float(
            row[
                "back_lot"
            ]
        )

        # ====================================================
        # FRONT BASIS
        #
        # Futures MTM - Spot MTM * futures lot
        # ====================================================

        front_basis = None

        if (
            front_fut_mtm is not None
            and spot_mtm is not None
            and front_lot is not None
        ):

            front_basis = (
                front_fut_mtm
                - spot_mtm
                * front_lot
            )

        # ====================================================
        # BACK BASIS
        # ====================================================

        back_basis = None

        if (
            back_fut_mtm is not None
            and spot_mtm is not None
            and back_lot is not None
        ):

            back_basis = (
                back_fut_mtm
                - spot_mtm
                * back_lot
            )

        # ====================================================
        # FRONT IMPLIED RATE
        # ====================================================

        front_rate = (
            calculate_implied_rate(
                future_mtm=(
                    front_fut_mtm
                ),

                spot_mtm=(
                    spot_mtm
                ),

                lot=(
                    front_lot
                ),

                ndays=(
                    row[
                        "front_ndays"
                    ]
                ),

                dividend_fv=(
                    row[
                        "front_div_fv"
                    ]
                ),
            )
        )

        # ====================================================
        # BACK IMPLIED RATE
        # ====================================================

        back_rate = (
            calculate_implied_rate(
                future_mtm=(
                    back_fut_mtm
                ),

                spot_mtm=(
                    spot_mtm
                ),

                lot=(
                    back_lot
                ),

                ndays=(
                    row[
                        "back_ndays"
                    ]
                ),

                dividend_fv=(
                    row[
                        "back_div_fv"
                    ]
                ),
            )
        )

        # ====================================================
        # ROLL BASIS
        #
        # Back basis - Front basis
        # ====================================================

        roll_basis = None

        if (
            front_basis is not None
            and back_basis is not None
        ):

            roll_basis = (
                back_basis
                - front_basis
            )

        # ====================================================
        # ROLL IMPLIED RATE
        #
        # Derive forward simple rate from front/back implied
        # rates:
        #
        # (1 + rB*TB)
        # -------------  - 1
        # (1 + rF*TF)
        #
        # divided by:
        #
        # TB - TF
        # ====================================================

        roll_rate = None

        if (
            front_rate is not None
            and back_rate is not None
        ):

            front_t = (
                row[
                    "front_ndays"
                ]
                / DAYS_IN_YEAR
            )

            back_t = (
                row[
                    "back_ndays"
                ]
                / DAYS_IN_YEAR
            )

            roll_t = (
                back_t
                - front_t
            )

            denominator = (
                1.0
                + front_rate
                * front_t
            )

            if (
                roll_t > 0
                and denominator != 0
            ):

                roll_rate = (
                    (
                        (
                            1.0
                            + back_rate
                            * back_t
                        )
                        / denominator
                    )
                    - 1.0
                ) / roll_t

        # ====================================================
        # OUTPUT
        # ====================================================

        output.append(
            {
                "contract": (
                    row[
                        "und_moex"
                    ]
                ),

                # --------------------------------------------
                # FRONT
                # --------------------------------------------

                "front_rate": (
                    front_rate
                ),

                "front_basis": (
                    front_basis
                ),

                "front_futures_mtm": (
                    front_fut_mtm
                ),

                "front_spot_mtm": (
                    spot_mtm
                ),

                "front_ndays": (
                    row[
                        "front_ndays"
                    ]
                ),

                "front_div_gross": (
                    row[
                        "front_div_gross"
                    ]
                ),

                # --------------------------------------------
                # BACK
                # --------------------------------------------

                "back_rate": (
                    back_rate
                ),

                "back_basis": (
                    back_basis
                ),

                "back_futures_mtm": (
                    back_fut_mtm
                ),

                "back_spot_mtm": (
                    spot_mtm
                ),

                "back_ndays": (
                    row[
                        "back_ndays"
                    ]
                ),

                "back_div_gross": (
                    row[
                        "back_div_gross"
                    ]
                ),

                # --------------------------------------------
                # ROLL
                # --------------------------------------------

                "roll_rate": (
                    roll_rate
                ),

                "roll_basis": (
                    roll_basis
                ),
            }
        )

    return pd.DataFrame(
        output
    )


# ============================================================
# FORMATTING
# ============================================================

def fmt_rate(value):

    if (
        value is None
        or pd.isna(value)
    ):
        return ""

    return (
        f"{float(value) * 100:.1f}%"
    )


def fmt_basis(value):

    if (
        value is None
        or pd.isna(value)
    ):
        return ""

    return (
        f"{float(value):,.0f}"
    )


def fmt_price(value):

    if (
        value is None
        or pd.isna(value)
    ):
        return ""

    return (
        f"{float(value):,.2f}"
    )


def fmt_days(value):

    if (
        value is None
        or pd.isna(value)
    ):
        return ""

    return str(
        int(value)
    )


def fmt_dividend(value):

    if (
        value is None
        or pd.isna(value)
    ):
        return ""

    return (
        f"{float(value):,.2f}"
    )


# ============================================================
# TABLE STYLING
# ============================================================

def style_ssf_table(
    df,
):

    styler = (
        df.style

        .set_properties(
            **{
                "text-align": "center",
            }
        )

        .set_properties(
            subset=[
                "Contract"
            ],
            **{
                "font-weight": "bold",
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

    return styler


# ============================================================
# COMPACT TABLE
#
# Contract | FRONT        | BACK         | ROLL
#          | rate | basis | rate | basis | rate | basis
#
# MultiIndex columns give us the grouped headers.
# ============================================================

def build_compact_table(
    results,
):

    rows = []

    for _, row in results.iterrows():

        rows.append(
            [
                row["contract"],

                fmt_rate(
                    row[
                        "front_rate"
                    ]
                ),

                fmt_basis(
                    row[
                        "front_basis"
                    ]
                ),

                fmt_rate(
                    row[
                        "back_rate"
                    ]
                ),

                fmt_basis(
                    row[
                        "back_basis"
                    ]
                ),

                fmt_rate(
                    row[
                        "roll_rate"
                    ]
                ),

                fmt_basis(
                    row[
                        "roll_basis"
                    ]
                ),
            ]
        )

    columns = pd.MultiIndex.from_tuples(
        [
            (
                "",
                "Contract",
            ),

            (
                "Front",
                "rate",
            ),

            (
                "Front",
                "basis",
            ),

            (
                "Back",
                "rate",
            ),

            (
                "Back",
                "basis",
            ),

            (
                "Roll",
                "rate",
            ),

            (
                "Roll",
                "basis",
            ),
        ]
    )

    return pd.DataFrame(
        rows,
        columns=columns,
    )


# ============================================================
# EXPANDED TABLE
# ============================================================

def build_expanded_table(
    results,
):

    rows = []

    for _, row in results.iterrows():

        rows.append(
            [
                row["contract"],

                # FRONT
                fmt_rate(
                    row["front_rate"]
                ),

                fmt_basis(
                    row["front_basis"]
                ),

                fmt_price(
                    row[
                        "front_futures_mtm"
                    ]
                ),

                fmt_price(
                    row[
                        "front_spot_mtm"
                    ]
                ),

                fmt_days(
                    row[
                        "front_ndays"
                    ]
                ),

                fmt_dividend(
                    row[
                        "front_div_gross"
                    ]
                ),

                # BACK
                fmt_rate(
                    row["back_rate"]
                ),

                fmt_basis(
                    row["back_basis"]
                ),

                fmt_price(
                    row[
                        "back_futures_mtm"
                    ]
                ),

                fmt_price(
                    row[
                        "back_spot_mtm"
                    ]
                ),

                fmt_days(
                    row[
                        "back_ndays"
                    ]
                ),

                fmt_dividend(
                    row[
                        "back_div_gross"
                    ]
                ),

                # ROLL
                fmt_rate(
                    row["roll_rate"]
                ),

                fmt_basis(
                    row["roll_basis"]
                ),
            ]
        )

    columns = pd.MultiIndex.from_tuples(
        [
            (
                "",
                "Contract",
            ),

            # FRONT
            (
                "Front",
                "rate",
            ),

            (
                "Front",
                "basis",
            ),

            (
                "Front",
                "futures mtm",
            ),

            (
                "Front",
                "spot mtm",
            ),

            (
                "Front",
                "ndays",
            ),

            (
                "Front",
                "div, gross",
            ),

            # BACK
            (
                "Back",
                "rate",
            ),

            (
                "Back",
                "basis",
            ),

            (
                "Back",
                "futures mtm",
            ),

            (
                "Back",
                "spot mtm",
            ),

            (
                "Back",
                "ndays",
            ),

            (
                "Back",
                "div, gross",
            ),

            # ROLL
            (
                "Roll",
                "rate",
            ),

            (
                "Roll",
                "basis",
            ),
        ]
    )

    return pd.DataFrame(
        rows,
        columns=columns,
    )


# ============================================================
# PAGE
# ============================================================

def render_ru_ssf_rates_page():

    # ========================================================
    # STATIC / DAILY DATA
    #
    # Loaded once when application starts.
    # ========================================================

    reference = (
        load_ssf_reference_data()
    )

    prepared = reference[
        "prepared"
    ]

    if prepared.empty:

        st.warning(
            "No Russian SSF reference data available."
        )

        return

    # ========================================================
    # LIVE PRICES
    #
    # Refreshed on every global refresh.
    # ========================================================

    prices = (
        load_live_ssf_prices(
            prepared
        )
    )

    # ========================================================
    # CALCULATE
    # ========================================================

    results = (
        calculate_ssf_table(
            prepared,
            prices,
        )
    )

    # ========================================================
    # EXPANDED / COMPACT
    #
    # Compact is default.
    # ========================================================

    expanded = st.toggle(
        "Expanded",
        value=False,
        key="ru_ssf_expanded",
    )

    # ========================================================
    # TABLE
    # ========================================================

    if expanded:

        display_df = (
            build_expanded_table(
                results
            )
        )

        table_width = 1700

    else:

        display_df = (
            build_compact_table(
                results
            )
        )

        table_width = 900

    # ========================================================
    # HEIGHT
    # ========================================================

    table_height = (
        70
        + len(display_df)
        * 35
    )

    # ========================================================
    # DISPLAY
    # ========================================================

    # ========================================================
    # BOLD CONTRACT + RATE COLUMNS
    # ========================================================

    styled_df = display_df.style.set_properties(
        **{
            "text-align": "center",
        }
    ).set_table_styles(
        [
            {
                "selector": "th",
                "props": [
                    ("text-align", "center"),
                ],
            }
        ]
    )

    # Contract
    styled_df = styled_df.set_properties(
        subset=[("", "Contract")],
        **{
            "font-weight": "bold",
        }
    )

    # Front / Back / Roll rates
    styled_df = styled_df.set_properties(
        subset=[
            ("Front", "rate"),
            ("Back", "rate"),
            ("Roll", "rate"),
        ],
        **{
            "font-weight": "bold",
        }
    )

    st.dataframe(
        styled_df,
        hide_index=True,
        use_container_width=False,
        width=table_width,
        height=table_height,
    )

    # ========================================================
    # DATE INFO
    # ========================================================

    st.caption(
        "Calculation date: "
        f"{reference['today'].strftime('%d-%b-%Y')} "
        " | "
        "Value date: "
        f"{reference['value_date'].strftime('%d-%b-%Y')}"
    )