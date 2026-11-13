"""Trading dates shared by the KRX observation entry points."""

from datetime import date, timedelta
from datetime import time
from functools import lru_cache

from exchange_calendars import get_calendar


@lru_cache(maxsize=4)
def calendar(year):
    return get_calendar("XKRX", start=f"{year - 1}-12-01", end=f"{year + 1}-01-31")


def previous_close_date(session):
    try:
        day = date.fromisoformat(session)
        if session != day.isoformat():
            raise ValueError
        krx = calendar(day.year)
        if not krx.is_session(day):
            raise ValueError
        return krx.previous_session(day).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        raise ValueError("KRX가 개장하는 날짜를 감시 거래일로 선택하세요.") from None


def require_previous_close(session, as_of):
    required = previous_close_date(session)
    if as_of != required:
        raise ValueError(f"{session} 감시에는 직전 거래일 {required} 종가 후보가 필요합니다.")
    return required


def default_watch_session(now):
    day = now.date()
    if now.time() >= time(20):
        day += timedelta(days=1)
    return calendar(day.year).date_to_session(day, direction="next").date().isoformat()


def observation_market(now):
    """Market whose trades can be observed at the given Korean local time."""
    if not calendar(now.year).is_session(now.date()):
        return None
    local = now.time().replace(tzinfo=None)
    if time(9) <= local <= time(15, 30):
        return "krx"
    if time(15, 40) <= local < time(20):
        return "nxt_after"
    return None
