import pandas as pd
import streamlit as st
import altair as alt

from db import get_conn


# ============================================================
# CURVE GROUP DEFINITIONS
#
# More groups can be added later:
#
# "Energy"
# "Crypto"
# "Rates"
# etc.
# ============================================================

CURVE_GROUPS = {
    "Precious Metals": {
        "GC": "b1_curve_gc",
        "SI": "b1_curve_si",
        "PL": "b1_curve_pl",
        "PA": "b1_curve_pa",
    },
}



# ============================================================
# CHART SIZE
# ============================================================

CURVE_CHART_WIDTH = 850
CURVE_CHART_HEIGHT = 860


# ============================================================
# MARKET DATA TABLES
# ============================================================

MARKET_DATA_TABLES = [
    "md_snap",
    "md_snap_bb",
    "md_snap_moex",
    "md_snap_hl",
    "b1_price_mapping",
]


# ============================================================
# FIXED CURVE COLOURS
#
# GC = Gold
# SI = Silver
# PL = Platinum
# PA = Palladium
# ============================================================

CURVE_COLOR_DOMAIN = [
    "GC",
    "SI",
    "PL",
    "PA",
]

CURVE_COLOR_RANGE = [
    "#C49A00",   # GC - dark yellow / gold
    "#4A4A4A",   # SI - dark grey
    "#8A8A8A",   # PL - lighter grey
    "#1976B9",   # PA - blue
]


# ============================================================
# LOAD CURVE DEFINITIONS
#
# These tables define which contracts belong to each curve.
# They are cached because they don't change every few seconds.
#
# If you change the curve definition tables in Supabase,
# restart Streamlit to force them to reload.
# ============================================================

@st.cache_resource
def load_curve_definitions():

    conn = get_conn()

    result = {}

    with conn.cursor() as cur:

        for group_name, curves in CURVE_GROUPS.items():

            result[group_name] = {}

            for curve_name, table_name in curves.items():

                sql = f"""
                    SELECT
                        contract,
                        type,
                        expiration
                    FROM public.{table_name}
                """

                cur.execute(sql)

                rows = cur.fetchall()

                df = pd.DataFrame(
                    rows,
                    columns=[
                        "contract",
                        "type",
                        "expiration",
                    ],
                )

                # --------------------------------------------
                # Clean contract
                # --------------------------------------------

                df["contract"] = (
                    df["contract"]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                )

                # --------------------------------------------
                # Clean type
                # --------------------------------------------

                df["type"] = (
                    df["type"]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                )

                # --------------------------------------------
                # Convert expiration to datetime
                # --------------------------------------------

                df["expiration"] = pd.to_datetime(
                    df["expiration"],
                    errors="coerce",
                )

                result[group_name][curve_name] = df

    return result


# ============================================================
# GET MARKET DATA TABLE STRUCTURE
#
# This lets us safely work with the different market-data
# tables even if their schemas are slightly different.
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
            (MARKET_DATA_TABLES,),
        )

        rows = cur.fetchall()

    result = {}

    for table_name, column_name in rows:

        if table_name not in result:
            result[table_name] = set()

        result[table_name].add(
            column_name
        )

    return result


# ============================================================
# GET ALL CONTRACTS REQUIRED BY ALL CURVES
# ============================================================

def get_required_contracts(
    curve_definitions,
):

    contracts = set()

    for group_data in curve_definitions.values():

        for df in group_data.values():

            for contract in df["contract"]:

                contract = str(
                    contract
                ).strip()

                if contract:
                    contracts.add(
                        contract
                    )

    return contracts


# ============================================================
# LOAD LIVE PRICES
#
# IMPORTANT:
#
# This function is deliberately NOT cached.
# Prices are therefore reloaded on every Streamlit refresh.
# ============================================================

