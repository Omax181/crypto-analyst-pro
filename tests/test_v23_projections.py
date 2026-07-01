"""v23.x — ÉCHAFAUDAGE DE PROJECTION des thèses (deepthink Omar 2026-06-29).

Verrouille : (1) le moteur déterministe `compute_price_projection` (cibles ancrées
sur de vrais niveaux + confluence + bande de volatilité réaliste + fourchette LT),
(2) son câblage dans `_merge_python_facts` — taille en $ déterministe ET garde-fou
anti-cible-30j-irréaliste.

Hermétique : pur calcul, aucun réseau.
"""

from __future__ import annotations

from src.analytics.projections import compute_price_projection


# --------------------------------------------------------------------------- #
# Moteur de projection — sélection ancrée
# --------------------------------------------------------------------------- #
def test_short_term_anchors_on_confluence():
    """La cible 30j s'ancre sur une CONFLUENCE (résistance + Fibonacci au même prix)."""
    p = compute_price_projection(
        100.0,
        support_resistance={"resistance": 110.0, "support": 90.0},
        fibonacci={"level_382": 110.3},          # ~ même prix que la résistance
        atr_pct=3.0,
    )
    st = p["short_term_30d"]
    assert st["confluence"] is True
    assert 109.5 <= st["target"] <= 110.7        # moyenne de la confluence
    assert "résistance" in st["basis"] and "Fibonacci" in st["basis"]
    assert st["within_realistic_band"] is True


def test_short_term_falls_back_to_atr_when_resistance_too_far():
    """Aucune résistance sous le plafond réaliste → projection ATR pure (bornée)."""
    p = compute_price_projection(
        100.0,
        support_resistance={"resistance": 200.0, "support": 95.0},  # +100% = hors bande
        atr_pct=3.0,                                                 # ~16.4% attendu 30j
    )
    st = p["short_term_30d"]
    assert "volatilité 30j" in st["basis"]
    assert 14.0 <= st["move_pct"] <= 19.0        # ≈ ATR×√30, pas +100%
    assert st["target"] < 120.0


def test_volatility_band_scales_with_atr():
    """expected_move_30d = ATR×√30 ; plafond réaliste = ×1.5."""
    p = compute_price_projection(100.0, atr_pct=4.0, ath=300.0)
    vol = p["volatility"]
    assert 21.0 <= vol["expected_move_30d_pct"] <= 22.5     # 4×√30 ≈ 21.9
    assert vol["realistic_30d_high_pct"] > vol["expected_move_30d_pct"]


def test_long_term_below_ath_retraces_toward_ath():
    """Sous l'ATH : bas = retracement 0.382 du repli, haut = retour ATH."""
    p = compute_price_projection(100.0, ath=300.0, atr_pct=3.0)
    lt = p["long_term_6_12m"]
    assert lt["high"] == 300.0                              # retour ATH
    assert abs(lt["low"] - (100 + 0.382 * 200)) < 0.6       # ≈ 176.4
    assert "ATH" in lt["basis"]


def test_long_term_extension_when_near_ath():
    """Au contact de l'ATH : extension Fibonacci 1.414 (price discovery)."""
    p = compute_price_projection(300.0, ath=300.0, atr_pct=3.0)
    lt = p["long_term_6_12m"]
    assert lt["low"] == 300.0
    assert abs(lt["high"] - 300 * 1.414) < 0.6             # ≈ 424.2
    assert "extension" in lt["basis"].lower()


def test_short_term_bear_anchors_on_support():
    """Pour une thèse ALLÉGER : cible baissière = support le plus proche."""
    p = compute_price_projection(
        100.0, support_resistance={"resistance": 130.0, "support": 90.0}, atr_pct=3.0,
    )
    bear = p["short_term_30d_bear"]
    assert bear["target"] == 90.0
    assert bear["move_pct"] < 0


def test_stop_suggestion_below_nearest_support():
    """Le stop suggéré est ancré 1% sous le niveau de support le plus proche."""
    p = compute_price_projection(
        100.0, support_resistance={"resistance": 130.0, "support": 92.0}, atr_pct=3.0,
    )
    stop = p["stop_suggestion"]
    assert abs(stop["level"] - 92.0 * 0.99) < 1e-6
    assert "support" in stop["basis"]


