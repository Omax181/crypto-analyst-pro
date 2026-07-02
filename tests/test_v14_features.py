"""Tests des fonctionnalités v14 (corrections + restructuration evening).

Couvre :
- fmt_money (format français 69.637,63 $)
- _parse_num (parsing robuste)
- _build_evening_reco_bilan (recos fermes seules + dédup + statuts explicites)
- add_recommendation (dédup anti-doublons par actif+action)
- OKX fallback Binance (géo-block 451)
- config : série or FRED retirée
- rendu evening 8 blocs (enrichi + dégradé)
- macro arrow / flèche 24h (seuil adaptatif via rendu)
"""

from __future__ import annotations

import json
import os
import re


# --------------------------------------------------------------------------- #
# fmt_money — v23 (C2) : format ANGLO unifié avec fmt_price ($ préfixe)
# --------------------------------------------------------------------------- #
def test_fmt_money_anglo_format():
    from src.reporting.email_html import _fmt_money
    assert _fmt_money(69637.63) == "$69,637.63"
    assert _fmt_money(63180) == "$63,180.00"
    assert _fmt_money("63180") == "$63,180.00"
    assert _fmt_money("1,679.33") == "$1,679.33"
    assert _fmt_money(7.94) == "$7.94"
    # parsing FR encore toléré en ENTRÉE (rétro-compat), sortie anglo
    assert _fmt_money("69.637,63 $") == "$69,637.63"


def test_fmt_money_edge_cases():
    from src.reporting.email_html import _fmt_money
    assert _fmt_money(None) == "—"
    assert _fmt_money("marché") == "marché"   # non parsable → inchangé
    assert _fmt_money(0) == "$0"
    assert _fmt_money(-1512.07) == "−$1,512.07"
    # micro-prix : pas de notation scientifique, préfixe $
    out = _fmt_money(0.00000001)
    assert "e" not in out.lower() and out.startswith("$")


# --------------------------------------------------------------------------- #
# _parse_num
# --------------------------------------------------------------------------- #
def test_parse_num():
    from src.main import _parse_num
    assert _parse_num(63180) == 63180.0
    assert _parse_num("63180") == 63180.0
    assert _parse_num("1,679.33") == 1679.33
    assert _parse_num("63 180 $") == 63180.0
    assert _parse_num(None) is None
    assert _parse_num("n/d") is None


# --------------------------------------------------------------------------- #
# _build_evening_reco_bilan — BLOC 6
# --------------------------------------------------------------------------- #
def _morning_with_theses():
    return {"thesis_of_the_day": [
        {"asset": "BTC", "action": "RENFORCER",
         "action_plan": {"entry": "63180", "stop_loss": "60500"}},
        {"asset": "ETH", "action": "RENFORCER",
         "action_plan": {"entry": "1679", "stop_loss": "1500"}},
        {"asset": "ADA", "action": "RENFORCER",
         "action_plan": {"entry": "0.16", "stop_loss": "0.155"}},
        {"asset": "LINK", "action": "SURVEILLER"},
        # doublon d'actif (BTC) : ne doit produire qu'UNE ligne BTC
        {"asset": "BTC", "action": "RENFORCER",
         "action_plan": {"entry": "63180"}},
    ]}


def test_reco_bilan_dedup_one_row_per_asset():
    from src.main import _build_evening_reco_bilan
    market = {"BTC": {"price": 63600}, "ETH": {"price": 1650},
              "ADA": {"price": 0.150}, "LINK": {"price": 7.9}}
    bilan = _build_evening_reco_bilan(_morning_with_theses(), market)
    # v23.x : seules les recos FERMES (5 thèses dont 1 SURVEILLER + 1 BTC doublon)
    # -> 3 lignes (BTC dédupliqué, ETH, ADA). LINK (SURVEILLER) n'apparaît plus.
    assert len(bilan) == 3
    assets = [r["asset"] for r in bilan]
    assert assets.count("BTC") == 1
    assert "LINK" not in assets


