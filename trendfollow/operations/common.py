"""Shared operations data conversion and local clock."""

import json
from zoneinfo import ZoneInfo
import pandas as pd

KST = ZoneInfo("Asia/Seoul")


def json_rows(frame):
    return json.loads(
        frame.to_json(orient="records", date_format="iso", force_ascii=False)
    )


def read_csv(path):
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(
            path, dtype={"symbol": str, "benchmark": str}, keep_default_na=True
        )
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
