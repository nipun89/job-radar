"""Fetchers against canned API responses (no network)."""
from radar import fetchers


class FakeResponse:
    def __init__(self, data):
        self.data = data
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

    out = fetchers.fetch_microsoft(["principal product manager"], {}, pause=0)
    assert calls == [0, 10]                              # stops at a short page
    assert len(out) == 11
    assert out[0]["url"] == "https://apply.careers.microsoft.com/careers/job/0"
    assert out[0]["location"] == "Redmond, WA, US" and out[0]["posted"] == "2026-10-16"
