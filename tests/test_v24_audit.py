"""v24 — régressions d'audit des mails (runs du 01/07/2026).

Bugs RÉCURRENTS corrigés à la racine :
  • Polymarket : bruit sport / nom propre classé « crypto » (« Wimbledon WTA :
    Solana Sierra vs Coco Gauff » — le prénom « Solana » matchait le thème). Les
    thèmes étaient matchés en SOUS-CHAÎNE (« netflix » contient « etf »). → mot
    entier partout + blocklist sport/divertissement élargie.
  • Weekly : le label « ★ cœur » était dérivé du TIER d'ANALYSE (1/2), pas d'un
    vrai set cœur → des poussières (RSR, JASMY…) étaient « cœur ». → set
    CORE_ASSETS (BTC/ETH/TAO/LINK) + override portfolio.yaml.
"""

from __future__ import annotations


# --------------------------------------------------------------------------- #
# #1 Polymarket — filtrage robuste (mot entier + sport bloqué)
# --------------------------------------------------------------------------- #
def _passes(q: str) -> bool:
    """Réplique le filtre réel : bloqué sport/divertissement puis tier ≠ None."""
    from src.data_sources.prediction_markets import _BLOCK_RE, _market_tier
    ql = q.lower()
    if _BLOCK_RE.search(ql):
        return False
    return _market_tier(ql) is not None


def test_polymarket_tennis_player_name_not_crypto():
    # Le prénom « Solana » d'une joueuse ne doit PAS classer un match de tennis.
    assert _passes("Wimbledon WTA: Solana Sierra vs Coco Gauff") is False
    assert _passes("ATP Finals: Alcaraz vs Sinner") is False
    assert _passes("Who wins the Roland Garros final?") is False


def test_polymarket_wholeword_no_substring_false_positive():
    from src.data_sources.prediction_markets import _market_tier
    # « netflix » contient « etf » mais n'est pas un marché crypto.
    assert _market_tier("will netflix add 10m subscribers in q3?") is None
    # « toward » contient « war » : \bwar\b ne doit pas matcher.
    assert _market_tier("who leads toward the nomination?") is None


def test_polymarket_real_crypto_macro_geo_still_pass():
    # Les VRAIS marchés pertinents restent gardés (pas de sur-filtrage).
    assert _passes("Will Solana flip Ethereum by 2027?") is True
    assert _passes("Will Bitcoin drop below $50k in 2026?") is True
    assert _passes("US recession in 2026?") is True
    assert _passes("Will the U.S. invade Iran before 2027?") is True


# --------------------------------------------------------------------------- #
# #2 Weekly — ★ cœur = vraie conviction, PAS le tier d'analyse
# --------------------------------------------------------------------------- #
def test_positions_review_core_is_conviction_not_tier():
    from src.main import _build_positions_review
    portfolio = {
        "BTC": {"tier": 1, "pru": 50000},
        "RSR": {"tier": 2, "pru": 0.01},      # tier 2 (analyse) mais SATELLITE
        "JASMY": {"tier": 1, "pru": 0.02},    # tier 1 (analyse) mais SATELLITE
        "LINK": {"tier": 2, "pru": 12},       # CŒUR malgré tier 2
    }
    market = {"BTC": {"price": 60000}, "RSR": {"price": 0.001},
              "JASMY": {"price": 0.004}, "LINK": {"price": 7}}
    long_term = [{"asset": a} for a in ("BTC", "RSR", "JASMY", "LINK")]
    rows = {r["asset"]: r
            for r in _build_positions_review(long_term, [], portfolio, market)}
    assert rows["BTC"]["conviction"] is True
    assert rows["LINK"]["conviction"] is True
    assert rows["RSR"]["conviction"] is False
    assert rows["JASMY"]["conviction"] is False