def test_reco_bilan_statuses():
    from src.main import _build_evening_reco_bilan
    market = {"BTC": {"price": 63600}, "ETH": {"price": 1650},
              "ADA": {"price": 0.150}, "LINK": {"price": 7.9}}
    bilan = {r["asset"]: r for r in _build_evening_reco_bilan(_morning_with_theses(), market)}
    assert bilan["BTC"]["status"] == "on_track"        # +0.66%
    assert bilan["ETH"]["status"] == "under_pressure"  # -1.73%, au-dessus du SL
    assert bilan["ADA"]["status"] == "invalidated"     # sous le SL 0.155
    assert "LINK" not in bilan                          # v23.x : SURVEILLER filtré
    assert bilan["BTC"]["delta_pct"] == 0.66
    # v23.x : le statut porte une raison utile + le niveau d'invalidation.
    assert "invalidé sous" in bilan["ETH"]["reason"]
    assert "$1,500" in bilan["ETH"]["reason"]


def test_reco_bilan_alleger_bearish_logic():
    from src.main import _build_evening_reco_bilan
    # ALLÉGER (baissier) : on track si le prix BAISSE
    morning = {"thesis_of_the_day": [
        {"asset": "TAO", "action": "ALLÉGER", "action_plan": {"entry": "300", "stop_loss": "340"}}]}
    bilan = _build_evening_reco_bilan(morning, {"TAO": {"price": 280}})
    assert bilan[0]["status"] == "on_track"  # prix baissé -> bon pour un allègement


def test_reco_bilan_firm_postures_enrichis():
    """v23.x : firm_postures (source de vérité) -> cible, confiance, raison ;
    les SURVEILLER sont filtrés."""
    from src.main import _build_evening_reco_bilan
    morning = {
        "firm_postures": {
            "ETH": {"action": "RENFORCER", "entry": 1600, "stop_loss": 1500,
                    "target": 1800, "confidence": 78.0}},
        "thesis_of_the_day": [{"asset": "CKB", "action": "SURVEILLER"}],  # ignoré
    }
    bilan = _build_evening_reco_bilan(morning, {"ETH": {"price": 1650}})
    assert len(bilan) == 1
    row = bilan[0]
    assert row["asset"] == "ETH"
    assert row["target"] == 1800
    assert row["confidence"] == 78.0
    assert row["status"] == "on_track"           # 1650 > entrée 1600
    assert "invalidé sous" in row["reason"]


def test_reco_bilan_empty_renders_ras():
    """v23.x : aucune reco ferme -> ligne courte « RAS », pas de tableau vide."""
    from src.reporting.email_html import render
    html = render({"reco_bilan": []}, "evening")
    assert "Recos du matin" in html
    assert "Aucune reco active" in html


# --------------------------------------------------------------------------- #
# add_recommendation — dédup anti-doublons (fix tracker)
# --------------------------------------------------------------------------- #
def test_tracker_dedup_same_asset_action(monkeypatch=None):
    import src.state.report_memory as rm
    store: dict = {}
    rm._read = lambda f, default: store.get(f, default if default is not None else [])
    rm._write = lambda f, data: store.__setitem__(f, data)
    # 3 mornings BTC RENFORCER (ids datés différents) -> 1 seule reco
    for day in ("05", "06", "08"):
        rm.add_recommendation({"id": f"BTC-2026-06-{day}-RENFORCER", "asset": "BTC",
                               "action": "RENFORCER", "entry_price": 60000 + int(day) * 10})
    recos = rm.load_active_recommendations()
    btc = [r for r in recos if r["asset"] == "BTC"]
    assert len(btc) == 1
    assert btc[0]["entry_price"] == 60050  # prix d'entrée d'origine conservé


def test_tracker_action_change_archives():
    import src.state.report_memory as rm
    store: dict = {}
    rm._read = lambda f, default: store.get(f, default if default is not None else [])
    rm._write = lambda f, data: store.__setitem__(f, data)
    rm.add_recommendation({"id": "ETH-1-RENFORCER", "asset": "ETH",
                           "action": "RENFORCER", "entry_price": 1600})
    rm.add_recommendation({"id": "ETH-2-ALLEGER", "asset": "ETH",
                           "action": "ALLEGER", "entry_price": 1700})
    recos = rm.load_active_recommendations()
    eth = [r for r in recos if r["asset"] == "ETH"]
    assert len(eth) == 1
    assert eth[0]["action"] == "ALLEGER"  # remplacé après changement d'avis


