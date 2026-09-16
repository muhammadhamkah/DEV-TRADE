import json

from research import analyze, backfill


class FakeResp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeSession:
    """Two pages of closed markets, then empty; every token has a short price history."""

    def __init__(self):
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if url.endswith("/markets"):
            off = params["offset"]
            if off >= 3:
                return FakeResp([])
            rows = []
            for i in range(off, min(off + 100, 3)):
                rows.append({"id": str(i), "question": f"Q{i}", "outcomes": '["Yes","No"]',
                             "outcomePrices": '["1","0"]' if i != 1 else '["0","1"]',
                             "clobTokenIds": f'["tok{i}a","tok{i}b"]', "endDate": "2026-09-01T00:00:00Z", "volumeNum": 1000, "liquidityNum": 50})
            return FakeResp(rows)
        tok = params["market"]
        base = 0.9 if tok.startswith(("tok0", "tok2")) else 0.2
        return FakeResp({"history": [{"t": 1000 + k * 3600, "p": base + 0.001 * k} for k in range(48)]})


def test_backfill_is_resumable_and_writes_rows(tmp_path):
    out = str(tmp_path / "resolved.jsonl")
    n = backfill.run(pause=0, session=FakeSession(), out=out)
    assert n == 3
    rows = [json.loads(l) for l in open(out)]
    assert rows[1]["winner"] == "No" and rows[0]["winner"] == "Yes"
    assert len(rows[0]["history"]) == 48
    assert backfill.run(pause=0, session=FakeSession(), out=out) == 0  # nothing new


def test_calibration_buckets_and_returns():
    rows = [
        {"outcomes": ["Yes", "No"], "winner": "Yes", "history": [[0, 0.9], [90000, 0.9], [180000, 0.9]]},
        {"outcomes": ["Yes", "No"], "winner": "Yes", "history": [[0, 0.9], [90000, 0.9], [180000, 0.9]]},
        {"outcomes": ["Yes", "No"], "winner": "No", "history": [[0, 0.9], [90000, 0.9], [180000, 0.9]]},
        {"outcomes": ["Yes", "No"], "winner": "No", "history": [[0, 0.03], [90000, 0.03], [180000, 0.03]]},
    ]
    cal = analyze.calibration(rows, 86400, spread=0.02)
    top = next(c for c in cal if c["bucket"] == "0.85-0.95")
    assert top["n"] == 3 and abs(top["actual"] - 2 / 3) < 1e-3 and top["return_after_spread"] < 0
    low = next(c for c in cal if c["bucket"] == "0.00-0.05")
    assert low["n"] == 1 and low["actual"] == 0.0
    text = analyze.report(rows)
    assert "Price 24 hours before close" in text and "0.85-0.95" in text
