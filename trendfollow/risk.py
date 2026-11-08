"""Shared cash/risk allocation. Values are indicative planning prices, not orders."""
from dataclasses import dataclass, field
import math

import pandas as pd


@dataclass
class Account:
    equity: float
    cash: float
    positions: list[dict] = field(default_factory=list)
    pending_cash: float = 0
    pending_risk: float = 0
    pending_symbols: list[str] = field(default_factory=list)

    def validate(self):
        values = [self.equity, self.cash, self.pending_cash, self.pending_risk]
        if not all(math.isfinite(v) for v in values) or self.equity <= 0 or min(values[1:]) < 0:
            raise ValueError('계좌 금액이 유효하지 않습니다.')
        if self.cash > self.equity or self.pending_cash > self.cash:
            raise ValueError('현금·예약금이 계좌 금액을 초과합니다.')
        symbols = []
        for p in self.positions:
            required = {'symbol', 'sector', 'quantity', 'current_price', 'stop_price'}
            if not required.issubset(p) or not p['sector']:
                raise ValueError('보유 종목에 sector,current_price,stop_price,quantity가 필요합니다.')
            if (not all(math.isfinite(p[k]) for k in ['quantity', 'current_price', 'stop_price'])
                or p['quantity'] <= 0 or int(p['quantity']) != p['quantity']
                or p['current_price'] <= 0 or p['stop_price'] < 0):
                raise ValueError('보유 종목 수량·가격이 유효하지 않습니다.')
            symbols.append(p['symbol'])
        if len(symbols) != len(set(symbols)):
            raise ValueError('보유 종목이 중복되었습니다.')
        if sum(p['quantity']*p['current_price'] for p in self.positions) + self.cash > self.equity + .01:
            raise ValueError('보유 평가액과 현금이 equity를 초과합니다.')


@dataclass(frozen=True)
class Limits:
    max_trade_risk: float = .01
    total_risk: float = .04
    max_position: float = .25
    max_sector: float = .40
    cash_reserve: float = .10
    volume_participation: float = .001
    units: int = 1
    max_units: int = 5
    cost_rate: float = .003
    entry_slippage: float = .002
    exit_slippage: float = .002

    def validate(self):
        if (not isinstance(self.units, int) or not isinstance(self.max_units, int)
                or isinstance(self.units, bool) or isinstance(self.max_units, bool)
                or not 1 <= self.units <= self.max_units or self.max_units < 1):
            raise ValueError('units는 1 이상 max_units 이하여야 합니다.')
        for name in ['max_trade_risk', 'total_risk', 'max_position', 'max_sector',
                     'cash_reserve', 'volume_participation', 'cost_rate',
                     'entry_slippage', 'exit_slippage']:
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 <= value < 1:
                raise ValueError(f'{name}은 0 이상 1 미만이어야 합니다.')


def allocate(candidates: pd.DataFrame, account: Account, limits: Limits = Limits()) -> pd.DataFrame:
    account.validate(); limits.validate()
    result = candidates.copy()
    result['quantity'] = 0
    result['planned_risk'] = 0.
    result['cash_required'] = 0.
    result['allocation_reason'] = 'not_a_signal'
    result['effective_units'] = limits.units
    if 'market_unit_cap' in result:
        def effective_units(cap):
            if pd.isna(cap):
                return 0
            if isinstance(cap,bool) or not math.isfinite(cap) or cap < 0 or int(cap) != cap:
                raise ValueError('시장 유닛 상한이 유효하지 않습니다.')
            return min(limits.units,int(cap))
        result['effective_units'] = result.market_unit_cap.map(effective_units)
    cash = max(0., account.cash - account.pending_cash - account.equity*limits.cash_reserve)
    def risk_per_share(price, stop):
        return max(0., price*(1+limits.entry_slippage)-stop*(1-limits.exit_slippage)) + price*limits.cost_rate
    existing_risk = sum(p['quantity'] * risk_per_share(p['current_price'], p['stop_price'])
                        for p in account.positions)
    risk_left = max(0., account.equity*limits.total_risk - existing_risk - account.pending_risk)
    sectors = {}
    for p in account.positions:
        sectors[p['sector']] = sectors.get(p['sector'], 0) + p['quantity']*p['current_price']
    held = {str(p['symbol']) for p in account.positions} | set(account.pending_symbols)
    if any(p['current_price'] <= p['stop_price'] for p in account.positions):
        result.loc[result.signal, 'allocation_reason'] = 'existing_stop_breached_review_exit_first'
        return result
    # Without per-sector reservations, pending orders cannot be allocated safely.
    if account.pending_cash or account.pending_risk or account.pending_symbols:
        result.loc[result.signal, 'allocation_reason'] = 'pending_orders_require_reconciliation'
        return result
    score = 'allocation_score' if 'allocation_score' in result else 'rs_percentile'
    ranked = result.sort_values([score, 'symbol'], ascending=[False, True])
    for i, row in ranked.iterrows():
        if not row.signal:
            continue
        if str(row.symbol) in held:
            result.at[i, 'allocation_reason'] = 'already_held'; continue
        if not isinstance(row.sector, str) or not row.sector.strip():
            result.at[i, 'allocation_reason'] = 'missing_sector'; continue
        units = int(row.effective_units)
        if not units:
            result.at[i,'allocation_reason'] = 'blocked_market_units'
            continue
        price, stop = float(row.reference_price), float(row.stop_price)
        if not (math.isfinite(price) and math.isfinite(stop) and 0 < stop < price):
            raise ValueError('후보의 진입·손절 가격이 유효하지 않습니다.')
        risk = risk_per_share(price, stop)
        cash_per_share = price * (1+limits.entry_slippage+limits.cost_rate)
        budget = account.equity*limits.max_trade_risk*units/limits.max_units
        sector_room = max(0, account.equity*limits.max_sector-sectors.get(row.sector, 0))
        caps = {'unit_risk': budget/risk, 'portfolio_risk': risk_left/risk,
                'cash': cash/cash_per_share, 'position': account.equity*limits.max_position/cash_per_share,
                'sector': sector_room/cash_per_share,
                'liquidity': row.avg_turnover*limits.volume_participation/cash_per_share}
        binding = min(caps, key=caps.get)
        quantity = max(0, math.floor(caps[binding]))
        result.at[i, 'quantity'] = quantity
        result.at[i, 'planned_risk'] = quantity*risk
        result.at[i, 'cash_required'] = quantity*cash_per_share
        result.at[i, 'allocation_reason'] = binding if quantity else f'blocked_{binding}'
        cash -= quantity*cash_per_share
        risk_left -= quantity*risk
        sectors[row.sector] = sectors.get(row.sector, 0) + quantity*cash_per_share
        if quantity:
            held.add(str(row.symbol))
    return result
