from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from db import get_conn


# ============================================================
# SETTINGS
# ============================================================

INDEX_LIST = ["IMOEX2", "RTSI", "RGBI"]

DAYS_IN_YEAR = 365.0
DIVIDEND_TAX_RATE = 0.15
INDEX_FUTURES_PRICE_DIVISOR = 100.0


# ============================================================
# BASIC HELPERS
# ============================================================

def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def to_float(value):
    value = pd.to_numeric(value, errors="coerce")

    if pd.isna(value):
        return None

    return float(value)


def to_date(value):
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    parsed = pd.to_datetime(value, errors="coerce")

    if pd.isna(parsed):
        return None

    return parsed.date()


def mx_contract(contract):
    contract = clean_text(contract)

    if not contract:
        return ""

    if contract.upper().startswith("MX:"):
        return contract

    return "MX:" + contract


# ============================================================
# STATIC REFERENCE DATA
# ============================================================

@st.cache_resource
def load_index_reference_data():
    conn = get_conn()

    # --------------------------------------------------------
    # IMOEX MEMBERS
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # RGBI MEMBERS
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # INDEX DIVISORS
    # --------------------------------------------------------

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

    if not imoex.empty:
        imoex["contract"] = (
            imoex["contract"]
            .fillna("")
            .astype(str)
            .str.strip()
        )

        for col in ["n_shares", "ff", "k_cap"]:
            imoex[col] = pd.to_numeric(
                imoex[col],
                errors="coerce",
            )

    if not rgbi.empty:
        rgbi["contract"] = (
            rgbi["contract"]
            .fillna("")
            .astype(str)
            .str.strip()
        )

        for col in ["n_shares", "ff", "w"]:
            rgbi[col] = pd.to_numeric(
                rgbi[col],
                errors="coerce",
            )

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
# INDEX FUTURES REFERENCE DATA
# ============================================================

@st.cache_resource
def load_index_futures_reference_data():
    conn = get_conn()

    # --------------------------------------------------------
    # INDEX FUTURES LIST
    # --------------------------------------------------------

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT contract
            FROM public.b1_index_arb
            ORDER BY contract
            """
        )
        rows = cur.fetchall()

    futures = pd.DataFrame(
        rows,
        columns=["contract"],
    )

    if not futures.empty:
        futures["contract"] = (
            futures["contract"]
            .fillna("")
            .astype(str)
            .str.strip()
        )

    # --------------------------------------------------------
    # IMOEX DIVIDENDS
    # --------------------------------------------------------

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                contract,
                dividend,
                rec_date
            FROM public.b1_divs_imoex
            """
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

    if not dividends.empty:
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
        ).dt.date

    # --------------------------------------------------------
    # RUB CURVE
    # --------------------------------------------------------

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                ndays,
                rate
            FROM public.b1_curve_rub
            ORDER BY ndays
            """
        )
        rows = cur.fetchall()

    rub_curve = pd.DataFrame(
        rows,
        columns=[
            "ndays",
            "rate",
        ],
    )

    if not rub_curve.empty:
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
            .dropna(subset=["ndays", "rate"])
            .sort_values("ndays")
            .reset_index(drop=True)
        )

    # --------------------------------------------------------
    # MOEX STATIC DATA
    # --------------------------------------------------------

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                contract,
                expiration
            FROM public.b1_static_data_moex
            """
        )
        rows = cur.fetchall()

    static_data = pd.DataFrame(
        rows,
        columns=[
            "contract",
            "expiration",
        ],
    )

    if not static_data.empty:
        static_data["contract"] = (
            static_data["contract"]
            .fillna("")
            .astype(str)
            .str.strip()
        )

        static_data["expiration"] = pd.to_datetime(
            static_data["expiration"],
            errors="coerce",
        ).dt.date

    return {
        "futures": futures,
        "dividends": dividends,
        "rub_curve": rub_curve,
        "static_data": static_data,
    }


# ============================================================
# DIVISOR LOOKUP
# ============================================================

def get_divisor(divisors, index_name):
    if divisors.empty:
        return None

    target = clean_text(index_name).upper()

    matched = divisors[
        divisors["index"] == target
    ]

    if matched.empty:
        return None

    return to_float(
        matched.iloc[0]["dev"]
    )


