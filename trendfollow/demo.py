"""Reproducible synthetic data, intentionally containing breakout and rejection cases."""
from pathlib import Path
import json

import numpy as np
import pandas as pd


def generate(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    dates = pd.bdate_range(end='2026-11-06', periods=240)
    rng = np.random.default_rng(42)
    rows, symbols = [], ['DEMO01', 'DEMO02', 'DEMO03', 'DEMO04', 'BENCH1']
    for i, symbol in enumerate(symbols):
        growth = [.003, .0025, -.001, .0015, .0007][i]
        close = 10000 * np.exp(np.arange(len(dates))*growth + rng.normal(0, .002, len(dates)))
        if i < 2:
            close[-1] *= 1.05
        for j, dt in enumerate(dates):
            volume = 500000 * (3 if j == len(dates)-1 and i < 2 else 1)
            rows.append([dt.date().isoformat(), symbol, close[j]*.997,
                         close[j]*1.01, close[j]*.99, close[j], volume])
    pd.DataFrame(rows, columns=['date','symbol','open','high','low','close','volume']).to_csv(directory/'prices.csv', index=False)
    pd.DataFrame([{'symbol': symbol, 'name': f'합성종목 {i+1}',
                   'sector': '합성업종 A' if i < 2 else '합성업종 B', 'benchmark': 'BENCH1'}
                  for i, symbol in enumerate(symbols[:-1])]).to_csv(directory/'universe.csv', index=False)
    (directory/'account.json').write_text(json.dumps({'equity': 10000000, 'cash': 10000000,
                                                    'positions': []}, indent=2), encoding='utf-8')
