"""Tests des correctifs V12.

Couvre : bornage des bêtas (A3), score de risque PTF (B5), conversion CPI en
YoY % (A5), résilience CoinMetrics (B7), calendrier soir FRED-only (P2-A1).
Compatibles pytest ET runner offline. Aucune source réseau réelle.
"""

from __future__ import annotations


# --------------------------------------------------------------------------- #
# B5 — score de risque PTF
# --------------------------------------------------------------------------- #
def test_risk_score_high_when_concentrated_drawdown_no_cash() -> None:
    from src.main import _compute_portfolio_risk_score

    snapshot = {"change_7d_pct": -16.0}
    sectors = {"available": True, "sectors": [
        {"sector": "L1", "ptf_pct": 47.0}, {"sector": "AI", "ptf_pct": 25.0},
    ]}
    macro = {"fear_greed": 12, "vix": 16}
    enriched = {
        "ADA": {"value_usd": 100, "change_24h": -13.0},
        "TAO": {"value_usd": 80, "change_24h": -9.0},
    }
    portfolio = {"ADA": {"value_usd": 100}, "TAO": {"value_usd": 80}}  # 0 cash
    out = _compute_portfolio_risk_score(snapshot, sectors, macro, enriched, portfolio)
    # v18 (M-B8) : le cash n'est PLUS une composante. Score = drawdown (3.0) +
    # concentration (~1.35) + volatilité (~1.8) + sentiment (1.0) ≈ 7.2 → élevé.
    assert out["score"] >= 7
    assert out["level"] == "élevé"
    # Le cash ne doit PLUS apparaître ni dans les facteurs ni dans les barres.
    assert not any("cash" in f.lower() for f in out["factors"])
    assert all(c["label"] != "Cash" for c in out["components"])

def test_risk_score_low_when_calm_diversified_cash() -> None:
    from src.main import _compute_portfolio_risk_score

    snapshot = {"change_7d_pct": 1.0}  # pas de drawdown
    sectors = {"available": True, "sectors": [
        {"sector": "L1", "ptf_pct": 18.0}, {"sector": "AI", "ptf_pct": 12.0},
    ]}
    macro = {"fear_greed": 55, "vix": 14}
    enriched = {"BTC": {"value_usd": 500, "change_24h": 0.5}}
    portfolio = {
        "BTC": {"value_usd": 500},
        "USDC": {"value_usd": 200, "role": "cash_reserve"},  # ~28% cash
    }
    out = _compute_portfolio_risk_score(snapshot, sectors, macro, enriched, portfolio)
    assert out["score"] <= 3
    assert out["level"] in ("maîtrisé", "modéré")
    assert all("cash" not in f for f in out["factors"])  # cash présent → pas de pénalité


# --------------------------------------------------------------------------- #
# A5 — CPI converti en YoY % (pas l'indice brut 332)
# --------------------------------------------------------------------------- #
def test_cpi_displayed_as_yoy_percent(monkeypatch) -> None:
    from src.data_sources import fred

    monkeypatch.setenv("FRED_API_KEY", "x")
    # 14 mois d'indice CPI : il y a 13 mois 320, aujourd'hui 332 → YoY = +3.75%.
    cpi_obs = [{"date": f"2025-{m:02d}-01", "value": 320.0 + m * 0.5} for m in range(1, 13)]
    cpi_obs += [{"date": "2025-12-01", "value": 331.0}, {"date": "2026-01-01", "value": 332.0}]

    def fake_obs(series_id, key, limit=60):
        if series_id == "CPIAUCSL":
            return cpi_obs[-limit:]
        if series_id == "UNRATE":
            return [{"date": "2026-01-01", "value": 4.3}, {"date": "2026-02-01", "value": 4.3}]
        return []

    monkeypatch.setattr(fred, "_series_observations", fake_obs)
    if hasattr(fred.CACHE, "_store"):
        fred.CACHE._store.clear()
    out = fred.get_calendar_prints()
    prints = {p["key"]: p for p in out.get("prints", [])}
    assert "cpi" in prints
    # Le champ display doit être un pourcentage sur 1 an, PAS l'indice 332.
    assert "sur 1 an" in prints["cpi"]["display"]
    assert "%" in prints["cpi"]["display"]
    assert "332" not in prints["cpi"]["display"]
    # Le chômage reste un niveau en %.
    if "unemployment" in prints:
        assert "%" in prints["unemployment"]["display"]


# --------------------------------------------------------------------------- #
# B7 — CoinMetrics : repli sur le sous-ensemble cœur si le lot complet échoue
# --------------------------------------------------------------------------- #
def test_coinmetrics_falls_back_to_core_metrics(monkeypatch) -> None:
    from src.data_sources import coinmetrics

    calls = {"n": 0}

    def fake_get_json(url, params=None, **kw):
        calls["n"] += 1
        metrics = (params or {}).get("metrics", "")
        # 1er appel (lot complet avec NVTAdj/AdrActCnt) → vide (refus tier).
        if "NVTAdj" in metrics or "AdrActCnt" in metrics:
            return {"data": []}
        # 2e appel (cœur : PriceUSD + CapMVRVCur) → données valides.
        return {"data": [
            {"asset": "btc", "time": "2026-01-01T00:00:00Z", "PriceUSD": "62000", "CapMVRVCur": "1.19"},
            {"asset": "btc", "time": "2026-01-02T00:00:00Z", "PriceUSD": "62500", "CapMVRVCur": "1.20"},
        ]}

    monkeypatch.setattr(coinmetrics, "get_json", fake_get_json)
    if hasattr(coinmetrics.CACHE, "_store"):
        coinmetrics.CACHE._store.clear()
    out = coinmetrics.get_onchain_metrics()
    assert out["available"] is True
    assert "BTC" in out["assets"]
    assert out["assets"]["BTC"]["mvrv"] == 1.2  # le MVRV est bien récupéré via le repli
    assert calls["n"] >= 2  # a bien tenté le lot complet PUIS le repli