# ============================================================
# LIVE INDEX / MEMBER DATA
# ============================================================

def load_live_index_data(reference):
    conn = get_conn()

    imoex = reference["imoex"]
    rgbi = reference["rgbi"]

    required_contracts = set()

    for contract in imoex["contract"]:
        code = mx_contract(contract)

        if code:
            required_contracts.add(code)

    for contract in rgbi["contract"]:
        code = mx_contract(contract)

        if code:
            required_contracts.add(code)

    required_contracts.update(
        [
            "MX:IMOEX2",
            "MX:RTSI",
            "MX:RGBI",
        ]
    )

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
            (list(required_contracts),),
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

        bid = to_float(bid)
        ask = to_float(ask)

        if bid is not None and ask is not None:
            mtm = (bid + ask) / 2.0
        elif bid is not None:
            mtm = bid
        elif ask is not None:
            mtm = ask
        else:
            mtm = None

        market[clean_text(contract)] = {
            "bid": bid,
            "ask": ask,
            "mtm": mtm,
            "duration": to_float(duration),
            "yield": to_float(yield_value),
        }

    # --------------------------------------------------------
    # CBR USD RATE
    # --------------------------------------------------------

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM public.b1_cbr
            """
        )

        rows = cur.fetchall()

        columns = [
            description.name
            for description in cur.description
        ]

    cbr = pd.DataFrame(
        rows,
        columns=columns,
    )

    usd_cbr = None
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

        usd_rows = cbr[mask]

        if not usd_rows.empty:
            usd_cbr = to_float(
                usd_rows.iloc[0]["mtm"]
            )

    return market, usd_cbr


# ============================================================
# MARKET LOOKUPS
# ============================================================

def get_market_row(market, contract):
    return market.get(
        mx_contract(contract)
    )


def get_mtm(market, contract):
    row = get_market_row(
        market,
        contract,
    )

    if row is None:
        return None

    return row["mtm"]


# ============================================================
# IMOEX CALCULATION
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

            total_contribution += contribution

        rows.append(
            {
                "contract": contract,
                "mtm": mtm,
                "contribution": contribution,
            }
        )

    members_live = pd.DataFrame(
        rows
    )

    if (
        not members_live.empty
        and total_contribution != 0
    ):
        members_live["weight"] = (
            members_live["contribution"]
            / total_contribution
        )
    else:
        members_live["weight"] = None

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
# RGBI CALCULATION
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
            mtm = market_row["mtm"]
            duration_days = market_row["duration"]
            yield_value = market_row["yield"]

        n_shares = to_float(
            member["n_shares"]
        )

        ff = to_float(
            member["ff"]
        )

        w = to_float(
            member["w"]
        )

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

            total_contribution += contribution

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
                "duration": duration_years,
                "yield": yield_value,
                "contribution": contribution,
            }
        )

    members_live = pd.DataFrame(
        rows
    )

    if (
        not members_live.empty
        and total_contribution != 0
    ):
        members_live["weight"] = (
            members_live["contribution"]
            / total_contribution
        )
    else:
        members_live["weight"] = None

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

    weighted_duration = None

    if not members_live.empty:

        valid = members_live[
            members_live["weight"].notna()
            & members_live["duration"].notna()
        ]

        if not valid.empty:

            weight_sum = valid[
                "weight"
            ].sum()

            if weight_sum != 0:
                weighted_duration = (
                    (
                        valid["weight"]
                        * valid["duration"]
                    ).sum()
                    / weight_sum
                )

    weighted_yield = None

    if not members_live.empty:

        valid = members_live[
            members_live["weight"].notna()
            & members_live["yield"].notna()
        ]

        if not valid.empty:

            weight_sum = valid[
                "weight"
            ].sum()

            if weight_sum != 0:
                weighted_yield = (
                    (
                        valid["weight"]
                        * valid["yield"]
                    ).sum()
                    / weight_sum
                )

    return (
        calculated,
        weighted_yield,
        weighted_duration,
        members_live,
    )


# ============================================================
# VALUATION DATE
# ============================================================

def get_index_valuation_date():
    today = date.today()
    weekday = today.weekday()

    if weekday <= 3:
        return today + timedelta(days=1)

    if weekday == 4:
        return today + timedelta(days=3)

    if weekday == 5:
        return today + timedelta(days=2)

    return today + timedelta(days=1)


# ============================================================
# FUTURES EXPIRATION
# ============================================================

def get_index_future_expiration(
    static_data,
    contract,
):
    contract = clean_text(
        contract
    )

    if (
        not contract
        or static_data.empty
    ):
        return None

    candidates = {
        contract.upper(),
        mx_contract(contract).upper(),
    }

    matched = static_data[
        static_data["contract"]
        .astype(str)
        .str.strip()
        .str.upper()
        .isin(candidates)
    ]

    if matched.empty:
        return None

    return to_date(
        matched.iloc[0]["expiration"]
    )


# ============================================================
# RUB CURVE INTERPOLATION / EXTRAPOLATION
# ============================================================

def interpolate_rub_rate(
    rub_curve,
    ndays,
):
    if (
        rub_curve is None
        or rub_curve.empty
        or ndays is None
    ):
        return None

    curve = (
        rub_curve[
            ["ndays", "rate"]
        ]
        .dropna()
        .sort_values("ndays")
        .reset_index(drop=True)
    )

    if curve.empty:
        return None

    if len(curve) == 1:
        return float(
            curve.iloc[0]["rate"]
        )

    x = float(
        ndays
    )

    if x <= float(
        curve.iloc[0]["ndays"]
    ):
        x1 = float(
            curve.iloc[0]["ndays"]
        )

        y1 = float(
            curve.iloc[0]["rate"]
        )

        x2 = float(
            curve.iloc[1]["ndays"]
        )

        y2 = float(
            curve.iloc[1]["rate"]
        )

    elif x >= float(
        curve.iloc[-1]["ndays"]
    ):
        x1 = float(
            curve.iloc[-2]["ndays"]
        )

        y1 = float(
            curve.iloc[-2]["rate"]
        )

        x2 = float(
            curve.iloc[-1]["ndays"]
        )

        y2 = float(
            curve.iloc[-1]["rate"]
        )

    else:
        upper_rows = curve[
            curve["ndays"] >= x
        ]

        upper_idx = (
            upper_rows.index[0]
        )

        lower_idx = (
            upper_idx - 1
        )

        x1 = float(
            curve.loc[
                lower_idx,
                "ndays",
            ]
        )

        y1 = float(
            curve.loc[
                lower_idx,
                "rate",
            ]
        )

        x2 = float(
            curve.loc[
                upper_idx,
                "ndays",
            ]
        )

        y2 = float(
            curve.loc[
                upper_idx,
                "rate",
            ]
        )

    if x2 == x1:
        return y1

    return (
        y1
        + (y2 - y1)
        * (x - x1)
        / (x2 - x1)
    )


# ============================================================
# LIVE INDEX FUTURES PRICES
# ============================================================

def load_live_index_futures_prices(
    futures,
):
    if futures.empty:
        return {}

    conn = get_conn()

    contracts = []

    for contract in futures[
        "contract"
    ]:
        code = mx_contract(
            contract
        )

        if code:
            contracts.append(
                code
            )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                contract,
                bid,
                ask
            FROM public.md_snap_moex
            WHERE contract = ANY(%s)
            """,
            (contracts,),
        )

        rows = cur.fetchall()

    result = {}

    for (
        contract,
        bid,
        ask,
    ) in rows:

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
            mtm = (
                bid + ask
            ) / 2.0

        elif bid is not None:
            mtm = bid

        elif ask is not None:
            mtm = ask

        else:
            mtm = None

        result[
            clean_text(contract)
        ] = mtm

    return result


