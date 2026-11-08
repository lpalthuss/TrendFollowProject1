"""Causal, ATR-confirmed wave setup; numerical thresholds are explicit research hypotheses."""
import numpy as np
import pandas as pd


SETUP_SETTINGS = ['entry_method','setup_reversal_atr','setup_min_rise_atr',
                  'setup_max_retracement','setup_volume_contraction','setup_range_contraction',
                  'sector_require_action','sector_min_peers','sector_min_breadth']


def setup_features(frame, rules, atr_values=None, calendar=None):
    """Recognize completed wave information only; no centered pivots or fixed base duration.

    A high is confirmed after a close retreats by reversal_atr. Base statistics exclude
    the trigger day. Four observations are needed to compare two halves, not a target
    duration. The first resistance crossing consumes a setup, even if filters reject it.
    """
    f=frame.sort_index()
    high,low,close,volume=(f[c].to_numpy(float) for c in ['high','low','close','volume'])
    previous=np.r_[np.nan,close[:-1]]
    tr=np.nanmax(np.vstack([high-low,np.abs(high-previous),np.abs(low-previous)]),axis=0)
    normalized=tr/close
    if atr_values is None:
        atr=np.full(len(f),np.nan)
        n=rules.atr_days
        if len(f)>=n:
            atr[n-1]=np.mean(tr[:n])
            for i in range(n,len(f)):
                atr[i]=(atr[i-1]*(n-1)+tr[i])/n
    else:
        atr=np.asarray(atr_values,dtype=float)
    prefix_volume=np.r_[0.,np.cumsum(volume)]
    prefix_range=np.r_[0.,np.cumsum(normalized)]
    sessions=None if calendar is None else pd.Series(np.arange(len(calendar)),index=calendar).reindex(f.index).to_numpy()
    phase='seeking'; start=peak=0; base_low=np.nan; confirmed=-1
    rows=[]
    for i,day in enumerate(f.index):
        row={'setup_ready':False,'setup_trigger':False,'setup_entry':False,
             'setup_invalidated':False,
             'setup_wave_start':None,'setup_peak_date':None,'setup_confirmed_at':None,
             'setup_wave_low':np.nan,'setup_resistance':np.nan,'setup_retracement':np.nan,
             'setup_base_bars':0,'setup_volume_ratio':np.nan,'setup_range_ratio':np.nan}
        if sessions is not None and i and (not np.isfinite(sessions[i-1:i+1]).all() or sessions[i]-sessions[i-1]!=1):
            phase='seeking';start=peak=i;confirmed=-1
        if volume[i]<=0:
            phase='seeking';start=peak=i;confirmed=-1
            row['setup_phase']='untradable'
            rows.append(row)
            continue
        if phase=='seeking':
            if low[i]<=low[start]: start=i
            if np.isfinite(atr[i]) and high[i]-low[start]>=rules.setup_min_rise_atr*atr[i]:
                peak=i;phase='rising'
        elif phase=='rising':
            if high[i]>high[peak]: peak=i
            if i>peak and np.isfinite(atr[i]) and high[peak]-close[i]>=rules.setup_reversal_atr*atr[i]:
                confirmed=i;phase='base';base_low=float(np.min(low[peak+1:i+1]))
                if (high[peak]-base_low)/(high[peak]-low[start])>rules.setup_max_retracement:
                    phase='seeking';start=peak=i;confirmed=-1
        else:
            first=peak+1;count=i-first
            wave=high[peak]-low[start]
            depth=(high[peak]-base_low)/wave if wave>0 else np.nan
            vr=rr=np.nan
            if count>=4:
                middle=first+count//2
                early_v=(prefix_volume[middle]-prefix_volume[first])/(middle-first)
                late_v=(prefix_volume[i]-prefix_volume[middle])/(i-middle)
                early_r=(prefix_range[middle]-prefix_range[first])/(middle-first)
                late_r=(prefix_range[i]-prefix_range[middle])/(i-middle)
                vr=late_v/early_v if early_v>0 else np.nan
                rr=late_r/early_r if early_r>0 else np.nan
            healthy=(wave>0 and np.isfinite(depth) and 0<=depth<=rules.setup_max_retracement)
            ready=bool(healthy and count>=4)
            trigger=bool(close[i]>high[peak] and close[i-1]<=high[peak])
            today_healthy=(high[peak]-min(base_low,low[i]))/wave<=rules.setup_max_retracement if wave>0 else False
            row.update(setup_ready=ready,setup_trigger=trigger,
                setup_invalidated=bool(not today_healthy),
                setup_entry=bool(ready and trigger and today_healthy and
                    vr<=rules.setup_volume_contraction and rr<=rules.setup_range_contraction),
                setup_wave_start=str(f.index[start].date()),setup_peak_date=str(f.index[peak].date()),
                setup_confirmed_at=str(f.index[confirmed].date()),setup_wave_low=float(low[start]),
                setup_resistance=float(high[peak]),setup_retracement=float(depth),
                setup_base_bars=count,setup_volume_ratio=vr,setup_range_ratio=rr)
            if not today_healthy:
                phase='seeking';start=peak=i;confirmed=-1
            elif trigger:
                # The next rising wave starts from this base, not from an ancient low.
                start=first+int(np.argmin(low[first:i])) if i>first else i
                peak=i;phase='rising';confirmed=-1
            else:
                base_low=min(base_low,low[i])
        row['setup_phase']=phase
        rows.append(row)
    return pd.DataFrame(rows,index=f.index)


