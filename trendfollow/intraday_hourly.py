"""Hourly, provisional KIS quote checkpoints; no account or order effects."""

from datetime import datetime, time, timedelta

from .intraday import stamp


def parsed(value):
    return value if isinstance(value, datetime) else stamp(value)


class HourlyQuotes:
    def __init__(self, session, targets):
        self.session = session
        self.targets = set(targets)
        self.rows = {}

    @staticmethod
    def bucket(at, market):
        local = parsed(at)
        return (local.replace(minute=0, second=0, microsecond=0), market)

    def tick(self, tick, received_at, basis_mismatch=False):
        symbol = tick["symbol"]
        at = stamp(tick["timestamp"])
        received = stamp(received_at)
        if symbol not in self.targets or str(at.date()) != self.session or str(received.date()) != self.session:
            return
        clock = at.time().replace(tzinfo=None)
        if not ((time(9) <= clock <= time(15, 30) and tick["market"] == "krx") or
                (time(15, 40) <= clock < time(20) and tick["market"] == "nxt_after")):
            return
        if not -2 <= (received - at).total_seconds() <= 60:
            return
        bucket, market = self.bucket(at, tick["market"])
        key = (bucket, market, symbol)
        previous = self.rows.get(key)
        if previous and at < stamp(previous["timestamp"]):
            return
        self.rows[key] = {
            "hour": bucket.isoformat(), "market": market, "symbol": symbol,
            "timestamp": at.isoformat(), "received_at": received.isoformat(),
            "price": tick["price"], "cumulative_volume": tick["cumulative_volume"],
            "previous_close": tick["previous_close"],
            "price_basis_mismatch": basis_mismatch,
            "breakout_present": previous.get("breakout_present") if previous else None,
            "volume_pass": previous.get("volume_pass") if previous else None,
            "entry_confirmed": previous.get("entry_confirmed") if previous else None,
            "decision_at": previous.get("decision_at") if previous else None,
        }

    def decision(self, event):
        if event.get("kind") != "signal_state" or event.get("symbol") not in self.targets:
            return
        at = stamp(event["timestamp"])
        key = (*self.bucket(at, event.get("market")), event["symbol"])
        row = self.rows.get(key)
        if row is None:
            candidates = [item for (hour, market, symbol), item in self.rows.items()
                          if symbol == event["symbol"] and market == event.get("market")
                          and 0 <= (at - stamp(item["timestamp"])).total_seconds() <= 60]
            if not candidates:
                return
            row = max(candidates, key=lambda item: item["timestamp"])
        row.update({field: event.get(field) for field in
                    ("breakout_present", "volume_pass", "entry_confirmed")})
        row["decision_at"] = at.isoformat()

    def basis(self, tick, mismatch):
        key = (*self.bucket(tick["timestamp"], tick["market"]), tick["symbol"])
        if key in self.rows:
            self.rows[key]["price_basis_mismatch"] = mismatch

    def snapshot(self, now, connected):
        now = parsed(now)
        result = []
        for (hour, market, _), row in sorted(self.rows.items()):
            close = hour + timedelta(hours=1)
            if market == "krx" and hour.hour == 15:
                close = hour.replace(minute=30)
            cutoff = min(now, close)
            age = (cutoff - stamp(row["received_at"])).total_seconds()
            current = now < close
            valid = 0 <= age <= 60 and (connected or not current)
            result.append({**row, "valid": valid,
                           "sample_status": ("잠정 관측" if valid else "관측 공백 · 판단 보류")})
        return result