# ============================================================
# INDEX DIVIDENDS
# ============================================================

def calculate_index_dividends(
    expiration,
    valuation_date,
    dividends,
    rub_curve,
    imoex_members_live,
    index_mtm,
):
    if (
        expiration is None
        or valuation_date is None
        or index_mtm is None
        or dividends.empty
        or imoex_members_live.empty
    ):
        return (
            0.0,
            pd.DataFrame(),
        )

    effective_expiration = (
        expiration
        + timedelta(days=1)
    )

    eligible = dividends[
        (
            dividends["rec_date"]
            > valuation_date
        )
        &
        (
            dividends["rec_date"]
            <= effective_expiration
        )
    ].copy()

    if eligible.empty:
        return (
            0.0,
            pd.DataFrame(),
        )

    member_lookup = {}

    for _, member in (
        imoex_members_live.iterrows()
    ):

        contract = clean_text(
            member["contract"]
        )

        member_lookup[
            contract
        ] = {
            "spot": to_float(
                member["mtm"]
            ),
            "weight": to_float(
                member["weight"]
            ),
        }

    event_rows = []

    for _, div in (
        eligible.iterrows()
    ):

        contract = clean_text(
            div["contract"]
        )

        gross_dividend = to_float(
            div["dividend"]
        )

        rec_date = to_date(
            div["rec_date"]
        )

        member = member_lookup.get(
            contract
        )

        if (
            member is None
            or gross_dividend is None
            or rec_date is None
        ):
            continue

        spot = member["spot"]
        weight = member["weight"]

        if (
            spot is None
            or spot == 0
            or weight is None
        ):
            continue

        # ----------------------------------------------------
        # NET DIVIDEND
        # ----------------------------------------------------

        net_dividend = (
            gross_dividend
            * (
                1.0
                - DIVIDEND_TAX_RATE
            )
        )

        # ----------------------------------------------------
        # FUTURE VALUE OF DIVIDEND
        # ----------------------------------------------------

        fv_days = (
            effective_expiration
            - rec_date
        ).days

        fv_days = max(
            fv_days,
            0,
        )

        rub_rate = (
            interpolate_rub_rate(
                rub_curve,
                fv_days,
            )
        )

        if rub_rate is None:
            rub_rate = 0.0

        future_value = (
            net_dividend
            * (
                1.0
                + rub_rate
                * fv_days
                / DAYS_IN_YEAR
            )
        )

        dividend_yield = (
            future_value
            / spot
        )

        points = (
            dividend_yield
            * weight
            * index_mtm
        )

        event_rows.append(
            {
                "contract": contract,
                "rec_date": rec_date,
                "gross_dividend": gross_dividend,
                "net_dividend": net_dividend,
                "future_value": future_value,
                "spot": spot,
                "weight": weight,
                "points": points,
            }
        )

    if not event_rows:
        return (
            0.0,
            pd.DataFrame(),
        )

    events = pd.DataFrame(
        event_rows
    )

    by_stock = (
        events
        .groupby(
            "contract",
            as_index=False,
        )
        .agg(
            points=(
                "points",
                "sum",
            )
        )
    )

    total_points = (
        by_stock["points"].sum()
    )

    return (
        float(total_points),
        by_stock,
    )