def load_curve_prices(
    curve_definitions,
):

    conn = get_conn()

    required_contracts = (
        get_required_contracts(
            curve_definitions
        )
    )

    table_columns = (
        get_market_data_columns()
    )

    prices = {}

    with conn.cursor() as cur:

        for table_name in MARKET_DATA_TABLES:

            columns = table_columns.get(
                table_name,
                set(),
            )

            # --------------------------------------------
            # Required columns
            # --------------------------------------------

            if not {
                "contract",
                "bid",
                "ask",
            }.issubset(columns):

                continue

            # --------------------------------------------
            # Only get contracts actually needed
            # --------------------------------------------

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
                (
                    list(
                        required_contracts
                    ),
                ),
            )

            rows = cur.fetchall()

            # --------------------------------------------
            # Build price lookup
            # --------------------------------------------

            for contract, bid, ask in rows:

                bid = pd.to_numeric(
                    bid,
                    errors="coerce",
                )

                ask = pd.to_numeric(
                    ask,
                    errors="coerce",
                )

                # ----------------------------------------
                # Calculate mid
                # ----------------------------------------

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

                # ----------------------------------------
                # Contract is our lookup key.
                # ----------------------------------------

                prices[
                    str(contract).strip()
                ] = {
                    "bid": bid,
                    "ask": ask,
                    "mid": mid,
                }

    return prices


# ============================================================
# PRICE LOOKUP
# ============================================================

def get_price_data(
    prices,
    contract,
):

    contract = str(
        contract
    ).strip()

    return prices.get(
        contract
    )


# ============================================================
# CALCULATE ONE CURVE
#
# Simple-interest convention:
#
#       F = S * (1 + rT)
#
# therefore:
#
#       r = (F / S - 1) / T
#
# where:
#
#       T = days / 365
#
# therefore:
#
#       r = (F / S - 1) * 365 / days
#
# Rate is stored internally as decimal:
#
#       0.045 = 4.5%
# ============================================================

def calculate_curve(
    curve_name,
    df_definition,
    prices,
):

    # ========================================================
    # FIND SPOT ROW
    # ========================================================

    spot_rows = df_definition[
        df_definition["type"]
        .str.lower()
        .eq("spot")
    ]

    if spot_rows.empty:
        return pd.DataFrame()

    spot_row = (
        spot_rows.iloc[0]
    )

    spot_contract = (
        spot_row["contract"]
    )

    # ========================================================
    # GET SPOT PRICE
    # ========================================================

    spot_data = get_price_data(
        prices,
        spot_contract,
    )

    if (
        spot_data is None
        or spot_data["mid"] is None
    ):

        return pd.DataFrame()

    spot_mid = float(
        spot_data["mid"]
    )

    if spot_mid == 0:
        return pd.DataFrame()

    # ========================================================
    # TODAY
    #
    # Normalize to calendar date.
    # Intraday time therefore doesn't change ndays.
    # ========================================================

    today = (
        pd.Timestamp.now()
        .normalize()
    )

    output = []

    # ========================================================
    # ADD SPOT ROW TO SOURCE TABLE
    #
    # Spot is NOT plotted as a rate point.
    # It is shown in Show sources for transparency.
    # ========================================================

    output.append(
        {
            "curve": curve_name,

            "contract": spot_contract,

            "type": "Spot",

            "expiration": pd.NaT,

            "days": None,

            "bid": spot_data["bid"],

            "ask": spot_data["ask"],

            "mid": spot_mid,

            "rate": None,
        }
    )

    # ========================================================
    # FUTURES
    # ========================================================

    futures = df_definition[
        df_definition["type"]
        .str.lower()
        .eq("futures")
    ].copy()

    for _, row in futures.iterrows():

        contract = row[
            "contract"
        ]

        expiration = row[
            "expiration"
        ]

        # --------------------------------------------
        # Get futures price
        # --------------------------------------------

        price_data = get_price_data(
            prices,
            contract,
        )

        if (
            price_data is None
            or price_data["mid"] is None
            or pd.isna(expiration)
        ):

            continue

        future_mid = float(
            price_data["mid"]
        )

        # --------------------------------------------
        # Expiration date
        # --------------------------------------------

        expiration_date = (
            pd.Timestamp(
                expiration
            )
            .normalize()
        )

        # --------------------------------------------
        # Calendar days to expiration
        # --------------------------------------------

        days = (
            expiration_date
            - today
        ).days

        # --------------------------------------------
        # Ignore expired / same-day contracts
        # --------------------------------------------

        if days <= 0:
            continue

        # --------------------------------------------
        # Implied simple annualized rate
        # --------------------------------------------

        rate = (
            (
                future_mid
                / spot_mid
            )
            - 1
        ) * 365 / days

        # --------------------------------------------
        # Add result
        # --------------------------------------------

        output.append(
            {
                "curve": curve_name,

                "contract": contract,

                "type": "Futures",

                "expiration": (
                    expiration_date
                ),

                "days": days,

                "bid": (
                    price_data["bid"]
                ),

                "ask": (
                    price_data["ask"]
                ),

                "mid": future_mid,

                "rate": rate,
            }
        )

    return pd.DataFrame(
        output
    )


