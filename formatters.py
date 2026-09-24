import pandas as pd


def fmt_auto(x):
    if pd.isna(x):
        return ""

    x = float(x)

    if x.is_integer():
        return f"{int(x):,}"

    return f"{x:,.10f}".rstrip("0").rstrip(".")