# ============================================================
# SECTION 2:
# INDEX FUTURES FAIR VALUES
# ============================================================

def calculate_index_futures_fair_values(
    reference,
    imoex_members_live,
    index_mtm,
):
    futures = reference[
        "futures"
    ]

    dividends = reference[
        "dividends"
    ]

    rub_curve = reference[
        "rub_curve"
    ]

    static_data = reference[
        "static_data"
    ]

    if futures.empty:
        return (
            pd.DataFrame(),
            pd.DataFrame(),
        )

    valuation_date = (
        get_index_valuation_date()
    )

    live_futures = (
        load_live_index_futures_prices(
            futures
        )
    )

    result_rows = []
    contribution_dict = {}

    for _, future in (
        futures.iterrows()
    ):

        contract = clean_text(
            future["contract"]
        )

        # ----------------------------------------------------
        # EXPIRATION
        # ----------------------------------------------------

        expiration = (
            get_index_future_expiration(
                static_data,
                contract,
            )
        )

        effective_expiration = None
        ndays = None

        if expiration is not None:

            effective_expiration = (
                expiration
                + timedelta(days=1)
            )

            ndays = (
                effective_expiration
                - valuation_date
            ).days

        # ----------------------------------------------------
        # FUTURES PRICE
        #
        # MOEX MX futures are quoted x100.
        # ----------------------------------------------------

        futures_mtm_raw = (
            live_futures.get(
                mx_contract(
                    contract
                )
            )
        )

        futures_mtm = None

        if (
            futures_mtm_raw
            is not None
        ):
            futures_mtm = (
                futures_mtm_raw
                / INDEX_FUTURES_PRICE_DIVISOR
            )

        # ----------------------------------------------------
        # DIVIDENDS
        # ----------------------------------------------------

        (
            dividend_points,
            contribution_df,
        ) = calculate_index_dividends(
            expiration=expiration,
            valuation_date=valuation_date,
            dividends=dividends,
            rub_curve=rub_curve,
            imoex_members_live=(
                imoex_members_live
            ),
            index_mtm=index_mtm,
        )

        contribution_dict[
            contract
        ] = contribution_df

        # ----------------------------------------------------
        # ADJUSTED SPOT
        # ----------------------------------------------------

        adjusted_spot = None

        if index_mtm is not None:
            adjusted_spot = (
                index_mtm
                - dividend_points
            )

        # ----------------------------------------------------
        # IMPLIED RATE
        # ----------------------------------------------------

        implied_rate = None

        if (
            futures_mtm is not None
            and adjusted_spot is not None
            and adjusted_spot != 0
            and ndays is not None
            and ndays > 0
        ):
            implied_rate = (
                (
                    futures_mtm
                    / adjusted_spot
                    - 1.0
                )
                * DAYS_IN_YEAR
                / ndays
            )

        # ----------------------------------------------------
        # CURVE RATE
        # ----------------------------------------------------

        curve_rate = None

        if (
            ndays is not None
            and ndays > 0
        ):
            curve_rate = (
                interpolate_rub_rate(
                    rub_curve,
                    ndays,
                )
            )

        # ----------------------------------------------------
        # FAIR VALUE
        #
        # FV =
        # (Index - Dividend Points)
        # * (1 + curve_rate * T)
        # ----------------------------------------------------

        fair_value = None

        if (
            adjusted_spot is not None
            and curve_rate is not None
            and ndays is not None
            and ndays > 0
        ):
            fair_value = (
                adjusted_spot
                * (
                    1.0
                    + curve_rate
                    * ndays
                    / DAYS_IN_YEAR
                )
            )

        # ----------------------------------------------------
        # EDGE
        #
        # Fair Value - normalized futures MTM
        # ----------------------------------------------------

        edge = None

        if (
            fair_value is not None
            and futures_mtm is not None
        ):
            edge = (
                fair_value
                - futures_mtm
            )

        result_rows.append(
            {
                "contract": contract,
                "implied_rate": implied_rate,
                "curve_rate": curve_rate,
                "fair_value": fair_value,
                "edge": edge,
                "dividend_points": dividend_points,
                "expiration": expiration,
                "ndays": ndays,
                "futures_mtm": futures_mtm,
                "index_mtm": index_mtm,
            }
        )

    results = pd.DataFrame(
        result_rows
    )

    # --------------------------------------------------------
    # DIVIDEND CONTRIBUTION MATRIX
    # --------------------------------------------------------

    all_stocks = set()

    for df in (
        contribution_dict.values()
    ):
        if (
            df is not None
            and not df.empty
        ):
            all_stocks.update(
                df["contract"]
                .astype(str)
                .tolist()
            )

    contribution_rows = []

    for stock in all_stocks:

        item = {
            "contract": stock
        }

        for future_contract in (
            futures["contract"]
        ):

            future_contract = (
                clean_text(
                    future_contract
                )
            )

            df = (
                contribution_dict.get(
                    future_contract
                )
            )

            points = 0.0

            if (
                df is not None
                and not df.empty
            ):
                matched = df[
                    df["contract"]
                    == stock
                ]

                if not matched.empty:
                    points = float(
                        matched.iloc[0][
                            "points"
                        ]
                    )

            item[
                future_contract
            ] = points

        contribution_rows.append(
            item
        )

    contribution_matrix = (
        pd.DataFrame(
            contribution_rows
        )
    )

    if (
        not contribution_matrix.empty
        and not results.empty
    ):

        nearest = (
            results
            .dropna(
                subset=["ndays"]
            )
            .sort_values(
                "ndays"
            )
        )

        if not nearest.empty:

            front_contract = (
                nearest.iloc[0][
                    "contract"
                ]
            )

            if (
                front_contract
                in contribution_matrix.columns
            ):
                contribution_matrix = (
                    contribution_matrix
                    .sort_values(
                        front_contract,
                        ascending=False,
                    )
                    .reset_index(
                        drop=True
                    )
                )

    return (
        results,
        contribution_matrix,
    )