# --------------------------------------------------------------------------- #
# OKX fallback (Binance géo-bloqué 451)
# --------------------------------------------------------------------------- #
def test_binance_okx_fallback():
    from src.data_sources import binance_futures as bf

    def fake_get_json(url, params=None, **kw):
        if "fapi.binance.com" in url:
            return None  # 451 géo-block
        if "funding-rate" in url:
            return {"data": [{"instId": "BTC-USDT-SWAP", "fundingRate": "0.00012"}]}
        if "mark-price" in url:
            return {"data": [{"markPx": "63600.5"}]}
        if "open-interest" in url:
            return {"data": [{"oi": "12345", "oiCcy": "67.5"}]}
        return None

    bf.get_json = fake_get_json
    if hasattr(bf.CACHE, "_store"):
        bf.CACHE._store.clear()
    out = bf.get_derivatives("BTC")
    assert out.get("available") is True
    assert out.get("source") == "OKX"
    assert out.get("funding_rate_pct") == 0.012
    assert out.get("open_interest") == 67.5


# --------------------------------------------------------------------------- #
# config : série or FRED retirée (corrige le 400)
# --------------------------------------------------------------------------- #
def test_fred_gold_series_removed():
    from src.utils.portfolio_loader import load_config
    sources = load_config("sources")
    assert "gold" not in sources.get("fred_series", {})


# --------------------------------------------------------------------------- #
# Rendu evening 8 blocs (enrichi)
# --------------------------------------------------------------------------- #
def _enriched_evening_payload():
    return {
        "header": {"time_casablanca": "lundi · 20:00", "morning_time_label": "08h32",
                   "since_morning_label": "il y a 11h"},
        "portfolio_snapshot": {"value_usd": 1734, "change_since_morning_pct": 0.6},
        "daily_pnl": {"value_usd": 1734, "day_change_usd": 10, "day_change_pct": 0.6,
                      "top_movers": [{"symbol": "IMX", "change": 11.9, "pnl_usd": 2}]},
        "risk_score": {"score": 7.2, "level": "élevé", "level_color": "#A32D2D"},
        "evening_macro": {"btc_price": 63600, "btc_change_24h": 2.8, "fear_greed": 8,
                          "gold_usd": 4354, "sp500": 7384, "sp500_delta": 31,
                          "nasdaq": 25709, "nasdaq_delta": -23, "vix": 18.8,
                          "brent_usd": 94.56, "dxy": 99.91, "dxy_broad": 118.88},
        "market_changes": [{"status": "invalidated", "description": "Escalade invalidée.",
                            "source": "FT 12h48"}],
        "news_today": [{"title": "News X", "source": "FT", "time": "12h48",
                        "impact": "Réduit le risque.", "status": "intégré"}],
        "reco_bilan": [
            {"asset": "BTC", "action": "RENFORCER", "entry": 63180.0, "current": 63600.0,
             "delta_pct": 0.66, "status": "on_track"},
            {"asset": "CKB", "action": "RENFORCER", "entry": 0.00112, "current": 0.00115,
             "delta_pct": 2.68, "status": "on_track"}],
        "levels_tonight": [{"asset": "BTC", "level": "63 000 $", "type": "support",
                            "trigger": "sous 63k → 62k"}],
        "tomorrow_checklist": {"calendar": "PPI", "checks": "DXY < 100 ?",
                               "scenario": "consolidation", "invalidation": "BTC < 62k"},
        "footer": {"next_morning_time": "08h30"},
    }


def test_evening_render_8_blocs():
    from src.reporting.email_html import render
    html = render(_enriched_evening_payload(), "evening")
    assert not re.search(r"\{\{|\{%", html)          # pas de Jinja non rendu
    assert "rendu simplifié" not in html             # pas de fallback
    assert "Crypto Analyst Pro · v25" in html        # versioning
    # blocs présents
    assert "Bilan du jour" in html
    assert "Marchés · mi-séance" in html
    assert "Ce qui a évolué" in html
    assert "Recos du matin" in html
    assert "Niveaux à surveiller" in html
    assert "Demain matin" in html
    # fmt_money dans le bilan recos
    assert "$63,180.00" in html
    # statut coloré
    assert "on track" in html


