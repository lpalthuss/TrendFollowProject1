"""Self-contained Streamlit chart for saved daily OHLCV bars."""

from functools import lru_cache
import json
import math
from pathlib import Path


_HERE = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def _assets():
    template = (_HERE / "price_chart.html").read_text(encoding="utf-8")
    library = (_HERE / "vendor" / "lightweight-charts-5.3.0.min.js").read_text(encoding="utf-8")
    return template, library


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def chart_html(prices):
    """Render a local chart bundle; no browser CDN or live market request is needed."""
    rows = []
    for item in prices:
        day = str(item.get("date", ""))[:10]
        if len(day) != 10:
            continue
        row = {"time": day}
        for field in ("open", "high", "low", "close", "volume", "volume_ratio", "atr",
                      "atr_up_multiple", "atr_down_multiple"):
            row[field] = _number(item.get(field))
        if all(row[field] is not None and row[field] > 0 for field in ("open", "high", "low", "close")):
            rows.append(row)
    rows.sort(key=lambda row: row["time"])
    payload = json.dumps(rows, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template, library = _assets()
    return template.replace("/*__LIGHTWEIGHT_CHARTS__*/", library).replace("/*__CHART_DATA__*/", payload)
