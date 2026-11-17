"""Exercise reruns, empty state, authorization and unknown-value filtering."""
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from trendfollow.streamlit_ui import filter_candidates

ROOT = Path(__file__).resolve().parents[1]


def test_filters_keep_unknown_separate_from_false():
    rows = [dict(symbol='000001',signal=True,rs_pass=True,volume_ratio=2),
            dict(symbol='000002',signal=False,rs_pass=False,volume_ratio=.5),
            dict(symbol='000003',signal=None,rs_pass=None,volume_ratio=None)]
    assert [r['symbol'] for r in filter_candidates(rows, signal='미통과')] == ['000002']
    assert [r['symbol'] for r in filter_candidates(rows, signal='미확인·미적용')] == ['000003']
    assert [r['symbol'] for r in filter_candidates(rows, rs='통과',volume='통과')] == ['000001']
    assert filter_candidates(rows,search='없는 종목') == []


def test_readonly_navigation_never_submits_jobs(monkeypatch):
    monkeypatch.delenv('UI_PASSWORD',raising=False)
    with patch('trendfollow.ui_service.Operations.submit') as submit:
        app = AppTest.from_file(str(ROOT/'streamlit_app.py')).run(timeout=30)
        assert not app.exception
        for page in ['오늘의 운용','보유·체결','장중 관측','전략·설정','종목 탐색']:
            app.radio[0].set_value(page).run(timeout=30)
            assert not app.exception
            assert not app.error
        submit.assert_not_called()


def test_password_gate_and_account_save(tmp_path, monkeypatch):
    import shutil
    import trendfollow.streamlit_ui as ui
    shutil.copytree(ROOT/'examples',tmp_path/'examples')
    monkeypatch.setattr(ui,'ROOT',tmp_path)
    monkeypatch.setenv('UI_PASSWORD','test-only-password')
    app = AppTest.from_file(str(ROOT/'streamlit_app.py')).run(timeout=30)
    assert not app.radio
    app.text_input[0].set_value('wrong')
    app.button[0].click().run()
    assert app.error
    app.text_input[0].set_value('test-only-password')
    app.button[0].click().run()
    assert not app.exception
    app.radio[0].set_value('전략·설정').run()
    next(b for b in app.button if b.label=='별도 계좌 저장').click().run()
    assert not app.exception
    assert not app.error
    saved=list((tmp_path/'outputs/ui/accounts').glob('*/config.json'))
    assert len(saved)==1
    app.run()
    assert len(list((tmp_path/'outputs/ui/accounts').glob('*/config.json')))==1
    assert not list((tmp_path/'outputs/ui/accounts').glob('*/state.json'))


def test_candidate_detail_and_filter_are_connected(monkeypatch):
    from trendfollow.ui_service import Operations
    original = Operations.snapshot
    def snapshot(self,key):
        result = original(self,key)
        result['rows'] = [dict(symbol='000001',name='테스트 상승',sector='산업A',
            reference_price=10000,rs_percentile=95,signal=True,rs_pass=True,
            setup_phase='breakout',volume_ratio=2),
            dict(symbol='000002',name='테스트 대기',sector='산업B',
            reference_price=9000,rs_percentile=70,signal=False,rs_pass=False,
            setup_phase='base',volume_ratio=None)]
        return result
    monkeypatch.delenv('UI_PASSWORD',raising=False)
    with patch.object(Operations,'snapshot',snapshot), patch.object(Operations,'prices',return_value=[
            dict(date='2026-11-13',close=9900),dict(date='2026-11-16',close=10000)]):
        app = AppTest.from_file(str(ROOT/'streamlit_app.py')).run(timeout=30)
        assert not app.exception and not app.error
        assert any(s.value=='테스트 상승' for s in app.subheader)
        next(t for t in app.text_input if t.label=='종목명·코드·산업 검색').set_value('대기').run()
        assert not app.exception and not app.error
        assert any(s.value=='테스트 대기' for s in app.subheader)
        next(b for b in app.button if b.label=='관측 목록에 추가').click().run()
        key=app.selectbox(key='account-picker').value
        assert app.session_state[f'watch-{key}']==['000002']