def test_evening_render_balanced_tags_no_none():
    from src.reporting.email_html import render
    html = render(_enriched_evening_payload(), "evening")
    for tag in ("table", "tr", "td", "div", "p", "span", "h2"):
        o = len(re.findall(rf"<{tag}[ >]", html))
        c = len(re.findall(rf"</{tag}>", html))
        assert o == c, f"balise <{tag}> déséquilibrée {o}/{c}"
    body = re.sub(r"<[^>]+>", "", html)
    assert not re.search(r"\bNone\b", body)


def test_evening_render_degraded_payloads():
    from src.reporting.email_html import render
    for pl in ({}, {"header": {"date": "X"}}, {"reco_bilan": [], "levels_tonight": []}):
        html = render(pl, "evening")
        assert "rendu simplifié" not in html
        assert not re.search(r"\{\{|\{%", html)


def test_evening_subcent_price_shows_dash():
    """CKB (<0.01) : delta % affiché (arrondi 1 décimale), prix sub-cent non formaté."""
    from src.reporting.email_html import render
    html = render(_enriched_evening_payload(), "evening")
    assert "CKB" in html
    assert "+2.7%" in html  # fmt_pct arrondit 2.68 -> +2.7%


# --------------------------------------------------------------------------- #
# Rendu morning : flèches 24h + réorg + fmt_money plan
# --------------------------------------------------------------------------- #
def _morning_payload():
    return {
        "header": {"date": "lundi", "active_sources_count": 16, "total_sources_count": 23},
        "portfolio_snapshot": {"value_usd": 1700},
        "story_of_the_day": {"narrative": "Rebond technique du marché."},
        "executive_summary": "Prudence macro.",
        "macro_context": {"btc_price": 63180, "btc_change_24h": 2.17, "dxy": 99.92,
                          "dxy_delta": -0.15, "sp500": 7384, "sp500_delta": 31,
                          "nasdaq": 25709, "nasdaq_delta": -23, "vix": 18.8, "vix_delta": -2.1,
                          "fear_greed": 8, "polymarket_fed_cut_pct": 0.2},
        "thesis_of_the_day": [{
            "asset": "BTC", "action": "RENFORCER", "action_type": "bullish", "confidence": 65,
            "reliability": "partielle",
            "targets": {"short_term_30d": "70990", "long_term_6_12m_low": "82752"},
            "action_plan": {"entry": "63180",
                            "take_profit": {"30pct": "68214.17", "30pct_b": "70990", "40pct": "73765.83"},
                            "stop_loss": "60500", "rr": "2.7:1"}}],
    }


def test_morning_reorder_histoire_before_enbref():
    # v16 — « L'histoire du jour » est SUPPRIMÉE ; EN BREF est le seul résumé.
    from src.reporting.email_html import render
    html = render(_morning_payload(), "morning")
    assert "histoire du jour" not in html.lower()  # bloc retiré en v16
    assert "en bref" in html.lower()               # EN BREF conservé


def test_morning_plan_fmt_money():
    from src.reporting.email_html import render
    html = render(_morning_payload(), "morning")
    assert "$63,180.00" in html   # entrée (plan d'action)
    assert "$60,500.00" in html   # SL (plan d'action)
    # v17 (M-B2) : le « Take profit » a été retiré du plan d'action (doublon des
    # cibles). La cible CT 30j reste affichée dans l'encadré cibles à droite.
    assert "$70,990.00" in html   # cible CT 30j (targets.short_term_30d)
    assert "Take profit :" not in html  # plus de TP dupliqué dans le plan


def test_morning_arrows_and_plural_and_polymarket():
    from src.reporting.email_html import render
    html = render(_morning_payload(), "morning")
    # v18 (M-A16) : la tuile BTC affiche désormais un Δ24h CHIFFRÉ (« +2.2% »)
    # au lieu d'une simple flèche. On vérifie ce % + la présence de flèches sur
    # les indices dont le mouvement dépasse le seuil (Nasdaq −23 → ▼).
    assert "+2.2%" in html                          # Δ24h BTC chiffré (M-A16)
    assert "▼" in html                              # flèche down (Nasdaq)
    assert "données partielles" in html             # pluriel
    assert "maintien" in html and "99.8%" in html    # Polymarket reframé
    assert "Crypto Analyst Pro · v25" in html


