"""Inspect and extend a published daily CSV without holding the full series in memory."""
import csv
import os
from bisect import bisect_left

import pandas as pd


KEY = ["symbol", "date"]
REQUIRED = {"date", "symbol", "open", "high", "low", "close", "volume"}
CHUNK_ROWS = 50000


class MissingDailyBars(ValueError):
    def __init__(self, message, pairs):
        super().__init__(message)
        self.missing_bars = [{"symbol": symbol, "date": day} for symbol, day in sorted(set(pairs))]


def _stamp(path):
    stat = path.stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def missing_close_bars(path, symbols, trading_days, first_close):
    """Find holes from the first known bar, including the 252 sessions before the close."""
    from .indicators import HIGH_52W_SESSIONS

    days = sorted(set(trading_days))
    start = max(0, bisect_left(days, first_close) - HIGH_52W_SESSIONS)
    days = days[start:]
    saved = {symbol: set() for symbol in symbols}
    first_seen = {}
    with pd.read_csv(path, usecols=KEY, dtype=str, chunksize=CHUNK_ROWS) as reader:
        for part in reader:
            part = part[part.symbol.isin(symbols)]
            for symbol, group in part.groupby("symbol", sort=False):
                first = group.date.min()
                first_seen[symbol] = min(first_seen.get(symbol, first), first)
                saved[symbol].update(group.date[group.date.isin(days)])
    missing = {}
    for symbol in symbols:
        # A symbol without saved history needs a full initial fetch, which can
        # start at listing. Do not demand pre-listing bars.
        if symbol in first_seen:
            absent = set(days[bisect_left(days, first_seen[symbol]):]) - saved[symbol]
            if absent:
                missing[symbol] = absent
    return missing


class PriceHistory:
    def __init__(self, path, requested):
        self.path = path
        self.stamp = _stamp(path)
        self.columns = pd.read_csv(path, nrows=0).columns.tolist()
        invalid = "기존 일봉의 필드나 날짜·종목 중복을 확인하세요."
        if not REQUIRED.issubset(self.columns):
            raise ValueError(invalid)
        self.count = 0
        self.symbols = set()
        self.sorted = True
        previous = None
        pieces = []
        columns = ["date", "symbol", "close", "volume"]
        with pd.read_csv(path, usecols=columns, dtype={"date": str, "symbol": str}, chunksize=CHUNK_ROWS) as reader:
            for part in reader:
                if part.empty:
                    continue
                keys = pd.MultiIndex.from_frame(part[KEY])
                if keys.has_duplicates or previous == keys[0]:
                    raise ValueError(invalid)
                self.sorted &= keys.is_monotonic_increasing and (previous is None or previous < keys[0])
                previous = keys[-1]
                self.count += len(part)
                self.symbols.update(part.symbol.unique())
                # The source comparison also needs the last saved day before the first gap.
                dates = sorted(part.date.unique())
                keep = set(requested)
                for day in requested:
                    index = bisect_left(dates, day)
                    if index:
                        keep.add(dates[index - 1])
                pieces.append(part.loc[part.date.isin(keep)])
        self.window = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame(columns=columns)
        if not self.sorted:
            # Older or imported CSVs may not be in publication order.
            keys = pd.read_csv(path, usecols=KEY, dtype=str)
            if keys.duplicated().any():
                raise ValueError(invalid)

    def write_merged(self, handle, fresh):
        if _stamp(self.path) != self.stamp:
            raise ValueError("갱신 중 기존 일봉이 변경되었습니다. 다시 실행하세요.")
        fresh = fresh.sort_values(KEY)
        if not self.sorted or not set(fresh.columns).issubset(self.columns):
            old = pd.read_csv(self.path, dtype={"date": str, "symbol": str})
            pd.concat([old, fresh], ignore_index=True).sort_values(KEY).to_csv(handle, index=False)
            return
        # Both inputs are ordered and duplicate keys were excluded during validation.
        # Copy existing text values verbatim to avoid a float round trip.
        writer = csv.writer(handle, lineterminator=os.linesep)
        symbol, date = (self.columns.index(field) for field in KEY)
        additions = iter(fresh.reindex(columns=self.columns).fillna("").itertuples(index=False, name=None))
        row = next(additions, None)
        with self.path.open(encoding="utf-8-sig", newline="") as src:
            reader = csv.reader(src)
            writer.writerow(next(reader))
            for saved in reader:
                if not saved:
                    continue
                while row is not None and (row[symbol], row[date]) < (saved[symbol], saved[date]):
                    writer.writerow(row)
                    row = next(additions, None)
                writer.writerow(saved)
        if row is not None:
            writer.writerow(row)
            writer.writerows(additions)