# ============================================================
# SECTION 3:
# MIX - RTS BOX ARBITRAGE
# ============================================================

def get_expiry_code(contract):
    """
    MXZ6 -> Z6
    MXH7 -> H7
    MXM7 -> M7
    """

    contract = (
        clean_text(
            contract
        )
        .upper()
    )

    if contract.startswith(
        "MX"
    ):
        return contract[2:]

    return ""


def load_mix_rts_box_prices(
    futures,
):
    if futures.empty:
        return {}

    conn = get_conn()

    required_contracts = set()

    for contract in (
        futures["contract"]
    ):

        contract = clean_text(
            contract
        )

        expiry_code = (
            get_expiry_code(
                contract
            )
        )

        if not expiry_code:
            continue

        required_contracts.add(
            mx_contract(
                contract
            )
        )

        required_contracts.add(
            "MX:RI"
            + expiry_code
        )

        required_contracts.add(
            "MX:Si"
            + expiry_code
        )

    if not required_contracts:
        return {}

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                contract,
                bid,
                ask
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

    prices = {}

    for (
        contract,
        bid,
        ask,
    ) in rows:

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
            mtm = (
                bid + ask
            ) / 2.0

        elif bid is not None:
            mtm = bid

        elif ask is not None:
            mtm = ask

        else:
            mtm = None

        prices[
            clean_text(
                contract
            ).upper()
        ] = mtm

    return prices


