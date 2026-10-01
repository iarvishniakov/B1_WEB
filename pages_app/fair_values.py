from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from pages_app.models import (
    ModelContext,
    calculate_all_models,
    load_live_data,
    load_reference_data,
    read_table,
    to_float,
)

INPUT_TABLE = "b1_fvpage_input"
DISPLAY_SETTINGS_TABLE = "b1_displ_settings"
DEFAULT_EDGE_DECIMALS = 2

BASE_COLUMNS = [
    "Contract", "Edge", "| Edge % |", "| Edge Ann. |", "| ROC |", "| RAR |",
    "ts_tot", "md_tot", "ndays",
]
DETAIL_COLUMNS = ["MTM", "FV", "src contracts", "src weights / rate", "src prices / div"]

# Text displayed in the detail cells of each section row.
# These are deliberately hard-coded because one section may contain mixed models.
SECTION_DETAIL_HEADERS = {
    "Precious":       ["MTM", "FV", "src contracts", "src weights", "src prices"],
    "Soft":           ["MTM", "FV", "src contracts", "src weights", "src prices"],
    "Index":          ["MTM", "FV", "src contracts", "rate",        "div"],
    "Nat Gas":        ["MTM", "FV", "src contracts", "src weights", "src prices"],
    "Crypto futures": ["MTM", "FV", "src contracts", "src weights", "src prices"],
    "Crypto ETFs":    ["MTM", "FV", "src contracts", "rate",        "div"],
}
PCT_COLUMNS = ["| Edge % |", "| Edge Ann. |", "| ROC |", "| RAR |"]


def clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def fmt_pct(value: Any, decimals: int = 1) -> str:
    x = to_float(value)
    return "" if x is None else f"{x:.{decimals}%}"


def fmt_int(value: Any) -> str:
    x = to_float(value)
    return "" if x is None else f"{int(round(x))}"


def fmt_compact(value: Any, max_decimals: int = 4) -> str:
    x = to_float(value)
    if x is None:
        return ""
    s = f"{x:.{max_decimals}f}".rstrip("0").rstrip(".")
    return "0" if s in {"-0", ""} else s


def load_display_settings() -> dict[str, int]:
    try:
        df = read_table(DISPLAY_SETTINGS_TABLE)
    except Exception:
        return {}
    if df.empty or "und_moex" not in df.columns or "spd_dec" not in df.columns:
        return {}

    out: dict[str, int] = {}
    for _, row in df.iterrows():
        und = clean_text(row.get("und_moex")).upper()
        dec = to_float(row.get("spd_dec"))
        if und and dec is not None and int(dec) >= 0:
            out[und] = int(dec)
    return out


def edge_decimals(contract: Any, settings: dict[str, int]) -> int:
    c = clean_text(contract).upper()
    if c.startswith("MX:"):
        c = c[3:]
    for und in sorted(settings, key=len, reverse=True):
        if c.startswith(und):
            return settings[und]
    return DEFAULT_EDGE_DECIMALS


def fmt_edge(value: Any, contract: Any, settings: dict[str, int]) -> str:
    x = to_float(value)
    if x is None:
        return ""
    dec = edge_decimals(contract, settings)
    return f"{x:,.{dec}f}"


def build_model_lookup() -> tuple[dict[str, dict], ModelContext]:
    """One lookup for calculated model rows, plus live MD context for mirror source price."""
    models = calculate_all_models()
    lookup: dict[str, dict] = {}
    for df in models.values():
        if df.empty or "contract_moex" not in df.columns:
            continue
        for _, row in df.iterrows():
            contract = clean_text(row.get("contract_moex"))
            if contract:
                lookup[contract] = row.to_dict()

    # Mirror output does not expose MTM_INTL, so use the same Models market-data
    # layer to obtain the source contract MTM without duplicating pricing logic.
    ctx = ModelContext(load_reference_data(), load_live_data())
    return lookup, ctx


def model_details(result: dict, ctx: ModelContext) -> dict[str, Any]:
    model = clean_text(result.get("model")).lower()
    details = {c: "" for c in DETAIL_COLUMNS}
    details["MTM"] = result.get("MTM")
    details["FV"] = result.get("FV")

    if model == "curve":
        c1, c2 = clean_text(result.get("c1")), clean_text(result.get("c2"))
        details["src contracts"] = "     ".join(x for x in [c1, c2] if x)

        w1, w2 = to_float(result.get("w1")), to_float(result.get("w2"))
        weights = []
        if w1 is not None:
            weights.append(f"{w1:.2f}")
        if w2 is not None:
            weights.append(f"{w2:.2f}")
        details["src weights / rate"] = "     ".join(weights)

        p1, p2 = result.get("p1"), result.get("p2")
        details["src prices / div"] = "     ".join(
            x for x in [fmt_compact(p1), fmt_compact(p2)] if x
        )

    elif model == "mirror":
        contract_intl = clean_text(result.get("contract_intl"))
        details["src contracts"] = contract_intl
        px = ctx.md.get(contract_intl) if contract_intl else None
        details["src prices / div"] = "" if px is None else px.mtm

    elif model == "mirror+fx":
        contract_intl = clean_text(result.get("contract_intl"))
        c1, c2 = clean_text(result.get("c1")), clean_text(result.get("c2"))
        details["src contracts"] = "     ".join(
            x for x in [contract_intl, c1, c2] if x
        )

        w1, w2 = to_float(result.get("w1")), to_float(result.get("w2"))
        weights = []
        if w1 is not None:
            weights.append(f"{w1:.2f}")
        if w2 is not None:
            weights.append(f"{w2:.2f}")
        details["src weights / rate"] = "     ".join(weights)

        details["src prices / div"] = "     ".join(
            x for x in [fmt_compact(result.get("MTM_INTL")), fmt_compact(result.get("FX"))] if x
        )

    elif model == "etf":
        details["src weights / rate"] = result.get("r")
        details["src prices / div"] = result.get("d")

    return details