# ─────────────────── v14 AUDIT HARDENING TESTS ─────────────────── #
def test_filters_non_finite_never_crash():
    """nan/inf ne doivent jamais crasher ni afficher 'nan'/'inf' (sinon fallback laid)."""
    from src.reporting.email_html import _fmt_money, _fmt_price, _fmt_pct, _fmt_vol, _num
    for v in (float("nan"), float("inf"), float("-inf"), "inf", "nan"):
        for fn in (_fmt_money, _fmt_price, _fmt_pct, _fmt_vol):
            out = fn(v)  # ne doit pas lever
            assert "inf" not in out.lower() and "nan" not in out.lower()
        assert _num(v, 0) == 0  # non-fini -> default


def test_parse_num_rejects_non_finite():
    from src.main import _parse_num
    assert _parse_num(float("nan")) is None
    assert _parse_num(float("inf")) is None
    assert _parse_num("inf") is None


def test_strict_60_filter_all_below_shows_empty_reason():
    """Toutes les thèses < 60% -> aucune affichée + thesis_empty_reason (pas la 'meilleure')."""
    from src.main import _merge_python_facts
    pl = {"thesis_of_the_day": [
        {"asset": "BTC", "action": "RENFORCER", "action_type": "bullish", "confidence": 58},
        {"asset": "ETH", "action": "SURVEILLER", "action_type": "neutral", "confidence": 45}]}
    out = _merge_python_facts(dict(pl), {"eligible_theses": []}, "2026-06-09")
    assert len(out.get("thesis_of_the_day") or []) == 0
    assert out.get("thesis_empty_reason")


def test_evening_delta_summary_rendered():
    from src.reporting.email_html import render
    html = render({"delta_summary": ["Un", "Deux", "Trois"], "footer": {}}, "evening")
    assert "À retenir" in html
    assert "Un" in html and "Trois" in html


def test_no_grid_flex_in_templates():
    """Gmail/Outlook ne supportent pas grid/flex : 0 occurrence dans les templates."""
    import os
    base = os.path.join(os.path.dirname(__file__), "..", "src", "reporting", "templates")
    for tpl in ("report_morning.html.j2", "report_evening.html.j2", "report_weekly.html.j2"):
        content = open(os.path.join(base, tpl), encoding="utf-8").read()
        assert "display:grid" not in content and "display: grid" not in content
        assert "display:flex" not in content and "display: flex" not in content


def test_morning_ct_lt_is_table_not_grid():
    from src.reporting.email_html import render
    pl = {"thesis_of_the_day": [{"asset": "BTC", "action": "RENFORCER", "action_type": "bullish",
          "confidence": 70, "targets": {"short_term_30d": "70000", "long_term_6_12m_low": "82000"}}]}
    html = render(pl, "morning")
    assert "Tactique court terme" in html or "Positionnement LT" in html
    assert "display:grid" not in html


# =========================================================================== #
# v14 AUDIT FINAL — régressions sur les 9 bugs corrigés
# =========================================================================== #
class _PassthroughCache:
    """Cache factice : exécute toujours compute (isole les tests du TTLCache)."""

    def get_or_compute(self, key, ttl, compute):
        return compute()

    def get(self, key):
        return None

    def set(self, key, value, ttl):
        pass


# --------------------------------------------------------------------------- #
# BUG #2 — coingecko : alias "prices" == "closes"
# --------------------------------------------------------------------------- #
def test_coingecko_series_exposes_prices_alias(monkeypatch):
    from src.data_sources import coingecko

    monkeypatch.setattr(coingecko, "CACHE", _PassthroughCache())
    monkeypatch.setattr(
        coingecko, "get_json",
        lambda *a, **k: {
            "prices": [[0, 1.0], [0, 2.0], [0, 3.0]],
            "total_volumes": [[0, 10.0], [0, 20.0], [0, 30.0]],
        },
    )
    out = coingecko.get_price_volume_series("BTC", days=30)
    assert out is not None
    assert out["closes"] == [1.0, 2.0, 3.0]
    # L'alias qui répare la corrélation des positions (main.py lit "prices").
    assert out["prices"] == out["closes"]
    assert out["volumes"] == [10.0, 20.0, 30.0]