def sector_features(prices,universe,rules):
    """Equal-weight leave-one-out peer breadth; universe membership is a current snapshot."""
    if 'industry_peer_history' in universe.columns:
        from .industries import industry_action_features
        return industry_action_features(prices,universe,rules)
    p=prices.copy()
    p['date']=pd.to_datetime(p.date)
    p=p.sort_values(['symbol','date'])
    p['day_return']=p.groupby('symbol').close.pct_change(fill_method=None)
    p['previous_date']=p.groupby('symbol').date.shift()
    stocks=p.merge(universe[['symbol','sector','benchmark']],on='symbol',validate='many_to_one')
    benchmarks=p[p.symbol.isin(set(universe.benchmark))][['date','symbol','day_return','previous_date']].rename(
        columns={'symbol':'benchmark','day_return':'benchmark_day_return','previous_date':'benchmark_previous_date'})
    stocks=stocks.merge(benchmarks,on=['date','benchmark'],how='left',validate='many_to_one')
    valid=(stocks.previous_date==stocks.benchmark_previous_date)&(stocks.volume>0)&stocks.day_return.notna()&stocks.benchmark_day_return.notna()
    stocks['peer_return_input']=stocks.day_return.where(valid)
    stocks['peer_excess_input']=(stocks.day_return-stocks.benchmark_day_return).where(valid)
    stocks['peer_up_input']=stocks.day_return.gt(0).astype(float).where(valid)
    group=stocks.groupby(['date','sector'])
    count=group.peer_return_input.transform('count')-valid.astype(int)
    up=(group.peer_up_input.transform('sum')-stocks.peer_up_input.fillna(0))/count.replace(0,np.nan)
    excess=(group.peer_excess_input.transform('sum')-stocks.peer_excess_input.fillna(0))/count.replace(0,np.nan)
    stocks['sector_peer_count']=count
    stocks['sector_up_fraction']=up
    stocks['sector_excess_return']=excess
    stocks['sector_action']=(count>=rules.sector_min_peers)&(up>=rules.sector_min_breadth)&(excess>0)
    stocks['sector_action_scope']='candidate_universe'
    return stocks[['date','symbol','sector_peer_count','sector_up_fraction','sector_excess_return','sector_action','sector_action_scope']]