# ============================================================
# CALCULATE ALL CURVES
# ============================================================

def calculate_all_curves(
    curve_definitions,
    prices,
):

    result = {}

    for (
        group_name,
        group_data,
    ) in curve_definitions.items():

        result[group_name] = {}

        for (
            curve_name,
            df_definition,
        ) in group_data.items():

            result[
                group_name
            ][
                curve_name
            ] = calculate_curve(
                curve_name,
                df_definition,
                prices,
            )

    return result


# ============================================================
# BUILD CHART DATA
#
# IMPORTANT:
#
# We use LONG format rather than a shared dataframe index.
#
# This means every metal keeps its own independent maturity
# points. GC, SI, PL and PA do NOT need matching expirations.
# ============================================================

def build_chart_data(
    group_curves,
):

    frames = []

    for (
        curve_name,
        df,
    ) in group_curves.items():

        if df.empty:
            continue

        # --------------------------------------------
        # Futures only
        # --------------------------------------------

        futures = df[
            df["type"]
            .str.lower()
            .eq("futures")
        ].copy()

        # --------------------------------------------
        # Valid calculated points only
        # --------------------------------------------

        futures = futures[
            futures["rate"].notna()
            & futures["days"].notna()
        ].copy()

        if futures.empty:
            continue

        # --------------------------------------------
        # Convert decimal rate to percentage points
        #
        # 0.045 -> 4.5
        # --------------------------------------------

        futures["rate_pct"] = (
            futures["rate"]
            * 100
        )

        futures["curve"] = (
            curve_name
        )

        frames.append(
            futures[
                [
                    "curve",
                    "contract",
                    "expiration",
                    "days",
                    "rate_pct",
                ]
            ]
        )

    # ========================================================
    # NOTHING TO PLOT
    # ========================================================

    if not frames:
        return pd.DataFrame()

    # ========================================================
    # COMBINE
    # ========================================================

    chart_df = pd.concat(
        frames,
        ignore_index=True,
    )

    # ========================================================
    # CLEAN NUMERIC VALUES
    # ========================================================

    chart_df["days"] = (
        pd.to_numeric(
            chart_df["days"],
            errors="coerce",
        )
    )

    chart_df["rate_pct"] = (
        pd.to_numeric(
            chart_df["rate_pct"],
            errors="coerce",
        )
    )

    # ========================================================
    # REMOVE INVALID POINTS
    # ========================================================

    chart_df = (
        chart_df
        .dropna(
            subset=[
                "days",
                "rate_pct",
            ]
        )
    )

    # ========================================================
    # SORT EACH CURVE BY MATURITY
    # ========================================================

    chart_df = (
        chart_df
        .sort_values(
            [
                "curve",
                "days",
            ]
        )
        .reset_index(
            drop=True
        )
    )

    return chart_df