def calculate_mix_rts_box(
    futures,
):
    if futures.empty:
        return pd.DataFrame()

    prices = (
        load_mix_rts_box_prices(
            futures
        )
    )

    rows = []

    for _, row in (
        futures.iterrows()
    ):

        mx_name = (
            clean_text(
                row["contract"]
            )
            .upper()
        )

        expiry_code = (
            get_expiry_code(
                mx_name
            )
        )

        if not expiry_code:
            continue

        ri_name = (
            "RI"
            + expiry_code
        )

        si_name = (
            "Si"
            + expiry_code
        )

        mx_mtm = prices.get(
            (
                "MX:"
                + mx_name
            ).upper()
        )

        ri_mtm = prices.get(
            (
                "MX:"
                + ri_name
            ).upper()
        )

        si_mtm = prices.get(
            (
                "MX:"
                + si_name
            ).upper()
        )

        spread = None
        edge = None

        if (
            mx_mtm is not None
            and ri_mtm is not None
            and si_mtm is not None
        ):

            mx_normalized = (
                mx_mtm
                / 100.0
            )

            rts_equivalent = (
                ri_mtm
                * 0.02
                * (
                    si_mtm
                    / 1000.0
                )
                / 63.0
            )

            spread = (
                mx_normalized
                - rts_equivalent
            )

            if mx_normalized != 0:
                edge = (
                    spread
                    / mx_normalized
                )

        description = (
            mx_name
            + " - "
            + ri_name
        )

        rows.append(
            {
                "description": (
                    description
                ),
                "spread": spread,
                "edge": edge,
                "mx_mtm": mx_mtm,
                "ri_mtm": ri_mtm,
                "si_mtm": si_mtm,
            }
        )

    return pd.DataFrame(
        rows
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

    return (
        f"{value * 100:.2f}%"
    )


def fmt_weight(value):
    if (
        value is None
        or pd.isna(value)
    ):
        return ""

    return (
        f"{value * 100:.1f}%"
    )


def fmt_rate(value):
    if (
        value is None
        or pd.isna(value)
    ):
        return ""

    return (
        f"{value * 100:.2f}%"
    )


def fmt_points(value):
    if (
        value is None
        or pd.isna(value)
    ):
        return ""

    return f"{value:,.2f}"


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

    if abs(value) <= 1:
        value *= 100

    return f"{value:.2f}%"


def fmt_date(value):
    value = to_date(
        value
    )

    if value is None:
        return ""

    return value.strftime(
        "%d-%b-%Y"
    )


# ============================================================
# CASH INDEX DISPLAY
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

        calculated = calculations[
            contract
        ]

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
                    if contract
                    == "RGBI"
                    else ""
                ),
                "dur": (
                    fmt_duration(
                        rgbi_duration
                    )
                    if contract
                    == "RGBI"
                    else ""
                ),
            }
        )

    return pd.DataFrame(
        rows
    )


