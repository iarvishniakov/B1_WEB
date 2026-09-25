import streamlit as st
from streamlit_autorefresh import st_autorefresh

from config import REFRESH_RATE

from pages_app.spreads import render_spreads_page
from pages_app.fair_values import render_fair_values_page
from pages_app.index_arb import render_index_arb_page
from pages_app.open_interest import render_open_interest_page
from pages_app.curves import render_curves_page
from pages_app.price_mapping import render_price_mapping_page
from pages_app.market_data import render_market_data_page


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="B1 Dashboard",
    layout="wide",
)


# ============================================================
# GLOBAL AUTO REFRESH
# ============================================================

st_autorefresh(
    interval=REFRESH_RATE * 1000,
    key="global_refresh",
)


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.header("Tables")

page = st.sidebar.radio(
    "Select table",
    [
        "Spreads",
        "Fair Values",
        "Index Arb",
        "Open Interest",
        "Curves",
        "Price Mapping",
        "Market Data",
    ],
    index=0,
)


# ============================================================
# PAGE ROUTING
# ============================================================

try:

    if page == "Spreads":

        render_spreads_page()

    elif page == "Fair Values":

        render_fair_values_page()

    elif page == "Index Arb":

        render_index_arb_page()

    elif page == "Open Interest":

        render_open_interest_page()

    elif page == "Curves":

        render_curves_page()

    elif page == "Price Mapping":

        render_price_mapping_page()

    elif page == "Market Data":

        render_market_data_page()


except Exception as e:

    st.error(
        "Failed to load page."
    )

    st.exception(e)


# ============================================================
# FOOTER
# ============================================================

st.caption(
    f"Auto-refresh: {REFRESH_RATE} seconds"
)