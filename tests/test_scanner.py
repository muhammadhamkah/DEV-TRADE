from survival.scanner import Scanner


def test_scanner_reports_movers_and_arbitrage(fake_market):
    sc = Scanner(fake_market, every_seconds=300, move_threshold=0.08, arb_threshold=0.03)
    assert sc.due(now=1000)
    assert sc.sweep(now=1000) == []            # baseline sweep: nothing to compare, books sum to 1
    assert not sc.due(now=1100) and sc.due(now=1400)
    fake_market.raw["1"]["outcomePrices"] = '["0.75", "0.25"]'   # Yes jumped 15 points
    fake_market.raw["2"]["outcomePrices"] = '["0.85", "0.10"]'   # Yes+No = 0.95
    leads = sc.sweep(now=1400)
    assert len(leads) == 2
    assert leads[0].startswith("mover: 'Will thing 1 happen?' Yes 0.60->0.75")
    assert "possible arbitrage: 'Will thing 2 happen?' Yes+No = 0.95" in leads[1]
    assert sc.sweep(now=1800) == []            # no further change; the persistent gap is not repeated
    fake_market.raw["2"]["outcomePrices"] = '["0.90", "0.10"]'   # gap closes...
    assert sc.sweep(now=2200) == []
    fake_market.raw["2"]["outcomePrices"] = '["0.85", "0.10"]'   # ...and reopens: reported again
    assert len(sc.sweep(now=2600)) == 1
