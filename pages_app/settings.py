import streamlit as st

from market_data_service import (
    get_a1_override_enabled,
    get_ib_override_status,
    set_a1_override_enabled,
)


def render_settings_page():
    st.subheader("Market Data Control")

    current = get_a1_override_enabled()
    enabled = st.toggle("IB → A1 fallback", value=current)
    if enabled != current:
        set_a1_override_enabled(enabled)
        st.rerun()

    status = get_ib_override_status()
    c1, c2, c3 = st.columns(3)
    c1.metric("IB delayed / stalled", status["bad"])
    c2.metric("Replaced from A1", status["replaced"])
    c3.metric("Still invalid", status["invalid"])

    st.caption(
        "When ON, only unusable IB prices are replaced. Raw md_snap is never modified. "
        "A1 MTM is used as bid=ask and marked delayed='a1'."
    )

    details = status["details"]
    if details is not None and not details.empty:
        with st.expander("IB fallback details", expanded=False):
            st.dataframe(details, use_container_width=True, hide_index=True)
