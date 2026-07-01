"""Tests v22 — P0 « PLOMBERIE » de l'analyse. Verrouille les correctifs qui rendent
VRAI ce que le prompt promet déjà :

  • #57 technical_local : RSI/MACD/Bollinger + vraie divergence depuis l'OHLC.
  • #56 thesis_scoring lit le RSI hebdo RÉEL (per_tf) + la divergence (tech_local).
  • #54 onchain_flows : score on-chain réel (plus de constante 55.0 factice).
  • #55 macro_alignment : tilt macro réel, plafonné 40-60 (contexte, pas déclencheur).
  • #53 complétude par actif + plafond de confiance (verrou anti-reco à trous).
  • #60 coherence_checker borne la confiance au plafond de complétude.

Hermétiques : aucun réseau (dicts en dur / fonctions pures).
"""

from __future__ import annotations


# --------------------------------------------------------------------------- #
# #57 — technical_local (calcul pur Python)
# --------------------------------------------------------------------------- #
def test_local_rsi_extremes():
    from src.analytics.technical_local import compute_rsi
    assert compute_rsi([100 + i for i in range(30)]) == 100.0       # hausse pure
    assert compute_rsi([100 - i for i in range(30)]) == 0.0         # baisse pure
    assert compute_rsi([100, 101]) is None                          # trop court


def test_local_macd_and_bollinger():
    from src.analytics.technical_local import compute_macd, compute_bollinger
    up = [100 + i for i in range(40)]
    assert compute_macd(up) > 0                                     # tendance haussière
    boll = compute_bollinger(up)
    assert boll["position"] in ("upper", "mid")
    assert boll["width_pct"] >= 0
    assert compute_macd([1, 2, 3]) is None                          # trop court


def test_local_technical_available_flag():
    from src.analytics.technical_local import compute_local_technical
    assert compute_local_technical([1, 2, 3])["available"] is False
    out = compute_local_technical([100 + (i % 5) for i in range(40)])
    assert out["available"] is True
    assert out["source"].startswith("local")


def test_local_bullish_divergence_detected():
    """Prix plus-bas plus bas mais RSI plus-bas plus haut = divergence haussière."""
    from src.analytics.technical_local import compute_local_technical
    warm = [100 - i * 0.4 for i in range(16)]
    dropA = [94 - i * 2.2 for i in range(1, 8)]
    bounce = [80, 83, 86, 88, 90]
    dropB = [90 - i * 1.0 for i in range(1, 14)]
    confirm = [78, 81, 84]
    seq = warm + dropA + bounce + dropB + confirm
    assert compute_local_technical(seq)["bullish_divergence"] is True


def test_local_tech_score_direction():
    from src.analytics.technical_local import compute_local_technical, local_tech_score
    up = local_tech_score(compute_local_technical([100 + i for i in range(40)]))
    down = local_tech_score(compute_local_technical([100 - i * 0.5 for i in range(40)]))
    assert up > 50 and down < 50
    assert local_tech_score({"available": False}) is None


# --------------------------------------------------------------------------- #
# #56 — thesis_scoring consomme les sources techniques réelles
# --------------------------------------------------------------------------- #
def test_thesis_reads_real_weekly_rsi():
    """RSI hebdo dans technical.per_tf (TradingView), pas dans tech_advanced."""
    from src.analytics.thesis_scoring import evaluate_thesis_eligibility
    asset = {"technical": {"per_tf": {"1w": {"rsi": 28.0}}},
             "tech_advanced": {"available": False}}
    out = evaluate_thesis_eligibility(asset, tier=2)
    assert any("RSI hebdo" in s["label"] for s in out["signals"])


def test_thesis_reads_local_divergence_and_bollinger():
    from src.analytics.thesis_scoring import evaluate_thesis_eligibility
    asset = {"tech_local": {"available": True, "bullish_divergence": True,
                            "bollinger": {"position": "lower", "width_pct": 5.0}}}
    out = evaluate_thesis_eligibility(asset, tier=3)
    labels = " ".join(s["label"] for s in out["signals"])
    assert "divergence" in labels          # divergence (tech_local)
    assert "Bollinger" in labels           # bollinger local en repli


