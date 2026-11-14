"""Strategy configuration, independent of calculation and transport modules."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Rules:
    rs_days: int = 126
    breakout_days: int = 55
    weekly_ma: int = 30
    weekly_slope: int = 4
    min_rs: float = 70
    volume_multiple: float = 1.5
    min_turnover: float = 1_000_000_000
    atr_days: int = 14
    stop_atr: float = 2
    max_stop_fraction: float = 0.15
    stop_limit_enabled: bool = True
    initial_stop_method: str = "atr"
    initial_stop_reference: str = "signal_close"
    fixed_stop_fraction: float = 0.08
    rs_method: str = "percentile"
    mansfield_weeks: int = 52
    min_mansfield: float = 0
    mansfield_benchmark: str = "universe"
    entry_method: str = "breakout"
    setup_52w_enabled: bool = False
    setup_reversal_atr: float = 2
    setup_min_rise_atr: float = 4
    setup_max_retracement: float = 0.5
    setup_volume_contraction: float = 0.8
    setup_range_contraction: float = 0.8
    sector_require_action: bool = True
    sector_min_peers: int = 3
    sector_min_breadth: float = 0.6
    market_units_enabled: bool = False
    market_units_benchmark: str = "universe"
    market_units_ma_days: int = 60
    market_units_strong_cap: int = 5
    market_units_weak_cap: int = 1
    rs_exit_enabled: bool = False
    rs_exit_percentile: float = 50
    rs_exit_mansfield: float = 0
    rs_exit_confirmations: int = 2
    pyramiding_review_enabled: bool = False
    pyramiding_enabled: bool = False
    pyramiding_max_additions: int = 3

    def validate(self):
        if not isinstance(self.pyramiding_enabled, bool):
            raise ValueError("pyramiding_enabled는 true/false여야 합니다.")
        if (
            isinstance(self.pyramiding_max_additions, bool)
            or not isinstance(self.pyramiding_max_additions, int)
            or not 0 <= self.pyramiding_max_additions <= 3
        ):
            raise ValueError("추가매수 상한은 0~3 정수여야 합니다.")
        if self.pyramiding_enabled and (
            not self.pyramiding_review_enabled
            or self.initial_stop_reference != "entry_fill"
        ):
            raise ValueError(
                "추가매수 모의 체결에는 검토 기능과 entry_fill 손절 기준이 필요합니다."
            )
        if not isinstance(self.pyramiding_review_enabled, bool):
            raise ValueError("pyramiding_review_enabled는 true/false여야 합니다.")
        if self.pyramiding_review_enabled and self.entry_method != "setup":
            raise ValueError("추가매수 검토는 셋업 전략을 사용하세요.")
        if self.initial_stop_method not in {"atr", "fixed", "hybrid"}:
            raise ValueError("초기 손절 방식은 atr/fixed/hybrid여야 합니다.")
        if self.initial_stop_reference not in {"signal_close", "entry_fill"}:
            raise ValueError("초기 손절 기준은 signal_close/entry_fill이어야 합니다.")
        if (
            self.initial_stop_method != "atr"
            and self.initial_stop_reference != "entry_fill"
        ):
            raise ValueError("고정·자동 전환 비교는 entry_fill 기준을 사용하세요.")
        if (
            not math.isfinite(self.fixed_stop_fraction)
            or not 0 < self.fixed_stop_fraction < 1
        ):
            raise ValueError("고정 손절 폭은 0 초과 1 미만이어야 합니다.")
        if not isinstance(self.stop_limit_enabled, bool):
            raise ValueError("stop_limit_enabled는 true/false여야 합니다.")
        if not isinstance(self.rs_exit_enabled, bool):
            raise ValueError("rs_exit_enabled는 true/false여야 합니다.")
        if (
            not math.isfinite(self.rs_exit_percentile)
            or not 0 <= self.rs_exit_percentile <= 100
        ):
            raise ValueError("RS 청산 순위 기준은 0~100이어야 합니다.")
        if not math.isfinite(self.rs_exit_mansfield):
            raise ValueError("Mansfield 청산 기준은 유한한 수여야 합니다.")
        if (
            isinstance(self.rs_exit_confirmations, bool)
            or not isinstance(self.rs_exit_confirmations, int)
            or self.rs_exit_confirmations < 1
        ):
            raise ValueError("RS 약화 확인 횟수는 양의 정수여야 합니다.")
        if not isinstance(self.market_units_enabled, bool):
            raise ValueError("market_units_enabled는 true/false여야 합니다.")
        if self.market_units_benchmark not in {"universe", "market_index"}:
            raise ValueError("시장 유닛 기준은 universe 또는 market_index여야 합니다.")
        for name in [
            "market_units_ma_days",
            "market_units_strong_cap",
            "market_units_weak_cap",
        ]:
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < (1 if name == "market_units_ma_days" else 0)
            ):
                raise ValueError(
                    "시장 유닛 기간은 양의 정수, 상한은 0 이상의 정수여야 합니다."
                )
        if self.market_units_weak_cap > self.market_units_strong_cap:
            raise ValueError("약세 유닛 상한은 강세 상한보다 클 수 없습니다.")
        for name in [
            "rs_days",
            "breakout_days",
            "weekly_ma",
            "weekly_slope",
            "atr_days",
            "mansfield_weeks",
        ]:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name}은 양의 정수여야 합니다.")
        if self.rs_method not in {"percentile", "mansfield"}:
            raise ValueError("rs_method는 percentile 또는 mansfield여야 합니다.")
        if self.entry_method not in {"breakout", "setup"}:
            raise ValueError("entry_method는 breakout 또는 setup이어야 합니다.")
        if not isinstance(self.setup_52w_enabled, bool):
            raise ValueError("setup_52w_enabled는 true/false여야 합니다.")
        if not isinstance(self.sector_require_action, bool):
            raise ValueError("sector_require_action은 true/false여야 합니다.")
        if (
            not isinstance(self.sector_min_peers, int)
            or isinstance(self.sector_min_peers, bool)
            or self.sector_min_peers < 1
        ):
            raise ValueError("sector_min_peers는 양의 정수여야 합니다.")
        for name in [
            "setup_max_retracement",
            "setup_volume_contraction",
            "setup_range_contraction",
            "sector_min_breadth",
        ]:
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 < value <= 1:
                raise ValueError(f"{name}은 0 초과 1 이하여야 합니다.")
        if self.mansfield_benchmark not in {"universe", "market_index"}:
            raise ValueError(
                "mansfield_benchmark는 universe 또는 market_index여야 합니다."
            )
        if not math.isfinite(self.min_mansfield):
            raise ValueError("min_mansfield는 유한한 숫자여야 합니다.")
        if not 0 <= self.min_rs <= 100:
            raise ValueError("min_rs는 0~100이어야 합니다.")
        if not 0 < self.max_stop_fraction < 1:
            raise ValueError("max_stop_fraction은 0~1 사이여야 합니다.")
        for name in [
            "volume_multiple",
            "min_turnover",
            "stop_atr",
            "setup_reversal_atr",
            "setup_min_rise_atr",
        ]:
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"{name}은 유한한 양수여야 합니다.")
