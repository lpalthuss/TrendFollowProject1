"""Post-3R setup reviews only. These records never authorize or size an order."""
import math

import pandas as pd

SETTINGS=('pyramiding_review_enabled',)
REVIEW_COLUMNS=['date','symbol','review_status','eligible_for_risk_review','review_reasons',
    'setup_id','success_3r_date','entry_date','entry_price','initial_risk_per_share','target_3r',
    'close','holding_quantity','holding_stop','proposed_stop_price','additional_quantity',
    'risk_aggregation_status','orders_created']


def review_columns(frame,rules):
    if not rules.pyramiding_review_enabled:return frame
    f=frame.copy();f['pyramiding_review_enabled']=True
    if rules.pyramiding_enabled:
        f['pyramiding_enabled']=True;f['pyramiding_max_additions']=rules.pyramiding_max_additions
    return f


def setup_identity(row):
    fields=['setup_wave_start','setup_peak_date','setup_confirmed_at']
    dates=[date_value(row.get(k)) for k in fields]
    if any(value is None for value in dates):return ''
    return '|'.join(str(value.date()) for value in dates)


def finite(value):
    try:return math.isfinite(float(value))
    except (TypeError,ValueError):return False


def is_true(value):
    return bool(pd.notna(value) and value==True)


def date_value(value):
    if value is None or pd.isna(value):return None
    try:
        result=pd.Timestamp(value)
        if pd.isna(result) or result.tz is not None or result!=result.normalize():return None
        return result
    except (ValueError,TypeError):return None


def evaluate_addition(pos,row,day,limits):
    """Evaluate completed-close evidence; mutate only the review deduplication ledger."""
    day=pd.Timestamp(day);reasons=[]
    if date_value(row.get('date'))!=day:reasons.append('stale_or_missing_signal')
    valid_position=all(finite(pos.get(k)) and float(pos[k])>0
        for k in ['quantity','entry_price','initial_risk','target_3r','stop_price'])
    if not valid_position:reasons.append('missing_initial_risk')
    success=date_value(pos.get('success_3r_date'))
    entered=date_value(pos.get('entry_date'))
    if not is_true(pos.get('success',False)):reasons.append('three_r_not_reached')
    if success is None or entered is None or not entered<=success<=day:
        reasons.append('missing_or_invalid_three_r_date')
    close=row.get('close',row.get('reference_price'))
    if not finite(close) or not valid_position or float(close)<float(pos['target_3r']):
        reasons.append('below_three_r_now')
    if pos.get('exit_pending') or pos.get('rs_exit_pending'):reasons.append('exit_pending')
    if valid_position and float(pos['stop_price'])<float(pos['entry_price']):
        reasons.append('holding_stop_below_entry')
    if finite(close) and valid_position and float(pos['stop_price'])>=float(close):
        reasons.append('holding_stop_breached')
    peak=date_value(row.get('setup_peak_date'));confirmed=date_value(row.get('setup_confirmed_at'))
    wave=date_value(row.get('setup_wave_start'))
    if (wave is None or peak is None or confirmed is None or not wave<=peak<confirmed<day
            or success is None or entered is None or peak<=max(success,entered)):
        reasons.append('no_new_post_three_r_setup')
    setup_id=setup_identity(row)
    if not setup_id or setup_id==pos.get('entry_setup_id'):reasons.append('not_a_new_setup')
    if setup_id in pos.get('pyramiding_review_seen',[]):reasons.append('setup_already_reviewed')
    if row.get('entry_method')!='setup':reasons.append('not_setup_strategy')
    if not all(is_true(row.get(k,False)) for k in ['setup_ready','setup_trigger','setup_entry']):
        reasons.append('no_valid_setup_trigger')
    if not is_true(row.get('setup_invalidated',True)==False):reasons.append('unhealthy_pullback')
    if not is_true(row.get('signal',False)):reasons.append('entry_filters_not_passed')
    stop=row.get('stop_price')
    if row.get('initial_stop_reference')=='entry_fill' and finite(close):
        from .initial_stop import initial_stop
        try:
            decision=initial_stop(float(close)*(1+limits.entry_slippage),float(row.get('atr',float('nan'))),
                float(row['stop_atr_multiple']),float(row['fixed_stop_fraction']),row['initial_stop_method'])
            stop=decision['stop_price']
            if row.get('stop_limit_enabled',True) and decision['stop_fraction']>float(row['max_stop_fraction']):
                reasons.append('proposed_stop_limit_exceeded')
        except (ValueError,KeyError,TypeError):stop=None
    if not finite(stop) or not finite(close) or not 0<float(stop)<float(close):
        reasons.append('invalid_proposed_stop')
    if is_true(row.get('pyramiding_enabled',False)):
        if pos.get('addition_count',0)>=row['pyramiding_max_additions']:reasons.append('max_additions_reached')
        if finite(stop) and finite(pos.get('stop_price')) and float(stop)<=float(pos['stop_price']):
            reasons.append('common_stop_not_raised')
        if pos.get('lots'):
            last_fill=pos['lots'][-1].get('entry_price')
            if not finite(last_fill):reasons.append('missing_campaign_history')
            elif finite(close) and float(close)<=float(last_fill):reasons.append('not_above_last_fill')
    eligible=not reasons
    if eligible:
        pos.setdefault('pyramiding_review_seen',[]).append(setup_id)
    return dict(date=str(day.date()),symbol=str(row['symbol']),
        review_status='eligible_for_risk_review' if eligible else 'blocked',
        eligible_for_risk_review=eligible,review_reasons=','.join(reasons),setup_id=setup_id,
        success_3r_date=str(success.date()) if success is not None else None,entry_date=pos.get('entry_date'),
        entry_price=pos.get('entry_price'),
        initial_risk_per_share=float(pos['initial_risk'])/float(pos.get('initial_quantity',pos['quantity'])) if valid_position else None,
        target_3r=pos.get('target_3r'),close=float(close) if finite(close) else None,
        holding_quantity=pos.get('quantity'),holding_stop=pos.get('stop_price'),
        proposed_stop_price=float(stop) if finite(stop) else None,additional_quantity=0,
        risk_aggregation_status='pending',orders_created=0)


def review_events(events):
    if events.empty or 'kind' not in events:return pd.DataFrame(columns=REVIEW_COLUMNS)
    return events[events.kind.eq('pyramiding_review')].reindex(columns=REVIEW_COLUMNS)