# --------------------------------------------------------------------------- #
# #54 — onchain_flows réel
# --------------------------------------------------------------------------- #
def test_onchain_flows_score_real():
    from src.main import _onchain_flows_score
    assert _onchain_flows_score(None) is None
    assert _onchain_flows_score({}) is None
    # MVRV < 1 (accumulation) + adresses actives en hausse → > 50.
    hi = _onchain_flows_score({"mvrv": 0.9, "active_addresses_trend_pct": 8.0})
    assert hi > 50
    # MVRV > 3.5 (euphorie) → tiré vers le bas.
    lo = _onchain_flows_score({"mvrv": 4.0})
    assert lo < 50
    # Borné 0-100.
    assert 0 <= _onchain_flows_score({"active_addresses_trend_pct": 999}) <= 100


# --------------------------------------------------------------------------- #
# #55 — macro_alignment (tilt déterministe, plafonné 40-60)
# --------------------------------------------------------------------------- #
def test_macro_alignment_capped_and_none():
    from src.analytics.cross_signals import macro_alignment_score
    risk_on = {
        "m2": {"a": 100, "b": 101, "c": 102, "d": 103},
        "dxy": {f"d{i}": 105 - i for i in range(8)},
        "hy_spread": {f"h{i}": 4.0 - i * 0.1 for i in range(8)},
    }
    risk_off = {
        "m2": {"a": 103, "b": 102, "c": 101, "d": 100},
        "dxy": {f"d{i}": 100 + i for i in range(8)},
        "hy_spread": {f"h{i}": 3.0 + i * 0.1 for i in range(8)},
    }
    assert macro_alignment_score(risk_on, vix=12) == 60.0    # plafonné haut
    assert macro_alignment_score(risk_off, vix=30) == 40.0   # plafonné bas
    assert macro_alignment_score({}, None) is None
    # Jamais convergent : reste dans [50-11, 50+11] donc |dev| < 12.
    val = macro_alignment_score(risk_on, vix=12)
    assert abs(val - 50) < 12


# --------------------------------------------------------------------------- #
# #53 — complétude + plafond de confiance
# --------------------------------------------------------------------------- #
def test_completeness_full_vs_bare():
    from src.analytics.thesis_scoring import compute_completeness
    full = {
        "tech_advanced": {"available": True},
        "onchain": {"mvrv": 1.1, "active_addresses_trend_pct": 2.0},
        "tvl": {"available": True}, "derivatives": {"available": True},
        "social": {"available": True}, "price_series_30d": [1, 2, 3],
    }
    bare = {"change_24h": 5.0}
    cf = compute_completeness(full)
    cb = compute_completeness(bare)
    assert cf["pct"] == 100 and cf["missing"] == []
    assert cb["pct"] == 0 and "on-chain" in cb["missing"]


def test_completeness_cap_and_confidence_bounds():
    from src.analytics.thesis_scoring import completeness_cap, confidence_bounds
    assert completeness_cap(100) == 85
    assert completeness_cap(50) == 65
    assert completeness_cap(20) == 60
    # Une conviction (plafond 85) sur analyse 50% est ramenée à 65.
    cb = confidence_bounds("conviction", dimensions_count=3, completeness_pct=50)
    assert cb["cap"] == 65
    assert cb["completeness_cap"] == 65
    # Sans complétude fournie : comportement historique inchangé.
    assert confidence_bounds("conviction", 3)["cap"] == 80