# ============================================================
# INDEX FUTURES DISPLAY
# ============================================================

def build_index_futures_display(
    results,
    show_details,
):
    if results.empty:

        columns = [
            "contract",
            "implied rate",
            "fair value",
            "edge",
            "dividend points",
        ]

        if show_details:
            columns += [
                "expiration",
                "ndays",
                "futures mtm",
                "index mtm",
            ]

        return pd.DataFrame(
            columns=columns
        )

    display = (
        results
        .copy()
        .sort_values(
            "ndays",
            ascending=True,
            na_position="last",
        )
    )

    data = {
        "contract": (
            display["contract"]
        ),
        "implied rate": (
            display[
                "implied_rate"
            ].apply(
                fmt_rate
            )
        ),
        "fair value": (
            display[
                "fair_value"
            ].apply(
                fmt_index
            )
        ),
        "edge": (
            display[
                "edge"
            ].apply(
                fmt_points
            )
        ),
        "dividend points": (
            display[
                "dividend_points"
            ].apply(
                fmt_points
            )
        ),
    }

    if show_details:

        data["expiration"] = (
            display[
                "expiration"
            ].apply(
                fmt_date
            )
        )

        data["ndays"] = (
            display[
                "ndays"
            ].apply(
                lambda x: (
                    ""
                    if pd.isna(x)
                    else str(
                        int(x)
                    )
                )
            )
        )

        # Already normalized /100.
        data["futures mtm"] = (
            display[
                "futures_mtm"
            ].apply(
                fmt_index
            )
        )

        data["index mtm"] = (
            display[
                "index_mtm"
            ].apply(
                fmt_index
            )
        )

    return pd.DataFrame(
        data
    )


# ============================================================
# DIVIDEND CONTRIBUTION DISPLAY
# ============================================================

def build_dividend_contribution_display(
    contribution_matrix,
    results,
):
    if contribution_matrix.empty:
        return contribution_matrix

    ordered_futures = (
        results
        .sort_values(
            "ndays",
            ascending=True,
            na_position="last",
        )["contract"]
        .tolist()
    )

    columns = [
        "contract"
    ]

    for contract in (
        ordered_futures
    ):
        if (
            contract
            in contribution_matrix.columns
        ):
            columns.append(
                contract
            )

    display = (
        contribution_matrix[
            columns
        ]
        .copy()
    )

    for col in columns[1:]:
        display[col] = (
            display[col]
            .apply(
                fmt_points
            )
        )

    return display


# ============================================================
# MIX / RTS DISPLAY
# ============================================================

def build_mix_rts_box_display(
    results,
    show_details,
):
    if results.empty:

        columns = [
            "description",
            "spread",
            "edge %",
        ]

        if show_details:
            columns += [
                "MX",
                "RI",
                "Si",
            ]

        return pd.DataFrame(
            columns=columns
        )

    data = {
        "description": (
            results[
                "description"
            ]
        ),

        "spread": (
            results[
                "spread"
            ].apply(
                fmt_points
            )
        ),

        "edge %": (
            results[
                "edge"
            ].apply(
                fmt_diff
            )
        ),
    }

    if show_details:

        # RAW market prices.
        data["MX"] = (
            results[
                "mx_mtm"
            ].apply(
                fmt_index
            )
        )

        data["RI"] = (
            results[
                "ri_mtm"
            ].apply(
                fmt_index
            )
        )

        data["Si"] = (
            results[
                "si_mtm"
            ].apply(
                fmt_index
            )
        )

    return pd.DataFrame(
        data
    )


# ============================================================
# INDEX MEMBERS DISPLAY
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
                display[
                    "contract"
                ]
            ),
            "weight": (
                display[
                    "weight"
                ].apply(
                    fmt_weight
                )
            ),
            "mtm": (
                display[
                    "mtm"
                ].apply(
                    fmt_equity_mtm
                )
            ),
        }
    )


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
                display[
                    "contract"
                ]
            ),
            "weight": (
                display[
                    "weight"
                ].apply(
                    fmt_weight
                )
            ),
            "mtm": (
                display[
                    "mtm"
                ].apply(
                    fmt_bond_mtm
                )
            ),
            "duration": (
                display[
                    "duration"
                ].apply(
                    fmt_duration
                )
            ),
            "yield": (
                display[
                    "yield"
                ].apply(
                    fmt_yield
                )
            ),
        }
    )


