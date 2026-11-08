"""Event-driven long-only research simulation. Uses adjusted vendor prices, not broker fills."""
from dataclasses import asdict, replace
import math

import numpy as np
import pandas as pd

from .risk import Account, Limits, allocate
from .strategy import Rules
from .relative_strength import mansfield_benchmark_symbol, mansfield_weekly
from .setup import sector_features, setup_features


def clean_research_prices(prices):
    p = prices.copy()
    if not {'symbol','date','open','high','low','close','volume'}.issubset(p):
        raise ValueError('일봉 필드가 누락되었습니다.')
    p['symbol'] = p.symbol.astype(str)
    p['date'] = pd.to_datetime(p.date, format='%Y-%m-%d', errors='raise')
    if p.duplicated(['symbol','date']).any():
        raise ValueError('중복 일봉은 자동 제거하지 않습니다.')
    cols = ['open','high','low','close','volume']
    p[cols] = p[cols].apply(pd.to_numeric, errors='raise')
    bad = (~np.isfinite(p[cols]).all(axis=1) | (p[cols[:-1]] <= 0).any(axis=1) | (p.volume < 0)
           | (p.high < p[['open','close','low']].max(axis=1))
           | (p.low > p[['open','close','high']].min(axis=1)) | p.date.isna())
    return p[~bad].sort_values(['symbol','date']).reset_index(drop=True), p[bad].copy()