# ============================================================
# BUILD CURVE CHART
#
# Features:
#
# - independent maturity points
# - line + dots
# - value label on every point
# - fixed colour for each underlying
# - dynamically tightened Y axis
# - X axis begins at zero
# - 860 px height
# ============================================================

def build_curve_chart(
    chart_df,
):

    if chart_df.empty:
        return None

    df = chart_df.copy()

    # ========================================================
    # RATE LABEL
    #
    # 4.102% -> 4.1%
    #
    # Calculation itself remains full precision.
    # ========================================================

    df["rate_label"] = (
        df["rate_pct"]
        .map(
            lambda x: f"{x:.1f}%"
        )
    )

    # ========================================================
    # AXIS RANGES
    # ========================================================

    max_days = float(
        df["days"].max()
    )

    min_rate = float(
        df["rate_pct"].min()
    )

    max_rate = float(
        df["rate_pct"].max()
    )

    # --------------------------------------------
    # X starts at 0 and extends slightly beyond
    # longest maturity.
    # --------------------------------------------

    x_max = (
        max_days
        * 1.08
    )

    # --------------------------------------------
    # Tight Y axis
    # --------------------------------------------

    rate_range = (
        max_rate
        - min_rate
    )

    if rate_range > 0:

        padding = max(
            rate_range * 0.20,
            0.20,
        )

    else:

        padding = 0.50

    y_min = (
        min_rate
        - padding
    )

    y_max = (
        max_rate
        + padding
    )

    # ========================================================
    # X AXIS
    # ========================================================

    x = alt.X(
        "days:Q",

        title=(
            "Days to expiration"
        ),

        scale=alt.Scale(
            domain=[
                0,
                x_max,
            ],
            nice=True,
        ),
    )

    # ========================================================
    # Y AXIS
    #
    # Values displayed to 1 decimal.
    # ========================================================

    y = alt.Y(
        "rate_pct:Q",

        title=(
            "Implied rate (%)"
        ),

        scale=alt.Scale(
            domain=[
                y_min,
                y_max,
            ],
            zero=False,
            nice=True,
        ),

        axis=alt.Axis(
            format=".1f",
        ),
    )

    # ========================================================
    # FIXED COLOURS
    # ========================================================

    color = alt.Color(
        "curve:N",

        title=None,

        scale=alt.Scale(
            domain=(
                CURVE_COLOR_DOMAIN
            ),
            range=(
                CURVE_COLOR_RANGE
            ),
        ),

        legend=alt.Legend(
            orient="bottom",
            direction="horizontal",
        ),
    )

    # ========================================================
    # TOOLTIP
    # ========================================================

    tooltip = [

        alt.Tooltip(
            "curve:N",
            title="Curve",
        ),

        alt.Tooltip(
            "contract:N",
            title="Contract",
        ),

        alt.Tooltip(
            "expiration:T",
            title="Expiration",
            format="%d-%b-%Y",
        ),

        alt.Tooltip(
            "days:Q",
            title="Days",
            format=".0f",
        ),

        alt.Tooltip(
            "rate_pct:Q",
            title="Rate (%)",
            format=".1f",
        ),
    ]

    # ========================================================
    # LINES
    # ========================================================

    lines = (
        alt.Chart(df)
        .mark_line(
            strokeWidth=3,
        )
        .encode(
            x=x,

            y=y,

            color=color,

            detail="curve:N",

            order="days:Q",

            tooltip=tooltip,
        )
    )

    # ========================================================
    # DOTS
    # ========================================================

    points = (
        alt.Chart(df)
        .mark_circle(
            size=130,
            strokeWidth=1.5,
        )
        .encode(
            x=x,

            y=y,

            color=color,

            tooltip=tooltip,
        )
    )

    # ========================================================
    # RATE LABELS
    # ========================================================

    labels = (
        alt.Chart(df)
        .mark_text(
            align="left",
            baseline="middle",

            dx=9,
            dy=-11,

            fontSize=13,
        )
        .encode(
            x=x,

            y=y,

            text="rate_label:N",

            color=color,
        )
    )

    # ========================================================
    # COMBINE
    # ========================================================

    chart = (
        alt.layer(
            lines,
            points,
            labels,
        )
        .properties(
            width=CURVE_CHART_WIDTH,
            height=CURVE_CHART_HEIGHT,
        )
        .interactive()
    )

    return chart