# ============================================================
# COMMON TABLE STYLE
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
                "text-align": (
                    "center"
                ),
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
        reference[
            "imoex"
        ]
    )

    rgbi_members = (
        reference[
            "rgbi"
        ]
    )

    divisors = (
        reference[
            "divisors"
        ]
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
    # 1. CASH INDEX
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
            rgbi_duration=(
                rgbi_duration
            ),
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
            + len(
                cash_table
            ) * 35
        ),
    )

    # ========================================================
    # 2. INDEX FUTURES FAIR VALUES
    # ========================================================

    st.divider()

    st.subheader(
        "Index Futures Fair Values"
    )

    index_futures_reference = (
        load_index_futures_reference_data()
    )

    index_mtm = get_mtm(
        market,
        "IMOEX2",
    )

    (
        index_futures_results,
        dividend_contributions,
    ) = (
        calculate_index_futures_fair_values(
            reference=(
                index_futures_reference
            ),
            imoex_members_live=(
                imoex_members_live
            ),
            index_mtm=index_mtm,
        )
    )

    show_index_futures_details = (
        st.toggle(
            "Show details",
            value=False,
            key=(
                "index_futures_"
                "show_details"
            ),
        )
    )

    index_futures_display = (
        build_index_futures_display(
            index_futures_results,
            show_index_futures_details,
        )
    )

    st.dataframe(
        style_table(
            index_futures_display,
            bold_columns=[
                "contract",
                "implied rate",
                "fair value",
            ],
        ),
        hide_index=True,
        use_container_width=False,
        width=(
            950
            if show_index_futures_details
            else 700
        ),
        height=(
            38
            + len(
                index_futures_display
            ) * 35
        ),
    )

    # --------------------------------------------------------
    # DIVIDEND CONTRIBUTIONS
    # --------------------------------------------------------

    st.markdown(
        "**Dividend Contributions, index points**"
    )

    dividend_display = (
        build_dividend_contribution_display(
            dividend_contributions,
            index_futures_results,
        )
    )

    if dividend_display.empty:

        st.caption(
            "No dividends inside the selected futures maturities."
        )

    else:

        future_columns = [
            col
            for col
            in dividend_display.columns
            if col != "contract"
        ]

        st.dataframe(
            style_table(
                dividend_display,
                bold_columns=[
                    "contract",
                ],
            ),
            hide_index=True,
            use_container_width=False,
            width=(
                220
                + 120
                * len(
                    future_columns
                )
            ),
            height=min(
                38
                + len(
                    dividend_display
                ) * 35,
                650,
            ),
        )

    # ========================================================
    # 3. MIX - RTS BOX ARBITRAGE
    # ========================================================

    st.divider()

    st.subheader(
        "MIX - RTS Box Arbitrage"
    )

    mix_rts_results = (
        calculate_mix_rts_box(
            index_futures_reference[
                "futures"
            ]
        )
    )

    show_mix_rts_details = (
        st.toggle(
            "Show details",
            value=False,
            key=(
                "mix_rts_"
                "show_details"
            ),
        )
    )

    mix_rts_display = (
        build_mix_rts_box_display(
            mix_rts_results,
            show_mix_rts_details,
        )
    )

    st.dataframe(
        style_table(
            mix_rts_display,
            bold_columns=[
                "description",
                "edge %",
            ],
        ),
        hide_index=True,
        use_container_width=False,
        width=(
            750
            if show_mix_rts_details
            else 450
        ),
        height=(
            38
            + len(
                mix_rts_display
            ) * 35
        ),
    )

    # ========================================================
    # 4. INDEX MEMBERS
    # ========================================================

    st.divider()

    st.subheader(
        "Index Members"
    )

    # --------------------------------------------------------
    # IMOEX
    # --------------------------------------------------------

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
            + len(
                imoex_display
            ) * 35,
            600,
        ),
    )

    # --------------------------------------------------------
    # RGBI
    # --------------------------------------------------------

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
            + len(
                rgbi_display
            ) * 35,
            600,
        ),
    )