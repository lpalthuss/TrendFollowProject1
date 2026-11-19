"""Resolve stock names and codes from already downloaded market lists."""

from pathlib import Path
import re

import pandas as pd


def symbol_names(*files):
    """Later files override earlier names, so an account keeps its own label."""
    names = {}
    for file in files:
        path = Path(file)
        if not path.is_file():
            continue
        frame = pd.read_csv(path, usecols=['symbol', 'name'], dtype=str,
                            keep_default_na=False, encoding='utf-8-sig')
        for symbol, name in frame[['symbol', 'name']].itertuples(index=False, name=None):
            code = symbol.strip().upper()
            if re.fullmatch(r'[0-9A-Z]{6}', code) and name.strip():
                names[code] = name.strip()
    return names


def matching_symbols(names, query):
    term = query.strip().casefold()
    if not term:
        return []
    matches = [code for code, name in names.items()
               if term in code.casefold() or term in name.casefold()]
    return sorted(matches, key=lambda code: (
        0 if code.casefold() == term or names[code].casefold() == term else
        1 if code.casefold().startswith(term) or names[code].casefold().startswith(term) else 2,
        names[code], code))


def named_rows(rows, names):
    result = []
    for row in rows:
        item = dict(row)
        code = str(item.get('symbol') or '').upper()
        if code in names and not item.get('name'):
            item = {'symbol': item['symbol'], 'name': names[code],
                    **{field: value for field, value in item.items() if field != 'symbol'}}
        result.append(item)
    return result