# ============================================================
# SOURCE TABLE FORMATTING
# ============================================================

def format_price(
    value,
):

    if value is None:
        return ""

    if pd.isna(value):
        return ""

    return (
        f"{float(value):,.3f}"
    )


def format_rate(
    value,
):

    if value is None:
        return ""

    if pd.isna(value):
        return ""

    # --------------------------------------------
    # 1 decimal percentage
    # --------------------------------------------

    return (
        f"{float(value) * 100:.1f}%"
    )


def format_days(
    value,
):

    if value is None:
        return ""

    if pd.isna(value):
        return ""

    return str(
        int(value)
    )


def format_expiration(
    value,
):

    if value is None:
        return ""

    if pd.isna(value):
        return ""

    return (
        pd.Timestamp(value)
        .strftime(
            "%d-%b-%Y"
        )
    )


# ============================================================
# SOURCE TABLE STYLE
# ============================================================

def style_source_table(
    df,
):

    return (
        df.style

        # --------------------------------------------
        # Formatting
        # --------------------------------------------

        .format(
            {
                "expiration": (
                    format_expiration
                ),

                "days": (
                    format_days
                ),

                "bid": (
                    format_price
                ),

                "ask": (
                    format_price
                ),

                "mid": (
                    format_price
                ),

                "rate": (
                    format_rate
                ),
            }
        )

        # --------------------------------------------
        # Centre all cells
        # --------------------------------------------

        .set_properties(
            **{
                "text-align": "center",
            }
        )

        # --------------------------------------------
        # Contract bold
        # --------------------------------------------

        .set_properties(
            subset=[
                "contract"
            ],
            **{
                "font-weight": "bold",
            }
        )

        # --------------------------------------------
        # Centre headers
        # --------------------------------------------

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


# ============================================================
# RENDER ONE CURVE GROUP
#
# IMPORTANT:
#
# Each asset-class group is independent.
#
# Later we can call this several times:
#
# render_curve_group("Precious Metals", ...)
# render_curve_group("Energy", ...)
# render_curve_group("Crypto", ...)
# etc.
#
# Each group gets:
#
# - its own chart
# - its own Show sources toggle
# - its own source table
# ============================================================

def render_curve_group(
    group_name,
    group_curves,
):

    # ========================================================
    # GROUP TITLE
    # ========================================================

    st.subheader(
        group_name
    )

    # ========================================================
    # BUILD CHART DATA
    # ========================================================

    chart_df = (
        build_chart_data(
            group_curves
        )
    )

    # ========================================================
    # CHART
    # ========================================================

    if chart_df.empty:

        st.warning(
            f"No valid curve data available for {group_name}."
        )

    else:

        chart = (
            build_curve_chart(
                chart_df
            )
        )

        st.altair_chart(
            chart,
            use_container_width=False,
        )

    # ========================================================
    # SHOW SOURCES
    #
    # Unique key is important because later there will be
    # several Show sources toggles on the same page.
    # ========================================================

    show_sources = st.toggle(
        "Show sources",

        value=False,

        key=(
            f"show_sources_{group_name}"
        ),
    )

    if not show_sources:
        return

    # ========================================================
    # COMBINE SOURCE DATA
    # ========================================================

    source_frames = []

    for (
        curve_name,
        df,
    ) in group_curves.items():

        if df.empty:
            continue

        source_frames.append(
            df.copy()
        )

    # ========================================================
    # NO SOURCE DATA
    # ========================================================

    if not source_frames:

        st.info(
            "No source data available."
        )

        return

    # ========================================================
    # COMBINE
    # ========================================================

    source_df = pd.concat(
        source_frames,
        ignore_index=True,
    )

    # ========================================================
    # SORT
    #
    # Spot first, then futures by maturity.
    # ========================================================

    type_order = {
        "Spot": 0,
        "Futures": 1,
    }

    source_df[
        "_type_order"
    ] = (
        source_df["type"]
        .map(
            type_order
        )
        .fillna(9)
    )

    source_df = (
        source_df
        .sort_values(
            by=[
                "curve",
                "_type_order",
                "days",
            ],
            na_position="first",
        )
        .drop(
            columns=[
                "_type_order",
            ]
        )
        .reset_index(
            drop=True
        )
    )

    # ========================================================
    # DISPLAY COLUMNS
    # ========================================================

    display_df = source_df[
        [
            "curve",
            "contract",
            "type",
            "expiration",
            "days",
            "bid",
            "ask",
            "mid",
            "rate",
        ]
    ]

    # ========================================================
    # TABLE HEIGHT
    #
    # Show all source rows without internal scrolling.
    # ========================================================

    table_height = (
        38
        + len(display_df) * 35
    )

    # ========================================================
    # DISPLAY SOURCE TABLE
    # ========================================================

    st.dataframe(
        style_source_table(
            display_df
        ),

        hide_index=True,

        use_container_width=False,

        width=1000,

        height=table_height,
    )

# ============================================================
# CRYPTO CURVES
#
# These tables already contain calculated rates:
#
# b1_curve_r_btc
# b1_curve_r_eth
#
# Columns:
#     contract
#     ndays
#     rate
#
# IMPORTANT:
# These tables are live and therefore are NOT cached.
# ============================================================

CRYPTO_CURVE_TABLES = {
    "BTC": "b1_curve_r_btc",
    "ETH": "b1_curve_r_eth",
}


CRYPTO_COLOR_DOMAIN = [
    "BTC",
    "ETH",
]


CRYPTO_COLOR_RANGE = [
    "#D62728",   # BTC - red
    "#1976B9",   # ETH - blue
]


# ============================================================
# LOAD CRYPTO CURVES
# ============================================================

def load_crypto_curves():

    conn = get_conn()

    frames = []

    with conn.cursor() as cur:

        for curve_name, table_name in CRYPTO_CURVE_TABLES.items():

            sql = f"""
                SELECT
                    contract,
                    ndays,
                    rate
                FROM public.{table_name}
                ORDER BY ndays
            """

            cur.execute(sql)

            rows = cur.fetchall()

            if not rows:
                continue

            df = pd.DataFrame(
                rows,
                columns=[
                    "contract",
                    "ndays",
                    "rate",
                ],
            )

            # --------------------------------------------
            # Clean
            # --------------------------------------------

            df["contract"] = (
                df["contract"]
                .fillna("")
                .astype(str)
                .str.strip()
            )

            df["ndays"] = pd.to_numeric(
                df["ndays"],
                errors="coerce",
            )

            df["rate"] = pd.to_numeric(
                df["rate"],
                errors="coerce",
            )

            df["curve"] = curve_name

            frames.append(df)

    # ========================================================
    # NOTHING FOUND
    # ========================================================

    if not frames:

        return pd.DataFrame(
            columns=[
                "curve",
                "contract",
                "ndays",
                "rate",
            ]
        )

    # ========================================================
    # COMBINE BTC + ETH
    # ========================================================

    df = pd.concat(
        frames,
        ignore_index=True,
    )

    # ========================================================
    # REMOVE INVALID VALUES
    # ========================================================

    df = (
        df
        .dropna(
            subset=[
                "ndays",
                "rate",
            ]
        )
        .sort_values(
            [
                "curve",
                "ndays",
            ]
        )
        .reset_index(drop=True)
    )

    return df


# ============================================================
# BUILD CRYPTO CHART
# ============================================================

def build_crypto_chart(
    crypto_df,
):

    if crypto_df.empty:
        return None

    df = crypto_df.copy()

    # ========================================================
    # RATE IN PERCENTAGE POINTS
    #
    # 0.05037 -> 5.037
    # ========================================================

    df["rate_pct"] = (
        df["rate"]
        * 100
    )

    # ========================================================
    # DISPLAY LABEL
    #
    # 5.037% -> 5.0%
    # ========================================================

    df["rate_label"] = (
        df["rate_pct"]
        .map(
            lambda x: f"{x:.1f}%"
        )
    )

    # ========================================================
    # AXIS RANGES
    # ========================================================

    max_days = float(
        df["ndays"].max()
    )

    min_rate = float(
        df["rate_pct"].min()
    )

    max_rate = float(
        df["rate_pct"].max()
    )

    # --------------------------------------------
    # X axis
    # --------------------------------------------

    if max_days > 0:

        x_max = (
            max_days
            * 1.08
        )

    else:

        x_max = 30

    # --------------------------------------------
    # Tight Y axis
    # --------------------------------------------

    rate_range = (
        max_rate
        - min_rate
    )

    if rate_range > 0:

        padding = max(
            rate_range * 0.20,
            0.20,
        )

    else:

        padding = 0.50

    y_min = (
        min_rate
        - padding
    )

    y_max = (
        max_rate
        + padding
    )

    # ========================================================
    # X AXIS
    # ========================================================

    x = alt.X(
        "ndays:Q",

        title="Days to expiration",

        scale=alt.Scale(
            domain=[
                0,
                x_max,
            ],
            nice=True,
        ),
    )

    # ========================================================
    # Y AXIS
    # ========================================================

    y = alt.Y(
        "rate_pct:Q",

        title="Implied rate (%)",

        scale=alt.Scale(
            domain=[
                y_min,
                y_max,
            ],
            zero=False,
            nice=True,
        ),

        axis=alt.Axis(
            format=".1f",
        ),
    )

    # ========================================================
    # FIXED COLORS
    #
    # BTC = RED
    # ETH = BLUE
    # ========================================================

    color = alt.Color(
        "curve:N",

        title=None,

        scale=alt.Scale(
            domain=(
                CRYPTO_COLOR_DOMAIN
            ),

            range=(
                CRYPTO_COLOR_RANGE
            ),
        ),

        legend=alt.Legend(
            orient="bottom",
            direction="horizontal",
        ),
    )

    # ========================================================
    # TOOLTIP
    # ========================================================

    tooltip = [

        alt.Tooltip(
            "curve:N",
            title="Curve",
        ),

        alt.Tooltip(
            "contract:N",
            title="Contract",
        ),

        alt.Tooltip(
            "ndays:Q",
            title="Days",
            format=".0f",
        ),

        alt.Tooltip(
            "rate_pct:Q",
            title="Rate (%)",
            format=".1f",
        ),
    ]

    # ========================================================
    # LINES
    # ========================================================

    lines = (
        alt.Chart(df)
        .mark_line(
            strokeWidth=3,
        )
        .encode(
            x=x,

            y=y,

            color=color,

            detail="curve:N",

            order="ndays:Q",

            tooltip=tooltip,
        )
    )

    # ========================================================
    # DOTS
    # ========================================================

    points = (
        alt.Chart(df)
        .mark_circle(
            size=130,
            strokeWidth=1.5,
        )
        .encode(
            x=x,

            y=y,

            color=color,

            tooltip=tooltip,
        )
    )

    # ========================================================
    # VALUE LABELS
    # ========================================================

    labels = (
        alt.Chart(df)
        .mark_text(
            align="left",
            baseline="middle",

            dx=9,
            dy=-11,

            fontSize=13,
        )
        .encode(
            x=x,

            y=y,

            text="rate_label:N",

            color=color,
        )
    )

    # ========================================================
    # COMBINE
    # ========================================================

    chart = (
        alt.layer(
            lines,
            points,
            labels,
        )
        .properties(
            width=CURVE_CHART_WIDTH,
            height=CURVE_CHART_HEIGHT,
        )
        .interactive()
    )

    return chart


# ============================================================
# CRYPTO SOURCE TABLE FORMATTING
# ============================================================

def style_crypto_source_table(
    df,
):

    return (
        df.style

        .format(
            {
                "ndays": (
                    lambda x:
                    ""
                    if pd.isna(x)
                    else f"{int(x)}"
                ),

                "rate": (
                    lambda x:
                    ""
                    if pd.isna(x)
                    else f"{float(x) * 100:.1f}%"
                ),
            }
        )

        .set_properties(
            **{
                "text-align": "center",
            }
        )

        .set_properties(
            subset=[
                "contract"
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


# ============================================================
# RENDER CRYPTO GROUP
# ============================================================

def render_crypto_group():

    # ========================================================
    # TITLE
    # ========================================================

    st.subheader(
        "Crypto"
    )

    # ========================================================
    # LIVE DATA
    #
    # Reloaded every global refresh.
    # ========================================================

    crypto_df = (
        load_crypto_curves()
    )

    # ========================================================
    # CHART
    # ========================================================

    if crypto_df.empty:

        st.warning(
            "No valid Crypto curve data available."
        )

    else:

        chart = (
            build_crypto_chart(
                crypto_df
            )
        )

        st.altair_chart(
            chart,
            use_container_width=False,
        )

    # ========================================================
    # SHOW SOURCES
    # ========================================================

    show_sources = st.toggle(
        "Show sources",

        value=False,

        key="show_sources_Crypto",
    )

    if not show_sources:
        return

    # ========================================================
    # SOURCE TABLE
    # ========================================================

    if crypto_df.empty:

        st.info(
            "No Crypto source data available."
        )

        return

    display_df = crypto_df[
        [
            "curve",
            "contract",
            "ndays",
            "rate",
        ]
    ].copy()

    # ========================================================
    # TABLE HEIGHT
    # ========================================================

    table_height = (
        38
        + len(display_df) * 35
    )

    # ========================================================
    # DISPLAY
    # ========================================================

    st.dataframe(
        style_crypto_source_table(
            display_df
        ),

        hide_index=True,

        use_container_width=False,

        width=650,

        height=table_height,
    )
# ============================================================
# MAIN CURVES PAGE
# ============================================================

def render_curves_page():

    # ========================================================
    # LOAD CURVE DEFINITIONS
    #
    # Cached.
    # ========================================================

    curve_definitions = (
        load_curve_definitions()
    )

    # ========================================================
    # LOAD LIVE PRICES
    #
    # NOT cached.
    #
    # Therefore refreshed according to global REFRESH_RATE.
    # ========================================================

    prices = (
        load_curve_prices(
            curve_definitions
        )
    )

    # ========================================================
    # CALCULATE ALL CURVES
    # ========================================================

    calculated_curves = (
        calculate_all_curves(
            curve_definitions,
            prices,
        )
    )

    # ========================================================
    # 1. PRECIOUS METALS
    # ========================================================

    render_curve_group(
        "Precious Metals",

        calculated_curves[
            "Precious Metals"
        ],
    )

    # ========================================================
    # 2. CRYPTO
    # ========================================================

    st.divider()

    render_crypto_group()

    # ========================================================
    # FUTURE CURVE GROUPS
    #
    # We will add them here underneath Precious Metals.
    #
    # Example:
    #
    # st.divider()
    #
    # render_curve_group(
    #     "Energy",
    #     calculated_curves["Energy"],
    # )
    #
    #
    # st.divider()
    #
    # render_curve_group(
    #     "Crypto",
    #     calculated_curves["Crypto"],
    # )
    #
    #
    # st.divider()
    #
    # render_curve_group(
    #     "Rates",
    #     calculated_curves["Rates"],
    # )
    # ========================================================