# --------------------------------------------------------------------------- #
# BUG #3 — tri des thèses : confiance string "72%" ne crash plus
# --------------------------------------------------------------------------- #
def test_merge_python_facts_sorts_string_confidence():
    from src.main import _merge_python_facts

    payload = {
        "thesis_of_the_day": [
            # v23.x — confiances ≥ 75% (seuil d'affichage) pour rester visibles ;
            # le test vérifie le TRI avec confiance en STRING (pas de TypeError).
            {"asset": "A", "action_type": "neutral", "confidence": "85%"},
            {"asset": "B", "action_type": "bullish", "confidence": "82%"},
            {"asset": "C", "action_type": "bearish", "confidence": 90},
        ]
    }
    # Avant le fix : TypeError (bad operand type for unary -: 'str') au tri.
    out = _merge_python_facts(payload, {}, "10/06/2026 · 08h30")
    theses = out["thesis_of_the_day"]
    # action (bullish/bearish) d'abord, par confiance décroissante, puis watch.
    assert [t["asset"] for t in theses] == ["C", "B", "A"]


# --------------------------------------------------------------------------- #
# BUG #4 — label F&G français + delta produits par _macro_context
# --------------------------------------------------------------------------- #
def test_fng_label_fr_paliers():
    from src.main import _fng_label_fr

    assert _fng_label_fr(10) == "Peur extrême"
    assert _fng_label_fr(25) == "Peur extrême"
    assert _fng_label_fr(40) == "Peur"
    assert _fng_label_fr(50) == "Neutre"
    assert _fng_label_fr(70) == "Avidité"
    assert _fng_label_fr(90) == "Avidité extrême"
    assert _fng_label_fr(None) is None
    assert _fng_label_fr("n/d") is None


def test_macro_context_produces_fng_label_and_delta():
    from src.main import _macro_context

    ctx = _macro_context(
        market={"BTC": {"price": 50000}},
        fng={"available": True, "value": 30, "delta": -4},
        macro={},
        polymarket={},
        yahoo_quotes={},
    )
    assert ctx["fear_greed"] == 30
    assert ctx["fear_greed_label"] == "Peur"
    assert ctx["fear_greed_delta"] == -4


# --------------------------------------------------------------------------- #
# BUG #1 — YouTube : compat youtube-transcript-api 0.6.x ET >= 1.0
# --------------------------------------------------------------------------- #
def _install_fake_yta(monkeypatch, module):
    import sys
    import types

    fake = types.ModuleType("youtube_transcript_api")
    fake.YouTubeTranscriptApi = module
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", fake)


def test_youtube_transcript_legacy_api(monkeypatch):
    from src.data_sources import youtube

    class LegacyApi:
        @staticmethod
        def get_transcript(video_id, languages=None):
            assert video_id == "vid1"
            return [{"text": "bonjour"}, {"text": "le marché"}]

    _install_fake_yta(monkeypatch, LegacyApi)
    assert youtube._get_transcript("vid1", ["fr"]) == "bonjour le marché"


def test_youtube_transcript_new_api(monkeypatch):
    from src.data_sources import youtube

    class Snippet:
        def __init__(self, text):
            self.text = text

    class NewApi:  # pas de get_transcript : reproduit la 1.2.4 installée
        def fetch(self, video_id, languages=None):
            assert video_id == "vid2"
            return [Snippet("btc"), Snippet("casse"), Snippet("80k")]

    _install_fake_yta(monkeypatch, NewApi)
    assert youtube._get_transcript("vid2", ["fr", "en"]) == "btc casse 80k"


def test_youtube_recent_videos_metadata_and_publishedafter(monkeypatch):
    """v14.1 : la playlist uploads (1 unité de quota) est tentée d'abord ;
    si elle échoue (None), repli sur search.list (100 unités) avec un
    publishedAfter RFC 3339 strict."""
    from src.data_sources import youtube

    captured = {}

    def fake_get_json(url, params=None, **kw):
        if url.endswith("/playlistItems"):
            # Échec playlist (ex. quota/erreur réseau) → repli search attendu.
            assert params["playlistId"] == "UUX"  # UC → UU (uploads)
            return None
        captured.update(params or {})
        return {
            "items": [
                {"id": {"videoId": "abc"},
                 "snippet": {"title": "BTC analyse", "description": "support 60k"}},
                {"id": {}},  # entrée sans videoId : ignorée
            ]
        }

    monkeypatch.setattr(youtube, "get_json", fake_get_json)
    vids = youtube._recent_videos("UCX", "key", 24, 2)
    assert vids == [{"id": "abc", "title": "BTC analyse",
                     "description": "support 60k"}]
    # RFC 3339 strict : pas de microsecondes, suffixe Z.
    pa = captured["publishedAfter"]
    assert pa.endswith("Z") and "." not in pa and "+00:00" not in pa