def build_fair_values_table() -> pd.DataFrame:
    inp = read_table(INPUT_TABLE)
    if inp.empty:
        return pd.DataFrame(columns=BASE_COLUMNS + DETAIL_COLUMNS + ["_section", "_model"])
    if "contract" not in inp.columns:
        raise ValueError(f'{INPUT_TABLE} must contain a column named "contract".')

    model_lookup, ctx = build_model_lookup()
    rows: list[dict] = []

    for raw_contract in inp["contract"].tolist():
        item = clean_text(raw_contract)
        if not item:
            continue

        if item.startswith("***"):
            section_name = item[3:].strip()
            row = {c: None for c in BASE_COLUMNS + DETAIL_COLUMNS}
            row.update({"Contract": section_name, "_section": True, "_model": ""})

            # When details are expanded, the section row itself acts as the
            # repeated header for the right-hand detail block.
            hdr = SECTION_DETAIL_HEADERS.get(
                section_name,
                ["MTM", "FV", "src contracts", "src weights", "src prices"],
            )
            for col, label in zip(DETAIL_COLUMNS, hdr):
                row[col] = label

            rows.append(row)
            continue

        lookup_contract = item if item.startswith("MX:") else f"MX:{item}"
        result = model_lookup.get(lookup_contract, {})
        display_contract = item[3:] if item.startswith("MX:") else item
        details = model_details(result, ctx) if result else {c: "" for c in DETAIL_COLUMNS}

        rows.append({
            "Contract": display_contract,
            "Edge": result.get("Edge"),
            "| Edge % |": result.get("| Edge % |"),
            "| Edge Ann. |": result.get("| Edge Ann. |"),
            "| ROC |": result.get("| ROC |"),
            "| RAR |": result.get("| RAR |"),
            "ts_tot": result.get("ts_tot"),
            "md_tot": result.get("md_tot"),
            "ndays": result.get("ndays"),
            **details,
            "_section": False,
            "_model": clean_text(result.get("model")),
        })

    return pd.DataFrame(rows)


def format_for_display(raw: pd.DataFrame, show_details: bool) -> pd.DataFrame:
    settings = load_display_settings()
    cols = BASE_COLUMNS + (DETAIL_COLUMNS if show_details else [])
    shown = raw.reindex(columns=cols).copy()

    shown["Edge"] = [fmt_edge(v, c, settings) for v, c in zip(shown["Edge"], shown["Contract"])]
    for col in PCT_COLUMNS:
        shown[col] = shown[col].map(lambda x: fmt_pct(x, 1))
    shown["ndays"] = shown["ndays"].map(fmt_int)

    if show_details:
        shown["MTM"] = shown["MTM"].map(fmt_compact)
        shown["FV"] = shown["FV"].map(fmt_compact)

        # Keep pre-composed source strings untouched; format scalar ETF/mirror values.
        for col in ["src weights / rate", "src prices / div"]:
            shown[col] = shown[col].map(
                lambda v: v if isinstance(v, str) else fmt_compact(v)
            )

    return shown


def style_fair_values(shown: pd.DataFrame, section_mask: pd.Series):
    def style_rows(row):
        if bool(section_mask.loc[row.name]):
            return ["font-weight: bold; background-color: rgba(128,128,128,0.18)"] * len(row)
        return [""] * len(row)

    def color_bool(value):
        if value is True:
            return "color: green; font-weight: bold"
        if value is False:
            return "color: red; font-weight: bold"
        return ""

    styler = shown.style.apply(style_rows, axis=1)
    styler = styler.set_properties(subset=["Contract"], **{"font-weight": "bold"})
    if "| RAR |" in shown.columns:
        styler = styler.set_properties(subset=["| RAR |"], **{"font-weight": "bold"})
    for col in ["ts_tot", "md_tot"]:
        if col in shown.columns:
            styler = styler.map(color_bool, subset=[col])
    return styler


def render_fair_values_page():
    st.title("Fair Values")

    # Compact by default; one click expands the source/model detail columns.
    show_details = st.toggle("Show model details", value=False)

    try:
        raw = build_fair_values_table()
    except Exception as exc:
        st.error(f"Fair Values calculation failed: {exc}")
        return

    if raw.empty:
        st.info("No rows in b1_fvpage_input.")
        return

    section_mask = raw["_section"].fillna(False).astype(bool)
    shown = format_for_display(raw, show_details)
    table_height = 38 + len(shown) * 35

    st.dataframe(
        style_fair_values(shown, section_mask),
        width="content",
        height=table_height,
        hide_index=True,
    )
