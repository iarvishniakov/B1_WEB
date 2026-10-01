from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from pages_app.fair_values import build_fair_values_table


# ============================================================
# SETTINGS
# ============================================================

MIN_NDAYS = 7


# ============================================================
# DATA
# ============================================================

def load_yields_data() -> pd.DataFrame:
    """
    Get the same RAW data used by Fair Values.

    Excludes:
        - section rows
        - contracts with ndays < 7
        - rows with missing ndays

    Percentage values remain decimal fractions:
        0.12 = 12%
    """

    df = build_fair_values_table().copy()

    if df.empty:
        return df

    # --------------------------------------------------------
    # Remove Fair Values section rows
    # --------------------------------------------------------

    if "_section" in df.columns:

        section_mask = (
            df["_section"]
            .fillna(False)
            .astype(bool)
        )

        df = df[
            ~section_mask
        ].copy()

    # --------------------------------------------------------
    # Convert required columns to numeric
    # --------------------------------------------------------

    numeric_columns = [
        "ndays",
        "| RAR |",
        "| ROC |",
    ]

    for col in numeric_columns:

        if col not in df.columns:
            df[col] = pd.NA

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    # --------------------------------------------------------
    # Exclude contracts shorter than 7 days
    # --------------------------------------------------------

    df = df[
        df["ndays"].notna()
        & (df["ndays"] >= MIN_NDAYS)
    ].copy()

    return df


# ============================================================
# SCATTER CHART
# ============================================================

def build_scatter_chart(
    df: pd.DataFrame,
    metric: str,
    title: str,
) -> alt.Chart:

    plot_df = df[
        [
            "Contract",
            "ndays",
            metric,
        ]
    ].copy()

    # Remove contracts without a value for this metric
    plot_df = plot_df.dropna(
        subset=[
            "ndays",
            metric,
        ]
    )
    # Cap RAR / ROC displayed on chart at 100%
    plot_df[metric] = plot_df[metric].clip(
        upper=1.0
    )
    # --------------------------------------------------------
    # Base chart
    # --------------------------------------------------------

    base = (
        alt.Chart(plot_df)
        .encode(
            x=alt.X(
                "ndays:Q",
                title="ndays",
                scale=alt.Scale(
                    zero=True,
                ),
            ),
            y=alt.Y(
                f"{metric}:Q",
                title=title,
                axis=alt.Axis(
                    format=".0%",
                ),
                scale=alt.Scale(
                    zero=True,
                ),
            ),
            tooltip=[
                alt.Tooltip(
                    "Contract:N",
                    title="Contract",
                ),
                alt.Tooltip(
                    "ndays:Q",
                    title="ndays",
                    format=".0f",
                ),
                alt.Tooltip(
                    f"{metric}:Q",
                    title=title,
                    format=".1%",
                ),
            ],
        )
    )

    # --------------------------------------------------------
    # Points
    # --------------------------------------------------------

    points = base.mark_point(
        filled=True,
        size=650,
        opacity=0.10,
        strokeWidth=1,
    )

    # --------------------------------------------------------
    # Contract labels
    # --------------------------------------------------------

    labels = (
        base.mark_text(
            fontSize=12,
            fontWeight="bold",
            baseline="middle",
            align="center",
        )
        .encode(
            text=alt.Text(
                "Contract:N"
            )
        )
    )

    # --------------------------------------------------------
    # Combined chart
    # --------------------------------------------------------

    chart = (
        points
        + labels
    ).properties(
        title=alt.TitleParams(
            title,
            anchor="middle",
            fontSize=16,
        ),
        height=560,
    )

    return (
        chart
        .configure_view(
            stroke="#d9d9d9"
        )
        .configure_axis(
            grid=True
        )
    )


# ============================================================
# PAGE
# ============================================================

def render_yields_graphs_page() -> None:

    st.title(
        "Yields Graphs"
    )

    # --------------------------------------------------------
    # Load Fair Values data
    # --------------------------------------------------------

    try:

        df = load_yields_data()

    except Exception as exc:

        st.error(
            "Yields Graphs calculation "
            f"failed: {exc}"
        )

        return

    if df.empty:

        st.info(
            "No Fair Values contracts "
            f"with ndays >= {MIN_NDAYS}."
        )

        return

    # ========================================================
    # TWO GRAPHS IN ONE ROW
    # ========================================================

    col1, col2 = st.columns(
        2,
        gap="small",
    )

    # --------------------------------------------------------
    # RAR
    # --------------------------------------------------------

    with col1:

        chart = build_scatter_chart(
            df=df,
            metric="| RAR |",
            title="| RAR |",
        )

        st.altair_chart(
            chart,
            use_container_width=True,
        )

    # --------------------------------------------------------
    # ROC
    # --------------------------------------------------------

    with col2:

        chart = build_scatter_chart(
            df=df,
            metric="| ROC |",
            title="| ROC |",
        )

        st.altair_chart(
            chart,
            use_container_width=True,
        )