def test_econ_calendar_parses_filters_zones_and_impact(monkeypatch):
    """v24 — feed ForexFactory : garde USD/EUR/JPY/GBP/CNY + High/Medium,
    écarte Low + zones non crypto + événements hors fenêtre ; date locale + zone."""
    from datetime import date, timedelta
    from src.data_sources import econ_calendar as ec
    from src.utils.cache import CACHE
    d0 = date.today()

    def _iso(days, hhmm="08:30"):
        return f"{(d0 + timedelta(days=days)).isoformat()}T{hhmm}:00-04:00"

    feed = [
        {"title": "Non-Farm Employment Change", "country": "USD", "impact": "High",
         "date": _iso(1), "forecast": "150K", "previous": "139K"},
        {"title": "ISM Manufacturing PMI", "country": "USD", "impact": "Medium",
         "date": _iso(2), "forecast": "", "previous": ""},
        {"title": "Bruit mineur", "country": "USD", "impact": "Low", "date": _iso(1)},
        {"title": "RBA Gov Speaks", "country": "AUD", "impact": "High", "date": _iso(1)},
        {"title": "Vieux truc", "country": "USD", "impact": "High", "date": _iso(-5)},
    ]
    monkeypatch.setattr(ec, "get_json", lambda url, timeout=15: feed)
    monkeypatch.setattr(CACHE, "get_or_compute", lambda k, ttl, fn: fn())
    out = ec.get_econ_calendar(horizon_days=8)
    titles = [e["title"] for e in out["events"]]
    assert out["available"] is True
    assert "Non-Farm Employment Change" in titles     # USD High gardé
    assert "ISM Manufacturing PMI" in titles           # USD Medium gardé
    assert "Bruit mineur" not in titles                # Low écarté
    assert "RBA Gov Speaks" not in titles              # AUD écarté (zone non crypto)
    assert "Vieux truc" not in titles                  # hors fenêtre (passé)
    nfp = next(e for e in out["events"] if "Non-Farm" in e["title"])
    assert nfp["zone"] == "US" and nfp["importance"] == "high"
    assert nfp["forecast"] == "150K" and nfp["label"].endswith("(US)")


def test_consolidated_calendar_uses_ff_and_suppresses_estimate(monkeypatch):
    """v24 — ForexFactory alimente le calendrier consolidé, et la récurrence NFP
    ESTIMÉE est supprimée quand une vraie source couvre déjà la famille nfp."""
    from datetime import date, timedelta
    from src.data_sources import macro_calendar as mc
    from src.data_sources import econ_calendar as ec
    from src.utils.cache import CACHE
    d0 = date.today()
    monkeypatch.setattr(mc.fred, "get_upcoming_releases",
                        lambda horizon_days=8: {"available": False})
    monkeypatch.setattr(mc, "get_boursorama_calendar", lambda: {"available": False})
    monkeypatch.setattr(ec, "get_econ_calendar", lambda horizon_days=8: {
        "available": True, "events": [
            {"label": "Non-Farm Employment Change (US)",
             "date": (d0 + timedelta(days=2)).isoformat(), "importance": "high",
             "time": "08:30", "zone": "US"},
            {"label": "ISM Manufacturing PMI (US)",
             "date": (d0 + timedelta(days=1)).isoformat(), "importance": "medium",
             "time": "10:00", "zone": "US"},
        ]})
    monkeypatch.setattr(CACHE, "get_or_compute", lambda k, ttl, fn: fn())
    out = mc.get_consolidated_calendar(horizon_days=8)
    labels = [e["label"] for e in out["events"]]
    assert "ForexFactory" in out["sources_used"]
    assert any("ISM Manufacturing PMI" in l for l in labels)       # FF medium
    assert any("Non-Farm Employment Change" in l for l in labels)  # FF high
    # la récurrence « Emploi US (NFP) (estimé) » ne doit PAS coexister (famille nfp
    # déjà couverte par ForexFactory) :
    assert not any(("Emploi US (NFP)" in l and "estimé" in l) for l in labels)