def test_youtube_recent_videos_playlist_path_saves_quota(monkeypatch):
    """v14.1 : quand la playlist uploads répond, search.list n'est JAMAIS
    appelé (économie ×100 du quota) et le filtre de fenêtre temporelle
    s'applique sur publishedAt."""
    from datetime import datetime, timedelta, timezone
    from src.data_sources import youtube

    calls = {"search": 0}
    recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(hours=80)).isoformat()

    def fake_get_json(url, params=None, **kw):
        if url.endswith("/playlistItems"):
            return {"items": [
                {"snippet": {"resourceId": {"videoId": "v1"}, "title": "Frais",
                             "description": "d", "publishedAt": recent}},
                {"snippet": {"resourceId": {"videoId": "v2"}, "title": "Vieux",
                             "description": "d", "publishedAt": old}},
            ]}
        calls["search"] += 1
        return {"items": []}

    monkeypatch.setattr(youtube, "get_json", fake_get_json)
    vids = youtube._recent_videos("UCX", "key", 24, 5)
    assert [v["id"] for v in vids] == ["v1"]  # le vieux est filtré
    assert calls["search"] == 0  # search.list jamais payé


def test_youtube_corpus_falls_back_to_titles(monkeypatch):
    """Transcripts bloqués (IP datacenter) -> repli titres/descriptions,
    source DISPONIBLE au lieu d'absente (cause du 'YouTube jamais cité')."""
    from src.data_sources import youtube

    monkeypatch.setattr(youtube, "CACHE", _PassthroughCache())
    monkeypatch.setattr(youtube, "_api_key", lambda: "k")
    monkeypatch.setattr(youtube, "_all_channel_names", lambda: ["Chaine X"])
    monkeypatch.setattr(youtube, "_resolve_channel_id", lambda n, k: "UCX")
    monkeypatch.setattr(
        youtube, "_recent_videos",
        lambda cid, k, age, n: [
            {"id": "v1", "title": "ETH vers 2000 ?", "description": "niveaux clés"},
        ],
    )
    monkeypatch.setattr(youtube, "_get_transcript", lambda vid, langs: None)
    corpus = youtube.get_youtube_corpus()
    assert corpus["available"] is True
    assert corpus["mode"] == "titles"
    assert corpus["video_count"] == 0 and corpus["videos_seen"] == 1
    assert "ETH vers 2000 ?" in corpus["transcripts"][0]


def test_youtube_corpus_prefers_transcripts(monkeypatch):
    from src.data_sources import youtube

    monkeypatch.setattr(youtube, "CACHE", _PassthroughCache())
    monkeypatch.setattr(youtube, "_api_key", lambda: "k")
    monkeypatch.setattr(youtube, "_all_channel_names", lambda: ["Chaine X"])
    monkeypatch.setattr(youtube, "_resolve_channel_id", lambda n, k: "UCX")
    monkeypatch.setattr(
        youtube, "_recent_videos",
        lambda cid, k, age, n: [{"id": "v1", "title": "t", "description": "d"}],
    )
    monkeypatch.setattr(
        youtube, "_get_transcript", lambda vid, langs: "transcript complet " * 10
    )
    corpus = youtube.get_youtube_corpus()
    assert corpus["available"] is True
    assert corpus["mode"] == "transcripts"
    assert corpus["video_count"] == 1


