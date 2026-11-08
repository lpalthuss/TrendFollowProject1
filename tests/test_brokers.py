import pytest

from trendfollow.brokers import BrokerError, Credentials, QuoteClient


class Response:
    status_code = 200

    def __init__(self, body, headers=None):
        self.body, self.headers = body, headers or {}

    def json(self):
        return self.body


class Session:
    def __init__(self, responses):
        self.responses, self.calls = iter(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return next(self.responses)


def kis_row(day):
    return dict(stck_bsop_date=day, stck_oprc='100', stck_hgpr='110', stck_lwpr='90',
                stck_clpr='105', acml_vol='1234')


def test_kis_date_pagination_and_adjusted_price_request():
    session = Session([Response({'access_token':'TOKEN'}), Response({'rt_cd':'0','output2':[kis_row('20261106')]}),
                       Response({'rt_cd':'0','output2':[kis_row('20261105')]})])
    client = QuoteClient(Credentials('kis','paper','KEY','SECRET'), session, delay=0)
    result = client.daily('005930','2026-11-05','2026-11-06')
    assert list(result.date) == ['2026-11-05','2026-11-06']
    assert session.calls[-1][2]['params']['FID_INPUT_DATE_2'] == '20261105'
    assert session.calls[-1][2]['params']['FID_ORG_ADJ_PRC'] == '0'
    assert ':29443/' in session.calls[0][1]
    assert session.calls[0][2]['allow_redirects'] is False


def test_kiwoom_continuation_keeps_base_date():
    def row(day):
        return dict(dt=day,open_pric='100',high_pric='110',low_pric='90',cur_prc='105',trde_qty='1234')
    session = Session([Response({'token':'TOKEN'}),
                       Response({'return_code':0,'stk_dt_pole_chart_qry':[row('20261106')]}, {'cont-yn':'Y','next-key':'page2'}),
                       Response({'return_code':0,'stk_dt_pole_chart_qry':[row('20261105')]})])
    client = QuoteClient(Credentials('kiwoom','live','KEY','SECRET'), session, delay=0)
    assert len(client.daily('005930','2026-11-05','2026-11-06')) == 2
    assert session.calls[-1][2]['headers']['next-key'] == 'page2'
    assert session.calls[-1][2]['json']['base_dt'] == '20261106'
    assert session.calls[0][2]['json']['secretkey'] == 'SECRET'


def test_api_error_does_not_echo_secrets_or_issue_orders():
    session = Session([Response({'return_code':1, 'return_msg':'SECRET TOKEN'})])
    client = QuoteClient(Credentials('kiwoom','live','KEY','SECRET'),session,delay=0)
    with pytest.raises(BrokerError) as error:
        client.authenticate()
    assert 'SECRET' not in str(error.value)
    with pytest.raises(BrokerError):
        client._send('POST','/api/dostk/ordr')
    assert len(session.calls) == 1


def test_missing_secret_is_rejected_without_network(tmp_path, monkeypatch):
    monkeypatch.delenv('KIS_APP_KEY',raising=False)
    monkeypatch.delenv('KIS_APP_SECRET',raising=False)
    p = tmp_path/'.env'; p.write_text('KIS_ENV=paper\nKIS_APP_KEY=KEY\n',encoding='utf-8')
    with pytest.raises(ValueError, match='키와 시크릿'):
        Credentials.load('kis', p)
