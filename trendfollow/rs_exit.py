"""Optional close-confirmed strength exits, independent of entry filters."""
import pandas as pd

RS_EXIT_SETTINGS=('rs_exit_enabled','rs_exit_percentile','rs_exit_mansfield','rs_exit_confirmations')


def exit_columns(frame,rules,as_of=None):
    if not rules.rs_exit_enabled:return frame
    f=frame.copy()
    f['rs_exit_enabled']=True
    f['rs_exit_method']=rules.rs_method
    field='rs_percentile' if rules.rs_method=='percentile' else 'mansfield_rs'
    f['rs_exit_value']=f[field]
    f['rs_exit_threshold']=rules.rs_exit_percentile if rules.rs_method=='percentile' else rules.rs_exit_mansfield
    f['rs_exit_confirmations']=rules.rs_exit_confirmations
    if rules.rs_method=='percentile':
        f['rs_exit_observation_date']=pd.to_datetime(f['date'] if 'date' in f else pd.Series(as_of,index=f.index))
    else:
        f['rs_exit_observation_date']=pd.to_datetime(f['mansfield_observation_date'])
    f['rs_exit_data_ready']=f.rs_exit_value.notna() & f.rs_exit_observation_date.notna()
    f['rs_exit_weak']=f.rs_exit_data_ready & (f.rs_exit_value<f.rs_exit_threshold)
    f['rs_exit_status']='unavailable'
    f.loc[f.rs_exit_data_ready,'rs_exit_status']='not_weak'
    f.loc[f.rs_exit_weak,'rs_exit_status']='weak'
    return f


def update_position(pos,row,day):
    """Return a latched next-open instruction after distinct weak observations."""
    if not row.get('rs_exit_enabled',False) or pos.get('rs_exit_pending'):return None
    if not row.get('rs_exit_data_ready',False) or pd.isna(row.get('rs_exit_value')):
        pos['rs_weak_count']=0
        return None
    observed=pd.Timestamp(row['rs_exit_observation_date']).strftime('%Y-%m-%d')
    if observed>str(day.date()):raise ValueError('미래 상대강도 관측으로 청산할 수 없습니다.')
    if observed<=pos.get('rs_last_observation',''):return None
    pos['rs_last_observation']=observed
    # A completed weekly value predating entry is not a post-entry observation.
    if observed<pos['entry_date']:
        pos['rs_weak_count']=0
        return None
    pos['rs_weak_count']=pos.get('rs_weak_count',0)+1 if row['rs_exit_weak'] else 0
    if pos['rs_weak_count']<row['rs_exit_confirmations']:return None
    decision={'reason':'rs_weakness_'+row['rs_exit_method'],'signal_date':str(day.date()),
        'observation_date':observed,'indicator_value':float(row['rs_exit_value']),
        'threshold':float(row['rs_exit_threshold']),'confirmations':int(pos['rs_weak_count'])}
    pos['rs_exit_pending']=decision
    return decision