def test_projection_graceful_degradation():
    """Prix invalide → indisponible ; aucune donnée exploitable → indisponible."""
    assert compute_price_projection(None) == {"available": False}
    assert compute_price_projection(0) == {"available": False}
    bare = compute_price_projection(100.0)        # ni niveaux, ni ATR, ni ATH
    assert bare["available"] is False
    assert bare["short_term_30d"] is None and bare["long_term_6_12m"] is None


# --------------------------------------------------------------------------- #
# Câblage _merge_python_facts — taille $ + garde-fou cible irréaliste
# --------------------------------------------------------------------------- #
def _tao_projection() -> dict:
    return compute_price_projection(
        207.0,
        support_resistance={"resistance": 248.0, "support": 188.0},
        fibonacci={"level_382": 247.0, "level_500": 215.0, "level_618": 183.0},
        bollinger={"upper": 250.0, "middle": 210.0, "lower": 192.0},
        moving_averages={"sma50": 220.0, "sma200": 300.0},
        ath=757.6, ath_distance_pct=-72.7, atr_pct=4.2, change_30d=12.0,
    )


def test_merge_computes_position_size_usd():
    """La taille en $ est calculée déterministiquement (% × valeur PTF)."""
    from src.main import _merge_python_facts
    proj = _tao_projection()
    payload = {"thesis_of_the_day": [{
        "asset": "TAO", "action": "RENFORCER", "action_type": "bullish", "confidence": 78,
        "targets": {"short_term_30d": 247, "long_term_6_12m_low": 417, "long_term_6_12m_high": 757},
        "action_plan": {"entry": 205, "stop_loss": 190,
                        "stop_loss_basis": "bande basse Bollinger",
                        "position_size_pct": 5, "take_profit": {"30pct": 247}},
    }]}
    data = {"eligible_theses": [{"asset": "TAO", "projection": proj}],
            "portfolio_snapshot": {"value_usd": 2334.0, "usdc_usd": 0.0}}
    out = _merge_python_facts(payload, data, "29/06 08:00")
    ap = out["thesis_of_the_day"][0]["action_plan"]
    assert ap["position_size_usd"] == round(0.05 * 2334.0)        # 117


def test_merge_clamps_unrealistic_short_term_target():
    """Une cible 30j fantaisiste (+380%) est ramenée à la cible ancrée réaliste."""
    from src.main import _merge_python_facts
    proj = _tao_projection()
    anchored = proj["short_term_30d"]["target"]                  # ~247.5 (confluence)
    payload = {"thesis_of_the_day": [{
        "asset": "TAO", "action": "RENFORCER", "action_type": "bullish", "confidence": 78,
        "targets": {"short_term_30d": 999, "short_term_note": "lune",
                    "long_term_6_12m_low": 417, "long_term_6_12m_high": 757},
        "action_plan": {"entry": 205, "stop_loss": 190,
                        "stop_loss_basis": "bande basse Bollinger",
                        "position_size_pct": 4, "take_profit": {"30pct": 247}},
    }]}
    data = {"eligible_theses": [{"asset": "TAO", "projection": proj}],
            "portfolio_snapshot": {"value_usd": 2000.0, "usdc_usd": 0.0}}
    out = _merge_python_facts(payload, data, "29/06 08:00")
    tg = out["thesis_of_the_day"][0]["targets"]
    assert tg["short_term_30d"] == anchored
    assert tg["short_term_30d_capped"] is True
    assert "réaliste" in tg["short_term_note"]


def test_merge_keeps_realistic_short_term_target():
    """Une cible 30j RAISONNABLE (dans la bande) n'est PAS modifiée."""
    from src.main import _merge_python_facts
    proj = _tao_projection()
    payload = {"thesis_of_the_day": [{
        "asset": "TAO", "action": "RENFORCER", "action_type": "bullish", "confidence": 78,
        "targets": {"short_term_30d": 245, "long_term_6_12m_low": 417, "long_term_6_12m_high": 757},
        "action_plan": {"entry": 205, "stop_loss": 190,
                        "stop_loss_basis": "bande basse Bollinger",
                        "position_size_pct": 3, "take_profit": {"30pct": 245}},
    }]}
    data = {"eligible_theses": [{"asset": "TAO", "projection": proj}],
            "portfolio_snapshot": {"value_usd": 2000.0, "usdc_usd": 0.0}}
    out = _merge_python_facts(payload, data, "29/06 08:00")
    tg = out["thesis_of_the_day"][0]["targets"]
    assert tg["short_term_30d"] == 245
    assert "short_term_30d_capped" not in tg