# --------------------------------------------------------------------------- #
# #60 — coherence_checker borne la confiance au plafond de complétude
# --------------------------------------------------------------------------- #
def test_coherence_caps_confidence():
    from src.analytics.coherence_checker import check_report
    payload = {"thesis_of_the_day": [{
        "asset": "JASMY", "action": "RENFORCER", "confidence": 90,
        "action_plan": {"stop_loss": 0.01, "take_profit": {"30pct": 0.02}},
    }]}
    out = check_report(payload, confidence_caps={"JASMY": 65})
    th = out["sanitized_payload"]["thesis_of_the_day"][0]
    assert th["confidence"] == 65
    assert th["_confidence_capped"] is True
    assert any("plafond complétude" in w for w in out["warnings"])


def test_coherence_no_cap_when_absent():
    from src.analytics.coherence_checker import check_report
    payload = {"thesis_of_the_day": [{
        "asset": "BTC", "action": "RENFORCER", "confidence": 78,
        "action_plan": {"stop_loss": 50000, "take_profit": {"30pct": 70000}},
    }]}
    out = check_report(payload)          # pas de caps fournis
    assert out["sanitized_payload"]["thesis_of_the_day"][0]["confidence"] == 78


def test_confidence_caps_from_data():
    from src.main import _confidence_caps_from_data
    data = {"eligible_theses": [
        {"asset": "eth", "thesis_scoring": {"confidence_bounds": {"cap": 75}}},
        {"asset": "CKB", "thesis_scoring": {"confidence_bounds": {"cap": 60}}},
        {"asset": "NOPE", "thesis_scoring": {}},
    ]}
    caps = _confidence_caps_from_data(data)
    assert caps == {"ETH": 75, "CKB": 60}


# --------------------------------------------------------------------------- #
# INTÉGRATION — câblage réel dans _build_asset_signals (main)
# --------------------------------------------------------------------------- #
def test_build_asset_signals_wires_onchain_and_macro(monkeypatch):
    from src import main
    from src.data_sources import defillama, github_dev, tradingview
    monkeypatch.setattr(tradingview, "get_technical", lambda s: {"available": False})
    monkeypatch.setattr(github_dev, "get_dev_activity", lambda s: {"available": False})
    monkeypatch.setattr(defillama, "get_protocol_tvl", lambda s: {"available": False})
    asset = main._build_asset_signals(
        "ZK", 3, {"price": 0.05, "change_24h": 1.0, "ath": 0.3},
        0.0, 0, None, {"available": False},
        onchain={"mvrv": 0.9, "active_addresses_trend_pct": 6.0},
        macro_alignment=58.0,
    )
    # onchain_flows = score RÉEL (plus la constante 55.0), macro_alignment câblé.
    assert asset["signals"]["onchain_flows"] is not None
    assert asset["signals"]["onchain_flows"] > 50
    assert asset["signals"]["macro_alignment"] == 58.0


def test_build_asset_signals_local_fallback_when_tv_down(monkeypatch):
    """TradingView down + OHLC dispo → tech_local calculé et score technique non nul."""
    from src import main
    from src.data_sources import (
        coingecko, defillama, github_dev, technical_advanced, tradingview,
    )
    monkeypatch.setattr(tradingview, "get_technical", lambda s: {"available": False})
    monkeypatch.setattr(technical_advanced, "get_technical_advanced",
                        lambda s: {"available": False})
    monkeypatch.setattr(github_dev, "get_dev_activity", lambda s: {"available": False})
    monkeypatch.setattr(defillama, "get_protocol_tvl", lambda s: {"available": False})
    prices = [100 + (i % 7) - 3 for i in range(40)]
    monkeypatch.setattr(coingecko, "get_price_volume_series",
                        lambda s, days=30: {"prices": prices, "volumes": [10.0] * 40})
    asset = main._build_asset_signals(
        "ETH", 1, {"price": 100.0, "change_24h": 1.0, "ath": 200.0},
        0.0, 0, None, {"available": False},
    )
    assert asset["tech_local"]["available"] is True
    # Le score technique du composite n'est plus None grâce au repli local.
    assert asset["signals"]["technical_multi_tf"] is not None
