"""Fetchers against canned API responses (no network)."""
from radar import fetchers


class FakeResponse:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code
    def raise_for_status(self):
        pass
    def json(self):
        return self.data


def test_microsoft_pages_and_dedupes(monkeypatch):
    def job(i):
        return {"id": i, "name": f"Principal Product Manager {i}", "standardizedLocations": ["Redmond, WA, US"],
                "postedTs": 1792180312, "positionUrl": f"/careers/job/{i}"}
    pages = {0: [job(i) for i in range(10)], 10: [job(10), job(3)]}   # job 3 repeats on page 2
    calls = []
    def fake_get(url, headers, timeout, params):
        calls.append(params["start"])
        return FakeResponse({"data": {"positions": pages.get(params["start"], [])}})
    monkeypatch.setattr(fetchers.requests, "get", fake_get)

    out = fetchers.fetch_microsoft(["principal product manager"], {}, sleep=lambda s: None)
    assert calls == [0, 10]                              # stops at a short page
    assert len(out) == 11
    assert out[0]["url"] == "https://apply.careers.microsoft.com/careers/job/0"
    assert out[0]["location"] == "Redmond, WA, US" and out[0]["posted"] == "2026-10-16"


def test_pay_from_text():
    text = ("<p>Base pay: $190,800 &ndash; $262,800 per year. In NYC: $210K - $289.5K.</p>"
            "<p>Interns earn $55/hour. We raised $500 million.</p>")
    assert fetchers.pay_from_text(text) == 289_500
    assert fetchers.pay_from_text("&lt;p&gt;USD $139,900 - $274,800 per year&lt;/p&gt;") == 274_800
    assert fetchers.pay_from_text("Competitive salary") is None
    # Only ranges count: a dollar figure in the pitch isn't pay
    assert fetchers.pay_from_text("spending $10M a year, that's $400K in waste. Salary: $184,000 - $230,000.") == 230_000
    assert fetchers.pay_from_text("USD $139,900 to USD $274,800 per year") == 274_800


def test_ashby_pay_from_feed():
    job = {"compensation": {"summaryComponents": [
        {"compensationType": "Salary", "interval": "1 YEAR", "currencyCode": "USD", "minValue": 190800, "maxValue": 262800},
        {"compensationType": "EquityCashValue", "interval": "1 YEAR", "currencyCode": "USD", "maxValue": None}]}}
    assert fetchers._ashby_pay(job) == 262800
    assert fetchers._ashby_pay({}) is None


def test_microsoft_waits_out_rate_limit(monkeypatch):
    answers = [FakeResponse({}, 429), FakeResponse({}, 429), FakeResponse({"data": {"positions": []}})]
    monkeypatch.setattr(fetchers.requests, "get", lambda url, headers, timeout, params: answers.pop(0))
    waited = []
    assert fetchers.fetch_microsoft(["pm"], {}, sleep=waited.append) == []
    assert waited == [15, 30]