def features(prices, universe, rules=Rules(), exit_days=20):
    """Vectorized daily features; no full-sample rank or unfinished-week values."""
    rules.validate()
    p, rejected = clean_research_prices(prices)
    frames = {s:f.set_index('date') for s,f in p.groupby('symbol')}
    required_mrs = {mansfield_benchmark_symbol(item, rules) for item in universe.to_dict('records')}
    if rules.mansfield_benchmark == 'market_index' and required_mrs-set(frames):
        raise ValueError('Mansfield 지수 일봉이 없습니다: '+','.join(sorted(required_mrs-set(frames)))+'. index-data로 수집하세요.')
    output = []
    needed = max(rules.rs_days+1,(rules.weekly_ma+rules.weekly_slope+2)*5,
                 rules.breakout_days+1 if rules.entry_method=='breakout' else 1,rules.atr_days,21)
    for item in universe.to_dict('records'):
        f, b = frames.get(str(item['symbol'])), frames.get(str(item['benchmark']))
        if f is None or b is None:
            continue
        f = f.copy()
        previous = f.close.shift()
        tr = pd.concat([f.high-f.low,(f.high-previous).abs(),(f.low-previous).abs()],axis=1).max(axis=1)
        atr = np.full(len(f),np.nan)
        if len(f) >= rules.atr_days:
            atr[rules.atr_days-1] = tr.iloc[:rules.atr_days].mean()
            for i in range(rules.atr_days,len(f)):
                atr[i] = (atr[i-1]*(rules.atr_days-1)+tr.iloc[i])/rules.atr_days
        f['atr'] = atr
        f['return_126d'] = f.close.pct_change(rules.rs_days,fill_method=None)
        f['excess_return'] = f.return_126d-b.close.pct_change(rules.rs_days,fill_method=None).reindex(f.index)
        mrs_benchmark = mansfield_benchmark_symbol(item, rules)
        mb = frames[mrs_benchmark]
        mansfield = mansfield_weekly(f.close, mb.close.reindex(b.index), rules.mansfield_weeks).reindex(f.index, method='ffill')
        f['mansfield_rs'] = mansfield.mansfield_rs
        f['mansfield_change_1w'] = mansfield.mansfield_change_1w
        # A stale/missing index session must not make yesterday's ratio look current.
        mrs_session_available = pd.Series(True,index=mb.index).reindex(f.index).eq(True)
        f.loc[~mrs_session_available,['mansfield_rs','mansfield_change_1w']] = np.nan
        f['mansfield_benchmark'] = mrs_benchmark
        f['rs_method'] = rules.rs_method
        weekly = f.close.resample('W-FRI').last().dropna()
        wma = weekly.rolling(rules.weekly_ma).mean()
        trend = (weekly>wma)&(wma>wma.shift(rules.weekly_slope))
        f['weekly_trend'] = trend.reindex(f.index,method='ffill').eq(True)
        f['prior_breakout_high'] = f.high.shift().rolling(rules.breakout_days).max()
        f['volume_ratio'] = f.volume/f.volume.shift().rolling(20).mean().replace(0,np.nan)
        f['avg_turnover'] = (f.close*f.volume).shift().rolling(20).mean()
        f['stop_price'] = f.close-rules.stop_atr*f.atr
        f['trailing_channel'] = f.low.rolling(exit_days).min()
        f['entry_method']=rules.entry_method
        if rules.entry_method=='setup':
            detected=setup_features(f,rules,atr_values=f.atr,calendar=b.index)
            f=f.join(detected)
        # Continuity in benchmark sessions, not a generic weekday calendar.
        session_numbers = pd.Series(np.arange(len(b)),index=b.index).reindex(f.index)
        consecutive = session_numbers.diff().eq(1).rolling(needed-1).sum().eq(needed-1)
        f['eligible_history'] = consecutive & f.excess_return.notna()
        f['reference_price'] = f.close
        for k,v in item.items():
            f[k] = v
        output.append(f.reset_index())
    if not output:
        raise ValueError('계산 가능한 유니버스·벤치마크 데이터가 없습니다.')
    result = pd.concat(output,ignore_index=True)
    if rules.market_units_enabled:
        from .market_units import market_unit_features
        result = result.merge(market_unit_features(p,universe,rules),on=['date','symbol'],how='left',validate='one_to_one')
    if rules.entry_method=='setup':
        result=result.merge(sector_features(p,universe,rules),on=['date','symbol'],how='left',validate='one_to_one')
    result['rs_percentile'] = result.return_126d.where(result.eligible_history).groupby(result.date).rank(pct=True)*100
    common = (result.eligible_history & result.weekly_trend &
        (result.volume_ratio>=rules.volume_multiple)&
        (result.avg_turnover>=rules.min_turnover)&(result.stop_price>0)&
        ((result.close-result.stop_price)/result.close<=rules.max_stop_fraction))
    if rules.entry_method=='breakout':
        common &= result.close>result.prior_breakout_high
    else:
        common &= result.setup_entry
        if rules.sector_require_action:
            common &= result.sector_action.eq(True)
    result['signal_rs'] = common & (result.rs_percentile>=rules.min_rs) & (result.excess_return>0)
    result['signal_mansfield'] = common & (result.mansfield_rs>rules.min_mansfield)
    result['signal'] = result.signal_rs if rules.rs_method == 'percentile' else result.signal_mansfield
    result['allocation_score'] = result.rs_percentile if rules.rs_method == 'percentile' else result.mansfield_rs
    return result.sort_values(['date','symbol']).reset_index(drop=True), rejected