def test_polymarket_plurals_still_match():
    """v25 (audit M1) — le mot entier tolère le PLURIEL : « tariffs »,
    « stablecoins » restent classés ; « playoffs » reste bloqué."""
    from src.data_sources.prediction_markets import _BLOCK_RE, _market_tier
    assert _market_tier("will trump's new tariffs on the eu take effect?") == 2
    assert _market_tier("will stablecoins reach a $300b market cap in 2026?") == 1
    assert _market_tier("will memecoins outperform in q3?") == 1
    assert _BLOCK_RE.search("nba playoffs winner 2026?") is not None
    # et le singulier marche toujours
    assert _market_tier("new tariff on china goods?") == 2


def test_calendar_speeches_and_expectations_not_merged():
    """v25 (audit M2/M3) — 2 discours Fed le même jour = 2 lignes ; « Inflation
    Expectations » ≠ CPI ; le libellé FF de la décision rejoint la famille FOMC."""
    from src.data_sources import macro_calendar as mc
    d = "2026-07-29"
    k1 = mc._norm_key("FOMC Member Bowman Speaks (US)", d)
    k2 = mc._norm_key("FOMC Member Williams Speaks (US)", d)
    assert k1 != k2                                   # discours distincts gardés
    assert mc._family("Prelim UoM Inflation Expectations") is None
    # décision FF == décision officielle (dédup du doublon jour J)
    assert mc._norm_key("Federal Funds Rate (US)", d) == \
        mc._norm_key("Décision FOMC (taux Fed)", d)
    assert mc._norm_key("Main Refinancing Rate (Zone euro)", "2026-07-23") == \
        mc._norm_key("Décision BCE (taux zone euro)", "2026-07-23")


def test_calendar_family_dedup_is_zone_aware(monkeypatch):
    """v25 — la dédup par famille distingue les ZONES : un CPI zone euro ne
    fusionne pas avec le CPI US, et un CPI zone euro réel ne supprime PAS la
    récurrence estimée du CPI US."""
    from src.data_sources import macro_calendar as mc
    # clés distinctes pour la même famille dans 2 zones
    k_us = mc._norm_key("Inflation US (CPI)", "2026-07-11")
    k_eu = mc._norm_key("CPI Flash Estimate y/y (Zone euro)", "2026-07-11")
    assert k_us != k_eu
    # même famille + même zone → même clé (dédup intra-zone préservée)
    assert mc._norm_key("Inflation US (CPI)", "2026-07-11") == \
        mc._norm_key("CPI (Consumer Price Index)", "2026-07-11")

    # intégration : CPI zone euro RÉEL présent → l'estimation CPI US survit.
    from datetime import date, timedelta
    from src.data_sources import econ_calendar as ec
    from src.utils.cache import CACHE
    d0 = date.today()
    monkeypatch.setattr(mc.fred, "get_upcoming_releases",
                        lambda horizon_days=8: {"available": False})
    monkeypatch.setattr(mc, "get_boursorama_calendar", lambda: {"available": False})
    monkeypatch.setattr(ec, "get_econ_calendar", lambda horizon_days=8: {
        "available": True, "events": [
            {"label": "CPI Flash Estimate y/y (Zone euro)",
             "date": (d0 + timedelta(days=1)).isoformat(), "importance": "high",
             "zone": "Zone euro"}]})
    monkeypatch.setattr(CACHE, "get_or_compute", lambda k, ttl, fn: fn())
    out = mc.get_consolidated_calendar(horizon_days=40)
    labels = [e["label"] for e in out["events"]]
    assert any("CPI Flash Estimate" in l for l in labels)          # EZ réel gardé
    assert any("Inflation US (CPI)" in l and "estimé" in l
               for l in labels)                                     # US estimé SURVIT


def test_weekly_macro_panorama_bullets_and_fallback():
    """v24 — 'Fil rouge macro' en bullets (liste) ; repli prose si string."""
    from src.reporting import email_html
    h_list = email_html.render(
        {"macro_panorama": ["NFP vendredi = catalyseur taux Fed",
                            "DXY +2.2% = vent de face structurel",
                            "→ prudence mais terrain d'accumulation"]}, "weekly")
    assert "Fil rouge macro" in h_list
    assert "NFP vendredi = catalyseur taux Fed" in h_list
    assert h_list.count("<li") >= 3
    h_str = email_html.render({"macro_panorama": "Régime risk-off, FOMC en juge."},
                              "weekly")
    assert "Régime risk-off, FOMC en juge." in h_str


