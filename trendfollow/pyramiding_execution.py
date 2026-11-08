"""Shared current-equity risk allocation for initial buys and up to three additions."""
import math

import pandas as pd

from .initial_stop import initial_stop
from .pyramiding import setup_identity,is_true
from .risk import MAX_POSITION_RISK

LOT_COLUMNS=['date','symbol','number','signal_date','quantity','entry_price','entry_total',
    'initial_risk','initial_stop_price','signal_atr','setup_id']
RISK_COLUMNS=['date','symbol','equity','quantity','current_price','common_stop','planned_stop_risk',
    'operating_risk_limit','hard_risk_limit','operating_risk_exceeded','hard_risk_exceeded',
    'portfolio_stop_risk','portfolio_risk_limit','portfolio_risk_exceeded','stale_price']


def stop_risk(price,stop,limits,entering=False):
    """Current-mark drawdown for holdings; actual entry cost for new shares. No profit credit."""
    value=price*(1+limits.entry_slippage)*(1+limits.cost_rate/2) if entering else price
    return max(0.,value-stop*(1-limits.exit_slippage)*(1-limits.cost_rate/2))


def allocate_campaigns(candidates,account,limits):
    account.validate();limits.validate()
    f=candidates.copy()
    for key in ['quantity','effective_units']:f[key]=0
    for key in ['planned_risk','cash_required','combined_position_risk','portfolio_risk_after',
                'existing_stop_risk_after','per_trade_budget','position_risk_limit','portfolio_risk_limit',
                'equity_at_allocation','common_stop_before','stop_price','estimated_entry_price']:
        f[key]=f[key].astype(float) if key=='stop_price' and key in f else 0.
    f['allocation_reason']='not_a_signal'
    holdings={str(p['symbol']):dict(p) for p in account.positions}
    risks={s:p['quantity']*stop_risk(p['current_price'],p['stop_price'],limits) for s,p in holdings.items()}
    sectors={}
    for p in holdings.values():sectors[p['sector']]=sectors.get(p['sector'],0)+p['quantity']*p['current_price']
    reserved=account.pending_cash or account.pending_risk or account.pending_symbols
    blocked=('pending_orders_require_reconciliation' if reserved else
        'stale_position_price' if any(p.get('price_stale') for p in holdings.values()) else
        'existing_stop_breached_review_exit_first' if any(p['current_price']<=p['stop_price'] for p in holdings.values()) else None)
    if blocked:
        f.loc[f.signal,'allocation_reason']=blocked;return f
    cash=max(0.,account.cash-account.equity*limits.cash_reserve)
    equity=account.equity
    visited=set();score='allocation_score' if 'allocation_score' in f else 'rs_percentile'
    for i,row in f.sort_values([score,'symbol'],ascending=[False,True]).iterrows():
        if not is_true(row.signal):continue
        symbol=str(row.symbol);pos=holdings.get(symbol);adding=row.get('order_intent')=='add'
        reason=None
        if not (is_true(row.get('pyramiding_enabled',False)) or is_true(row.get('campaign_risk_enabled',False))):reason='mixed_execution_modes'
        elif adding and not is_true(row.get('pyramiding_enabled',False)):reason='pyramiding_disabled'
        elif symbol in visited:reason='duplicate_symbol'
        elif adding and pos is None:reason='position_closed_before_addition'
        elif not adding and pos is not None:reason='already_held'
        elif not isinstance(row.sector,str) or not row.sector.strip():reason='missing_sector'
        elif adding:
            if row.sector!=pos['sector']:reason='sector_mismatch'
            elif any(k not in pos for k in ['lots','addition_count','pyramiding_filled_setups',
                    'initial_quantity','average_entry_price','total_committed_risk']):reason='missing_campaign_history'
            elif not pos['lots']:reason='missing_campaign_history'
            elif pos.get('exit_pending') or pos.get('rs_exit_pending'):reason='addition_exit_pending'
            elif not pos.get('success') or not pos.get('success_3r_date'):reason='three_r_not_reached'
            elif pos.get('addition_count',0)>=min(3,row['pyramiding_max_additions']):reason='max_additions_reached'
            elif setup_identity(row) in pos.get('pyramiding_filled_setups',[]):reason='setup_already_filled'
            elif float(row.reference_price)<pos['target_3r']:reason='below_three_r_at_open'
            elif pos['stop_price']<pos['entry_price']:reason='holding_stop_below_entry'
            elif float(row.reference_price)<=pos['lots'][-1].get('entry_price',pos['average_entry_price']):reason='not_above_last_fill'
        if reason:f.at[i,'allocation_reason']=reason;continue
        visited.add(symbol)
        cap=row.get('market_unit_cap',limits.max_units)
        if pd.isna(cap):cap=0
        if isinstance(cap,bool) or not math.isfinite(cap) or cap<0 or int(cap)!=cap:raise ValueError('시장 유닛 상한이 유효하지 않습니다.')
        units=min(limits.units,int(cap));f.at[i,'effective_units']=units
        if not units:f.at[i,'allocation_reason']='blocked_market_units';continue
        price=float(row.reference_price)
        decision=initial_stop(price*(1+limits.entry_slippage),float(row['atr']),
            float(row['stop_atr_multiple']),float(row['fixed_stop_fraction']),row['initial_stop_method'])
        stop=decision['stop_price']
        for key,value in decision.items():f.at[i,key]=value
        if not 0<stop<price:f.at[i,'allocation_reason']='invalid_entry_stop';continue
        if row.get('stop_limit_enabled',True) and decision['stop_fraction']>row['max_stop_fraction']:
            f.at[i,'allocation_reason']='blocked_stop_limit_at_entry';continue
        if adding and stop<=pos['stop_price']:
            f.at[i,'allocation_reason']='common_stop_not_raised';continue
        held_quantity=pos['quantity'] if adding else 0
        old_risk=risks.get(symbol,0.)
        held_risk=held_quantity*stop_risk(price,stop,limits)
        other_risk=sum(risks.values())-old_risk
        position_fraction=min(limits.max_trade_risk,MAX_POSITION_RISK)
        unit_fraction=position_fraction*units/limits.max_units
        other_max=max([risk for s,risk in risks.items() if s!=symbol],default=0.)
        if other_max>equity*position_fraction+1e-8:
            f.at[i,'allocation_reason']='existing_position_risk_exceeded';continue
        per_share=stop_risk(price,stop,limits,entering=True)
        entry_cash=price*(1+limits.entry_slippage)*(1+limits.cost_rate/2)
        friction=entry_cash-price
        # Execution costs reduce current equity too. Solve caps at post-fill equity.
        caps={'unit_risk':equity*unit_fraction/(per_share+unit_fraction*friction),
            'position_risk':max(0.,equity*position_fraction-held_risk)/(per_share+position_fraction*friction),
            'portfolio_risk':max(0.,equity*limits.total_risk-other_risk-held_risk)/(per_share+limits.total_risk*friction),
            'cash':cash/entry_cash,
            'position':max(0.,equity*limits.max_position-held_quantity*price)/(entry_cash+limits.max_position*friction),
            'sector':max(0.,equity*limits.max_sector-sectors.get(row.sector,0.))/(entry_cash+limits.max_sector*friction),
            'liquidity':float(row.avg_turnover)*limits.volume_participation/entry_cash}
        if friction>0 and position_fraction>0:
            caps['other_position_risk']=max(0.,equity-other_max/position_fraction)/friction
        binding=min(caps,key=caps.get);quantity=max(0,math.floor(caps[binding]))
        after_equity=equity-quantity*friction
        position_limit=after_equity*position_fraction
        portfolio_limit=after_equity*limits.total_risk
        budget=after_equity*unit_fraction
        values=dict(quantity=quantity,planned_risk=quantity*per_share,cash_required=quantity*entry_cash,
            combined_position_risk=held_risk+quantity*per_share,portfolio_risk_after=other_risk+held_risk+quantity*per_share,
            existing_stop_risk_after=held_risk,per_trade_budget=budget,position_risk_limit=position_limit,
            portfolio_risk_limit=portfolio_limit,equity_at_allocation=after_equity,
            common_stop_before=pos['stop_price'] if adding else 0.,estimated_entry_price=price*(1+limits.entry_slippage),
            allocation_reason=binding if quantity else 'blocked_'+binding)
        for key,value in values.items():f.at[i,key]=value
        if quantity:
            equity=after_equity
            cash-=values['cash_required'];risks[symbol]=values['combined_position_risk']
            sectors[row.sector]=sectors.get(row.sector,0.)+values['cash_required']
    return f