def simulate(prices, signals, universe, start, end, initial_cash=10_000_000,
             mode='fixed', limits=Limits(), max_entry_gap=.05, initial_state=None):
    """Execute prior-close signals at next session open, then process intraday stops.

    Costs are split equally between entry and exit; slippage is separate. Mark holdings
    at actual known prices. No forced end liquidation, no same-close entries, no leverage.
    """
    limits.validate()
    if mode not in {'fixed','progressive'} or not math.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError('mode 또는 초기 자금이 유효하지 않습니다.')
    p, _ = clean_research_prices(prices)
    first, last = pd.Timestamp(start), pd.Timestamp(end)
    if first > last:
        raise ValueError('시작일은 종료일 이후일 수 없습니다.')
    benchmarks = set(universe.benchmark.astype(str))
    sessions = sorted(p[(p.symbol.isin(benchmarks))&(p.date>=first)&(p.date<=last)].date.unique())
    if not sessions:
        raise ValueError('모의 실행할 거래일이 없습니다.')
    bars = {pd.Timestamp(d):f.set_index('symbol').to_dict('index') for d,f in p.groupby('date')}
    daily_signals = {pd.Timestamp(d):f for d,f in signals.groupby('date')}
    state = initial_state or {}
    cash = float(state.get('cash',initial_cash))
    units = int(state.get('units',limits.max_units if mode=='fixed' else 1))
    positions = {k:dict(v) for k,v in state.get('positions',{}).items()}
    pending = list(state.get('pending',[]))
    curve, trades, orders, events = [], [], [], []
    fee = limits.cost_rate/2

    def sell(symbol, day, raw_price, reason):
        nonlocal cash, units
        pos = positions.pop(symbol)
        fill = raw_price*(1-limits.exit_slippage)
        proceeds = pos['quantity']*fill*(1-fee)
        cash += proceeds
        pnl = proceeds-pos['entry_total']
        trade = dict(symbol=symbol,entry_date=pos['entry_date'],exit_date=str(day.date()),
            quantity=pos['quantity'],entry_price=pos['entry_price'],exit_price=fill,
            pnl=pnl,r_multiple=pnl/pos['initial_risk'],exit_reason=reason,success_3r=pos['success'])
        trades.append(trade)
        if mode=='progressive' and pnl<0 and not pos['success']:
            units=max(1,units-1)
            events.append(dict(date=str(day.date()),symbol=symbol,kind='loss_closed',units=units))

    for raw_day in sessions:
        day=pd.Timestamp(raw_day); today=bars.get(day,{})
        # Overnight stop gaps and missing-data exits use the next available open.
        for symbol in sorted(list(positions)):
            pos=positions[symbol]; bar=today.get(symbol)
            if bar is None or bar['volume'] <= 0:
                continue
            if bar['open']<=pos['stop_price'] or pos.get('exit_pending',False):
                if bar['high']==bar['low']:
                    pos['exit_pending']=True
                    continue
                sell(symbol,day,float(bar['open']),'gap_stop' if bar['open']<=pos['stop_price'] else 'missing_data_exit')
        # Missing next-session bars cancel the intended entry rather than defer indefinitely.
        candidates=[]
        for pending_row in pending:
            symbol=pending_row['symbol']; bar=today.get(symbol)
            reason=None
            if symbol in positions: reason='already_held'
            elif bar is None or bar['volume']<=0: reason='missing_or_untradable_open'
            elif bar['high']==bar['low']: reason='locked_single_price_bar'
            elif bar['open']>pending_row['reference_price']*(1+max_entry_gap): reason='entry_gap_limit'
            elif bar['open']<=pending_row['stop_price']: reason='open_below_stop'
            if reason:
                orders.append(dict(date=str(day.date()),symbol=symbol,status='rejected',reason=reason))
            else:
                candidates.append(dict(pending_row,reference_price=float(bar['open']),signal=True))
        if candidates:
            account_positions=[]
            for symbol,pos in positions.items():
                price=float(today[symbol]['open']) if symbol in today else pos['last_price']
                account_positions.append(dict(symbol=symbol,sector=pos['sector'],quantity=pos['quantity'],
                    current_price=price,stop_price=pos['stop_price']))
            equity=cash+sum(x['quantity']*x['current_price'] for x in account_positions)
            allocation=allocate(pd.DataFrame(candidates),Account(equity,cash,account_positions),
                replace(limits,units=limits.max_units if mode=='fixed' else units))
            for row in allocation.to_dict('records'):
                quantity=int(row['quantity']); symbol=row['symbol']
                if not quantity:
                    orders.append(dict(date=str(day.date()),symbol=symbol,status='rejected',reason=row['allocation_reason']))
                    continue
                fill=row['reference_price']*(1+limits.entry_slippage)
                entry_total=quantity*fill*(1+fee)
                if entry_total>cash+.001:
                    raise ValueError('현금 배분 불일치')
                cash-=entry_total
                initial_risk=entry_total-quantity*row['stop_price']*(1-limits.exit_slippage)*(1-fee)
                positions[symbol]=dict(sector=row['sector'],quantity=quantity,entry_date=str(day.date()),
                    entry_price=fill,entry_total=entry_total,initial_risk=initial_risk,
                    stop_price=float(row['stop_price']),target_3r=fill+3*initial_risk/quantity,
                    last_price=fill,success=False,exit_pending=False)
                orders.append(dict(date=str(day.date()),symbol=symbol,status='filled',quantity=quantity,
                                   fill_price=fill,signal_date=row['date'],effective_units=row['effective_units'],
                                   market_unit_regime=row.get('market_unit_regime','disabled')))
        pending=[]
        # A same-day low below the stop wins over a same-day high above the 3R threshold.
        for symbol in sorted(list(positions)):
            pos=positions[symbol]; bar=today.get(symbol)
            if bar is None or bar['volume']<=0:
                pos['exit_pending']=True
                continue
            if bar['high'] == bar['low']:
                # Price-limit queue is unknown: conservatively do not assume execution.
                if bar['low']<=pos['stop_price']: pos['exit_pending']=True
            elif bar['low']<=pos['stop_price']:
                sell(symbol,day,float(pos['stop_price']),'intraday_stop')
                continue
            pos['last_price']=float(bar['close'])
            if not pos['success'] and bar['close']>=pos['target_3r']:
                pos['success']=True
                if mode=='progressive':
                    units=min(limits.max_units,units+1)
                    events.append(dict(date=str(day.date()),symbol=symbol,kind='success_3r',units=units))
                pos['stop_price']=max(pos['stop_price'],pos['entry_price'])
        todays_features=daily_signals.get(day)
        if todays_features is not None:
            for row in todays_features.to_dict('records'):
                symbol=row['symbol']
                if symbol in positions and pd.notna(row['trailing_channel']):
                    positions[symbol]['stop_price']=max(positions[symbol]['stop_price'],float(row['trailing_channel']))
            score = 'allocation_score' if 'allocation_score' in todays_features else 'rs_percentile'
            selected=todays_features[todays_features.signal].sort_values([score,'symbol'],ascending=[False,True])
            pending=json_records(selected)
        marked=sum(pos['quantity']*pos['last_price'] for pos in positions.values())
        curve.append(dict(date=str(day.date()),equity=cash+marked,cash=cash,positions=len(positions),
                          units=units,stale_positions=sum(pos.get('exit_pending',False) for pos in positions.values())))
    final_state={'cash':cash,'positions':positions,'units':units,'pending':pending,
                 'last_date':str(pd.Timestamp(sessions[-1]).date()),'initial_cash':initial_cash,'mode':mode}
    return pd.DataFrame(curve),pd.DataFrame(trades),pd.DataFrame(orders),pd.DataFrame(events),final_state


