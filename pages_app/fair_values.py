from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from pages_app.models import calculate_all_models, read_table, to_float


# ============================================================
# SETTINGS
# ============================================================

INPUT_TABLE = "b1_fvpage_input"
DISPLAY_SETTINGS_TABLE = "b1_displ_settings"

DEFAULT_EDGE_DECIMALS = 2

OUTPUT_COLUMNS = [
    "Contract",
    "Edge",
    "| Edge % |",
    "| Edge Ann. |",
    "| ROC |",
    "| RAR |",
    "ts_tot",
    "md_tot",
    "ndays",
]

PCT_COLUMNS = [
    "| Edge % |",
    "| Edge Ann. |",
    "| ROC |",
    "| RAR |",
]


# ============================================================
# HELPERS
# ============================================================

def clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def fmt_num(value: Any, decimals: int = 2) -> str:
    x = to_float(value)
    return "" if x is None else f"{x:,.{decimals}f}"


def fmt_pct(value: Any, decimals: int = 1) -> str:
    x = to_float(value)
    return "" if x is None else f"{x:.{decimals}%}"


def fmt_int(value: Any) -> str:
    x = to_float(value)
    return "" if x is None else f"{int(round(x))}"


# ============================================================
# DISPLAY SETTINGS
# ============================================================

def load_display_settings() -> dict[str, int]:
    """
    Load Edge decimal settings from b1_displ_settings.

    Expected columns:
        und_moex
        spd_dec

    Example:
        GD -> 1
        KC -> 3
        NG -> 3
        BT -> 0

    If a contract has no matching setting,
    DEFAULT_EDGE_DECIMALS is used.
    """
    try:
        df = read_table(DISPLAY_SETTINGS_TABLE)
    except Exception:
        return {}

    if df.empty:
        return {}

    if "und_moex" not in df.columns or "spd_dec" not in df.columns:
        return {}

    settings: dict[str, int] = {}

    for _, row in df.iterrows():
        und = clean_text(row.get("und_moex")).upper()
        decimals = to_float(row.get("spd_dec"))

        if not und or decimals is None:
            continue

        try:
            decimals_int = int(decimals)
        except (TypeError, ValueError):
            continue

        # Prevent accidental invalid formatting settings.
        if decimals_int < 0:
            continue

        settings[und] = decimals_int

    return settings


def get_edge_decimals(
    contract: Any,
    display_settings: dict[str, int],
) -> int:
    """
    Find the display setting for a contract.

    Uses the longest matching configured underlying prefix.

    Examples:
        GDZ6 -> GD
        NGX6 -> NG
        BTZ6 -> BT

    If there is no match -> 2 decimals.
    """
    contract_text = clean_text(contract).upper()

    if contract_text.startswith("MX:"):
        contract_text = contract_text[3:]

    if not contract_text:
        return DEFAULT_EDGE_DECIMALS

    # Longest prefix first, so this remains safe if one day
    # both e.g. "S" and "SF" exist in the settings table.
    for und in sorted(
        display_settings.keys(),
        key=len,
        reverse=True,
    ):
        if contract_text.startswith(und):
            return display_settings[und]

    return DEFAULT_EDGE_DECIMALS


def fmt_edge(
    value: Any,
    contract: Any,
    display_settings: dict[str, int],
) -> str:
    """
    Format Edge using b1_displ_settings.spd_dec.

    Keeps trailing zeroes intentionally:
        decimals = 3 -> 1.200
        decimals = 1 -> 1.2
        decimals = 0 -> 1
    """
    x = to_float(value)

    if x is None:
        return ""

    decimals = get_edge_decimals(
        contract,
        display_settings,
    )

    return f"{x:,.{decimals}f}"


# ============================================================
# MODEL DATA
# ============================================================

def build_model_lookup() -> dict[str, dict]:
    """
    Combine all model outputs into one:

        contract_moex -> result
    """
    models = calculate_all_models()

    lookup: dict[str, dict] = {}

    for df in models.values():

        if df.empty or "contract_moex" not in df.columns:
            continue

        for _, row in df.iterrows():

            contract = clean_text(
                row.get("contract_moex")
            )

            if contract:
                lookup[contract] = row.to_dict()

    return lookup


# ============================================================
# FAIR VALUES TABLE
# ============================================================