def lot_record(row,day,quantity,fill,entry_total,initial_risk,number):
    return dict(number=number,date=str(day.date()),signal_date=row['date'],quantity=quantity,
        entry_price=fill,entry_total=entry_total,initial_risk=initial_risk,
        initial_stop_price=float(row['stop_price']),signal_atr=float(row['atr']),setup_id=setup_identity(row))


def fill_addition(pos,row,day,quantity,fill,entry_total,initial_risk):
    """Only called after the shared allocation batch has passed all risk checks."""
    number=pos['addition_count']+1
    pos['lots'].append(lot_record(row,day,quantity,fill,entry_total,initial_risk,number))
    total_quantity=pos['quantity']+quantity
    pos['average_entry_price']=(pos['average_entry_price']*pos['quantity']+fill*quantity)/total_quantity
    pos['quantity']=total_quantity;pos['entry_total']+=entry_total
    pos['total_committed_risk']+=initial_risk
    pos['addition_count']=number;pos['pyramiding_filled_setups'].append(setup_identity(row))
    pos['stop_price']=float(row['stop_price'])


def execution_events(events,kind,columns):
    if events.empty or 'kind' not in events:return pd.DataFrame(columns=columns)
    return events[events.kind.eq(kind)].reindex(columns=columns)


def risk_events(positions,today,equity,day,limits):
    risks={s:p['quantity']*stop_risk(p['last_price'],p['stop_price'],limits) for s,p in positions.items()}
    total=sum(risks.values());result=[]
    for symbol,pos in positions.items():
        result.append(dict(kind='campaign_risk',date=str(day.date()),symbol=symbol,equity=equity,
            quantity=pos['quantity'],current_price=pos['last_price'],common_stop=pos['stop_price'],
            planned_stop_risk=risks[symbol],operating_risk_limit=equity*limits.max_trade_risk,
            hard_risk_limit=equity*MAX_POSITION_RISK,
            operating_risk_exceeded=risks[symbol]>equity*limits.max_trade_risk+1e-8,
            hard_risk_exceeded=risks[symbol]>equity*MAX_POSITION_RISK+1e-8,
            portfolio_stop_risk=total,portfolio_risk_limit=equity*limits.total_risk,
            portfolio_risk_exceeded=total>equity*limits.total_risk+1e-8,
            stale_price=symbol not in today or today[symbol]['volume']<=0))
    return result