def json_records(frame):
    import json
    f=frame.copy()
    if 'date' in f:
        f['date']=f.date.dt.strftime('%Y-%m-%d')
    return json.loads(f.to_json(orient='records'))


def metrics(curve, trades, initial_cash):
    equity=curve.equity.astype(float)
    equity_with_initial=pd.Series([initial_cash,*equity.tolist()])
    elapsed=max(1,(pd.Timestamp(curve.date.iloc[-1])-pd.Timestamp(curve.date.iloc[0])).days+1)
    final=float(equity.iloc[-1]); pnl=trades.pnl if not trades.empty else pd.Series(dtype=float)
    wins=float(pnl[pnl>0].sum()); losses=float(-pnl[pnl<0].sum())
    return {'initial_equity':initial_cash,'final_equity':final,'total_return':final/initial_cash-1,
            'cagr':(final/initial_cash)**(365.25/elapsed)-1,
            'max_drawdown':float((equity_with_initial/equity_with_initial.cummax()-1).min()),
            'closed_trades':len(trades),'win_rate':float((pnl>0).mean()) if len(pnl) else None,
            'profit_factor':wins/losses if losses else None,
            'realized_pnl':float(pnl.sum()),'unrealized_pnl':final-initial_cash-float(pnl.sum()),
            'mean_r':float(trades.r_multiple.mean()) if not trades.empty else None,
            'stale_position_days':int(curve.stale_positions.sum())}