def test_weekly_strategy_merged_into_action_plan_as_bullets():
    """v24 — 'Stratégie de la semaine' regroupée EN TÊTE du 'Plan d'action',
    en bullets, fond BLANC (plus de bloc noir), avant les actions."""
    from src.reporting import email_html
    p = {
        "strategy_focus": ["Biais neutre/défensif",
                           "Priorité : surveiller le NFP de vendredi",
                           "Bascule si BTC casse 59 433 $"],
        "weekly_action_plan": [
            {"priority": 1, "action": "Renforcer **ETH** sous 1 500 $",
             "rationale": "MVRV bas + support W1"}],
    }
    html = email_html.render(p, "weekly")
    assert "Plan d'action de la semaine" in html
    assert "Stratégie de la semaine" in html
    assert "#1f1e1d" not in html                       # plus de bloc noir
    assert "Priorité : surveiller le NFP de vendredi" in html  # bullet stratégie
    # la stratégie est REGROUPÉE au-dessus des actions
    assert html.index("Stratégie de la semaine") < html.index("Renforcer")
    assert html.count("<li") >= 3                        # 3 bullets stratégie


def test_weekly_strategy_string_backward_compat():
    """Rétro-compat : strategy_focus en string reste rendu (repli prose)."""
    from src.reporting import email_html
    html = email_html.render({"strategy_focus": "Biais défensif. Priorité cash."},
                             "weekly")
    assert "Stratégie de la semaine" in html
    assert "Biais défensif. Priorité cash." in html


def test_weekly_scenarios_triggers_and_bullets():
    """v24 — scénarios : conditions (triggers) + analyse en bullets (points) ;
    repli prose (description) conservé pour rétro-compat."""
    from src.reporting import email_html
    scen = [
        {"type": "bullish", "label": "Haussier", "probability_pct": 30,
         "triggers": ["NFP déçoit (< consensus)", "DXY casse 101,0"],
         "points": ["BTC franchit **82 416 $**", "Saisonnalité juillet favorable"],
         "action": "Alléger les satellites sur la force."},
        {"type": "bearish", "label": "Baissier", "probability_pct": 25,
         "description": "Prose de repli sans points."},
    ]
    html = email_html.render({"scenarios": scen}, "weekly")
    assert "Se déclenche si" in html                       # bloc conditions
    assert "NFP déçoit" in html and "DXY casse 101,0" in html
    assert "<strong>82 416 $</strong>" in html             # md inline dans le bullet
    assert html.count("<li") >= 4                           # 2 triggers + 2 points
    assert "Prose de repli sans points." in html           # rétro-compat description


def test_is_core_asset_single_source_of_truth():
    """Helper unique cœur/satellite (positions review ET exit plan dust)."""
    from src.main import _is_core_asset
    assert _is_core_asset("BTC", {}) is True
    assert _is_core_asset("eth", {"tier": 3}) is True     # casse + tier ignoré
    assert _is_core_asset("RSR", {"tier": 1}) is False     # tier 1 mais satellite
    assert _is_core_asset("RENDER", {"core": True}) is True    # override portfolio
    assert _is_core_asset("TAO", {"core": False}) is False     # override portfolio
    assert _is_core_asset("XYZ", None) is False


def test_positions_review_core_override_via_portfolio():
    from src.main import _build_positions_review
    portfolio = {
        "RENDER": {"tier": 1, "pru": 3, "core": True},    # forcé cœur
        "TAO": {"tier": 1, "pru": 300, "core": False},    # forcé satellite
    }
    market = {"RENDER": {"price": 1.5}, "TAO": {"price": 200}}
    long_term = [{"asset": "RENDER"}, {"asset": "TAO"}]
    rows = {r["asset"]: r
            for r in _build_positions_review(long_term, [], portfolio, market)}
    assert rows["RENDER"]["conviction"] is True
    assert rows["TAO"]["conviction"] is False