def test_youtube_handle_sanitization(monkeypatch):
    """'Heu?reka' -> @heureka, 'HugoDécrypte' -> @hugodecrypte (forHandle)."""
    from src.data_sources import youtube

    monkeypatch.setattr(youtube, "CACHE", _PassthroughCache())
    # v23 — neutralise les channel_ids EPINGLES (sinon court-circuit avant le
    # chemin forHandle que ce test vise specifiquement a verifier).
    monkeypatch.setattr(youtube, "_YT_CONF", {})
    seen = []

    def fake_get_json(url, params=None, **kw):
        seen.append((url, dict(params or {})))
        if "channels" in url:
            return {"items": [{"id": "UCxyz"}]}
        return {"items": []}

    monkeypatch.setattr(youtube, "get_json", fake_get_json)
    assert youtube._resolve_channel_id("Heu?reka", "k") == "UCxyz"
    assert seen[0][1]["forHandle"] == "@heureka"
    seen.clear()
    assert youtube._resolve_channel_id("HugoDécrypte", "k") == "UCxyz"
    assert seen[0][1]["forHandle"] == "@hugodecrypte"


# --------------------------------------------------------------------------- #
# BUG #9 — workflows : secrets effectivement transmis
# --------------------------------------------------------------------------- #
def test_workflows_forward_coinmetrics_key():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    for wf in ("morning_report.yml", "evening_report.yml", "weekly_report.yml"):
        text = (root / wf).read_text(encoding="utf-8")
        assert "COINMETRICS_API_KEY" in text, wf
        assert "COINGLASS_PAID" in text, wf


# --------------------------------------------------------------------------- #
# BUG #5 — footer hebdo : 12:00 Casablanca (cron dimanche 11h UTC)
# --------------------------------------------------------------------------- #
def test_weekly_footer_time_is_noon():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "src" / "main.py").read_text(
        encoding="utf-8"
    )
    assert "12:00 Casablanca" in src
    assert "15:00 Casablanca" not in src


# --------------------------------------------------------------------------- #
# Rendu — régressions visuelles des fixes (BUG #4/#6/#7/#8 + dégradé)
# --------------------------------------------------------------------------- #
def test_render_evening_fng_label_and_arrow():
    from src.reporting.email_html import render

    html = render(
        {"evening_macro": {"btc_price": 61786.22, "btc_change_24h": -2.1,
                           "fear_greed": 30, "fear_greed_label": "Peur",
                           "fear_greed_delta": -4}},
        "evening",
    )
    assert ">Peur<" in html
    zone = html[html.find(">30"): html.find(">30") + 220]
    assert "▼" in zone  # flèche 24h sur le F&G


def test_render_weekly_week_number_dynamic_year():
    from src.reporting.email_html import render

    html = render({"header": {"week_number": 24, "year": 2027,
                              "date": "dimanche 14 juin"}}, "weekly")
    assert "semaine 24/2027" in html


def test_render_morning_md_and_outlook_tables():
    from src.reporting.email_html import render

    html = render(
        {
            "executive_summary": "Marché en **consolidation**.",
            "market_movers": {
                "available": True,
                "gainers": [{"symbol": "CKB", "change_24h": 7.8}],
                "losers": [{"symbol": "TAO", "change_24h": -6.2}],
            },
        },
        "morning",
    )
    assert "<strong>consolidation</strong>" in html  # |md appliqué
    assert "float:right" not in html                  # compat Outlook
    # v23.x — la section « top mouvements marché » a été retirée du morning :
    # ni l'en-tête ni les tickers hors PTF ne doivent apparaître.
    assert "Top mouvements marché" not in html
    assert "CKB" not in html


def test_render_evening_micro_price_not_masked():
    from src.reporting.email_html import render

    html = render(
        {"reco_bilan": [{"asset": "CKB", "action": "RENFORCER",
                         "entry": 0.00129, "current": 0.00131, "target": 0.0015,
                         "delta_pct": 1.6, "status": "on_track"}]},
        "evening",
    )
    i = html.find("CKB")
    row = html[i:i + 600]
    # v23.x : colonnes séparées Entrée / Cible / Actuel (plus de « entrée → actuel »).
    # Les micro-prix (<0.01) ne sont PAS masqués en « — ».
    assert "$0.00129" in row   # prix d'entrée micro non masqué
    assert "$0.00131" in row   # prix actuel micro non masqué


def test_render_degraded_shows_reason():
    from src.ai_brain.decision_engine import DecisionEngine
    from src.reporting.email_html import render

    for kind in ("morning", "evening", "weekly"):
        payload = DecisionEngine._degraded(kind, {}, "Quota IA épuisé.")
        html = render(payload, kind)
        assert "Quota IA épuisé." in html, kind