def build_fair_values_table() -> pd.DataFrame:
    """
    Build Fair Values in b1_fvpage_input order.

    Rules:

        GDZ6
            -> lookup MX:GDZ6

        ***Precious
            -> section row displayed as Precious

    The displayed Contract remains without MX:.
    """
    inp = read_table(INPUT_TABLE)

    if inp.empty:
        return pd.DataFrame(
            columns=OUTPUT_COLUMNS + ["_section"]
        )

    if "contract" not in inp.columns:
        raise ValueError(
            f'{INPUT_TABLE} must contain a column named "contract".'
        )

    model_lookup = build_model_lookup()

    rows: list[dict] = []

    for raw_contract in inp["contract"].tolist():

        item = clean_text(raw_contract)

        if not item:
            continue

        # ----------------------------------------------------
        # SECTION ROW
        # ----------------------------------------------------

        if item.startswith("***"):

            section_name = item[3:].strip()

            rows.append({
                "Contract": section_name,
                "_section": True,
            })

            continue

        # ----------------------------------------------------
        # CONTRACT ROW
        # ----------------------------------------------------

        lookup_contract = (
            item
            if item.startswith("MX:")
            else f"MX:{item}"
        )

        result = model_lookup.get(
            lookup_contract,
            {},
        )

        display_contract = (
            item[3:]
            if item.startswith("MX:")
            else item
        )

        rows.append({
            "Contract": display_contract,

            "Edge":
                result.get("Edge"),

            "| Edge % |":
                result.get("| Edge % |"),

            "| Edge Ann. |":
                result.get("| Edge Ann. |"),

            "| ROC |":
                result.get("| ROC |"),

            "| RAR |":
                result.get("| RAR |"),

            "ts_tot":
                result.get("ts_tot"),

            "md_tot":
                result.get("md_tot"),

            "ndays":
                result.get("ndays"),

            "_section": False,
        })

    return pd.DataFrame(rows)


# ============================================================
# DISPLAY FORMATTING
# ============================================================

def format_for_display(
    raw: pd.DataFrame,
    display_settings: dict[str, int],
) -> pd.DataFrame:

    shown = raw.reindex(
        columns=OUTPUT_COLUMNS
    ).copy()

    # --------------------------------------------------------
    # EDGE
    #
    # Custom number of decimals from:
    # b1_displ_settings.spd_dec
    # --------------------------------------------------------

    if "Edge" in shown.columns:

        shown["Edge"] = [
            fmt_edge(
                edge,
                contract,
                display_settings,
            )
            for edge, contract in zip(
                shown["Edge"],
                shown["Contract"],
            )
        ]

    # --------------------------------------------------------
    # PERCENTAGES
    # --------------------------------------------------------

    for col in PCT_COLUMNS:

        if col in shown.columns:
            shown[col] = shown[col].map(
                lambda x: fmt_pct(x, 1)
            )

    # --------------------------------------------------------
    # NDAYS
    # --------------------------------------------------------

    if "ndays" in shown.columns:
        shown["ndays"] = shown["ndays"].map(
            fmt_int
        )

    return shown


# ============================================================
# STYLING
# ============================================================

def style_fair_values(
    shown: pd.DataFrame,
    section_mask: pd.Series,
):
    """
    Style contracts/statuses and make section rows distinct.
    """

    def style_rows(row):

        idx = row.name

        if bool(section_mask.loc[idx]):

            return [
                (
                    "font-weight: bold; "
                    "background-color: "
                    "rgba(128, 128, 128, 0.18)"
                )
            ] * len(row)

        return [""] * len(row)

    def color_bool(value):

        if value is True:
            return (
                "color: green; "
                "font-weight: bold"
            )

        if value is False:
            return (
                "color: red; "
                "font-weight: bold"
            )

        return ""

    styler = shown.style.apply(
        style_rows,
        axis=1,
    )

    # Center all values and column headers
    styler = styler.set_properties(
        **{
            "text-align": "center",
            "vertical-align": "middle",
        }
    )

    styler = styler.set_table_styles(
        [
            {
                "selector": "th",
                "props": [
                    ("text-align", "center"),
                    ("vertical-align", "middle"),
                ],
            }
        ]
    )

    # Contract bold
    if "Contract" in shown.columns:

        styler = styler.set_properties(
            subset=["Contract"],
            **{
                "font-weight": "bold",
            },
        )

    # Status colors
    for col in ["ts_tot", "md_tot"]:

        if col in shown.columns:

            styler = styler.map(
                color_bool,
                subset=[col],
            )

    return styler


# ============================================================
# PAGE
# ============================================================

def render_fair_values_page():

    st.title("Fair Values")

    try:

        # -----------------------------------------------
        # Build model results
        # -----------------------------------------------

        raw = build_fair_values_table()

        # -----------------------------------------------
        # Load custom display settings
        # -----------------------------------------------

        display_settings = load_display_settings()

    except Exception as exc:

        st.error(
            f"Fair Values calculation failed: {exc}"
        )

        return

    if raw.empty:

        st.info(
            "No rows in b1_fvpage_input."
        )

        return

    # Section rows
    section_mask = (
        raw["_section"]
        .fillna(False)
        .astype(bool)
    )

    # Format values
    shown = format_for_display(
        raw,
        display_settings,
    )

    # --------------------------------------------------------
    # TABLE HEIGHT
    #
    # Make the table tall enough to display every row
    # without Streamlit's internal vertical scrollbar.
    # --------------------------------------------------------

    table_height = 38 + len(shown) * 35

    # --------------------------------------------------------
    # DISPLAY
    #
    # width="content" prevents the table stretching across
    # the entire Streamlit page.
    # --------------------------------------------------------

    st.dataframe(
        style_fair_values(
            shown,
            section_mask,
        ),
        width="content",
        height=table_height,
        hide_index=True,
    )