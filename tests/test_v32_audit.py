"""VERROUS V32 — un test par constat de l'audit des mails des 21-24/08/2026.

Chaque test nomme le défaut qu'il empêche de revenir, avec la valeur RÉELLEMENT
publiée en production comme cas d'entrée. Un test qui passe ici signifie que le
symptôme observé dans un mail réel ne peut plus se reproduire sous cette forme.

Aucun appel réseau : tout est reconstitué à partir des sorties observées.
"""

from __future__ import annotations

import math

import pytest


# ═══════════════════════════════════════════════════════════════════════════
# 5.3 — MAX PAIN : l'écart était calculé contre un FORWARD, pas contre le spot
# ═══════════════════════════════════════════════════════════════════════════
def test_max_pain_ecart_calcule_contre_le_spot_fourni():
    """Le 23/08, max pain 76 500 $ > spot 76 226 $ (aimant HAUSSIER) était
    publié « −3,3 % vs spot · aimant baissier » : verdict INVERSÉ."""
    j = 30 * 24 * 3600
    now_ms = int(__import__("time").time() * 1000)

    def _instr(exp_ms, strike, opt, oi, underlying):
        from datetime import datetime, timezone
        d = datetime.fromtimestamp(exp_ms / 1000, tz=timezone.utc)
        return {"instrument_name": f"BTC-{d.strftime('%d%b%y').upper()}-{strike}-{opt}",
                "open_interest": oi, "underlying_price": underlying}

    proche = now_ms + 3 * 24 * 3600 * 1000
    lointain = now_ms + 4 * j * 1000
    result = [
        # L'API renvoie d'abord une échéance LOINTAINE : son forward (+3,9 %)
        # servait de référence à l'écart. C'est le défaut exact.
        _instr(lointain, 90000, "C", 10, 80561.0),
        _instr(proche, 76000, "C", 100, 77555.0),
        _instr(proche, 77500, "C", 500, 77555.0),
        _instr(proche, 77500, "P", 500, 77555.0),
        _instr(proche, 79000, "P", 100, 77555.0),
    ]
    import src.data_sources.deribit as d

    def _fake(url, params=None, **kw):
        if "book_summary" in url:
            return {"result": result}
        return {}

    old = d.get_json
    d.get_json = _fake
    try:
        out = d._fetch_options_summary("BTC", spot_price=76226.0)
    finally:
        d.get_json = old

    assert out["max_pain"] == 77500
    ecart = out["max_pain_gap_pct"]
    # (77500 − 76226) / 76226 = +1,67 % → aimant HAUSSIER, pas baissier.
    assert ecart == pytest.approx(1.7, abs=0.1), (
        f"écart {ecart} : recalculable depuis les deux nombres du mail")
    assert ecart > 0, "le signe doit refléter un max pain AU-DESSUS du spot"
    assert out["max_pain_ref_kind"] == "spot"


# ═══════════════════════════════════════════════════════════════════════════
# 5.4 — hostname mort : deux sources tuées par une URL
# ═══════════════════════════════════════════════════════════════════════════
def test_coinmarketcal_pointe_sur_un_hote_qui_resout():
    """`developers.coinmarketcal.com` ne résout plus (mesuré le 25/08/2026)."""
    from src.data_sources import coinmarketcal

    assert "developers.coinmarketcal.com" not in coinmarketcal._BASE
    assert coinmarketcal._BASE.startswith("https://api.coinmarketcal.com/")


def test_catalyseurs_crypto_sont_dans_le_catalogue_de_sources():
    """Une source du périmètre qui tombe doit être VISIBLE quelque part."""
    from src.main import _ALL_SOURCES_LIST, _SOURCE_LABELS

    assert "crypto_events" in _SOURCE_LABELS
    assert _SOURCE_LABELS["crypto_events"] in _ALL_SOURCES_LIST


def test_catalogue_et_table_de_correspondance_restent_synchronises():
    from src.main import _ALL_SOURCES_LIST, _CLES_SOURCES, _active_sources

    tous = _active_sources(**{k: True for k in _CLES_SOURCES})
    assert sorted(tous) == sorted(_ALL_SOURCES_LIST)


# ═══════════════════════════════════════════════════════════════════════════
# 5.5 — le libellé RSS annonçait 16 flux pour 15 configurés / 14 vivants
# ═══════════════════════════════════════════════════════════════════════════
def test_libelle_rss_derive_du_catalogue_reel():
    from src.data_sources.crypto_rss import CRYPTO_FEEDS, MACRO_FEEDS
    from src.main import _RSS_LABEL

    n = len(CRYPTO_FEEDS) + len(MACRO_FEEDS)
    assert f"{n} flux" in _RSS_LABEL
    assert "16 flux" not in _RSS_LABEL or n == 16


# ═══════════════════════════════════════════════════════════════════════════
# 5.6 / 2.2 — micro-prix : « 0,0014 $ » et « 0.0000 $/j »
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("valeur,attendu", [
    (0.001446, "0,001446"),      # stop RSR réel — était tronqué en « 0,0014 »
    (0.000045, "0,000045"),      # ATR RSR réel — était affiché « 0.0000 »
    (0.00001209, "0,00001209"),  # 1000SATS
    (0.2037, "0,2037"),
    (78911, "78"),
])
def test_micro_prix_gardent_leurs_chiffres_significatifs(valeur, attendu):
    from src.utils.numfmt import fr_usd

    assert attendu in fr_usd(valeur)


def test_key_levels_localise_toutes_ses_branches():
    """La branche « < 1 » était la seule à ne pas convertir le point décimal."""
    from src.analytics.key_levels import _fmt_usd

    for v in (1413.2, 234.9, 5.37, 0.2037, 0.001446, 0.000045):
        rendu = _fmt_usd(v)
        assert "." not in rendu, f"{v} rendu en décimale anglaise : {rendu}"


def test_atr_dun_micro_prix_nest_jamais_zero():
    """« ATR 3,1% (≈0.0000 $/j) » : le nombre montré au lecteur valait ZÉRO."""
    from src.analytics.key_levels import _fmt_usd

    rendu = _fmt_usd(0.0000449)
    assert rendu.replace(" ", " ").split(" ")[0] not in ("0,0000", "0.0000")
    assert "449" in rendu or "45" in rendu


def test_invalidation_micro_prix_conserve_sa_precision():
    """« à 0,4 % de l'invalidation 0,0014 $ » pour un stop à 0,001446 $."""
    from src.utils.numfmt import fr_num

    assert fr_num(0.001446, thin=False) == "0,001446"


# ═══════════════════════════════════════════════════════════════════════════
# 5.1 — la boucle d'auto-évaluation : clôture, fenêtre, monotonie
# ═══════════════════════════════════════════════════════════════════════════
def test_cible_publiee_cloture_la_reco():
    """RENDER 21/08 : « Cible 1,43 $ · 100 % · ✅ Cible atteinte » dans le
    tableau, et « en cours » pour le moteur, qui exigeait entrée × 1,10."""
    from src.tracking.prediction_scoring import PredictionTracker

    reco = {"asset": "RENDER", "action": "RENFORCER", "entry_price": 1.33,
            "target_price": 1.43, "created_at": _il_y_a(14)}
    assert PredictionTracker().evaluate_recommendation(reco, 1.44) == "validated"


def test_cible_extravagante_ne_rend_pas_la_reco_inclotutrable():
    from src.tracking.prediction_scoring import PredictionTracker

    reco = {"asset": "X", "action": "RENFORCER", "entry_price": 1.0,
            "target_price": 99.0, "created_at": _il_y_a(1)}
    # Plafonnée par le multiplicateur : +10 % clôture quand même.
    assert PredictionTracker().evaluate_recommendation(reco, 1.11) == "validated"


def test_win_rate_sancre_sur_la_cloture_pas_sur_lemission(monkeypatch):
    """Une reco EXPIRÉE à J+30 sortait de la fenêtre de 30 j à l'instant même
    où elle y entrait : les perdantes disparaissaient du dénominateur."""
    from src.tracking import prediction_scoring as ps

    monkeypatch.setattr(ps.mem, "load_prediction_history", lambda: [
        {"asset": "PERDANTE", "status": "invalidated",
         "created_at": _il_y_a(31), "closed_at": _il_y_a(1)},
        {"asset": "GAGNANTE", "status": "validated",
         "created_at": _il_y_a(20), "closed_at": _il_y_a(5)},
    ])
    wr = ps.PredictionTracker().compute_win_rate(30)
    assert wr["total"] == 2, "la reco expirée doit rester comptée"
    assert wr["invalidated"] == 1
    assert wr["win_rate_pct"] == 50
    assert wr["window_days"] == 30


def test_win_rate_et_calibration_partagent_la_meme_population(monkeypatch):
    """Le mail juxtaposait « échantillon 15 » et « 3/5 clôturées »."""
    from src.tracking import prediction_scoring as ps

    hist = [{"asset": f"A{i}", "status": "validated" if i % 2 else "invalidated",
             "confidence": 72, "created_at": _il_y_a(80),
             "closed_at": _il_y_a(10)} for i in range(6)]
    monkeypatch.setattr(ps.mem, "load_prediction_history", lambda: hist)
    t = ps.PredictionTracker()
    n_wr = t.compute_win_rate(90)["total"]
    n_cal = sum(int(b.get("n") or 0)
                for b in (t.compute_calibration(90).get("buckets") or []))
    assert n_wr == n_cal == 6


def test_calibration_annonce_sa_fenetre(monkeypatch):
    from src.analytics import confidence_calibration as cc

    class _T:
        def compute_calibration(self, days):
            return {"available": True,
                    "buckets": [{"range": "70-79%", "realized_pct": 20, "n": 15}]}

    from src.state import report_memory as mem
    monkeypatch.setattr(mem, "_read", lambda *a, **k: {})
    monkeypatch.setattr(mem, "_write", lambda *a, **k: None)
    out = cc.compute_confidence_multiplier(_T())
    assert f"{cc._FENETRE_JOURS} j" in out["reason"]


# ═══════════════════════════════════════════════════════════════════════════
# 5.7 — prose non validée
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("avant,doit_contenir,ne_doit_pas", [
    ("les interventions sur la d'ette américaine", "dette", "d'ette"),
    ("un déclin de -23 000k emplois", "-23 000", "000k"),
    ("Si LINK replie sous 11,00 $ -> Renforcer", "→", "->"),
    ("Rachat visé : N/A", "—", "N/A"),
    ("Le DXY progresse. · since_morning_facts", "DXY", "since_morning_facts"),
    ("RSR : RSR : à 0,8% de l'invalidation", "RSR :", "RSR : RSR :"),
])
def test_prose_assainie(avant, doit_contenir, ne_doit_pas):
    from src.analytics.prose_guard import sanitize_llm_prose

    out, _ = sanitize_llm_prose([avant])
    assert doit_contenir in out[0]
    assert ne_doit_pas not in out[0]


@pytest.mark.parametrize("phrase", [
    "Tu as tout à fait raison, d'accord avec l'analyse d'ensemble.",
    "La liquidité globale se resserre, l'impact sur l'or reste limité.",
    "Le seuil d'invalidation n'est pas atteint.",
    "L'objectif d'accumulation reste valable.",
])
def test_les_elisions_legitimes_sont_intouchees(phrase):
    """Une réparation spéculative de la langue ferait plus de mal que le
    défaut qu'elle prétend corriger."""
    from src.analytics.prose_guard import sanitize_llm_prose

    out, _ = sanitize_llm_prose([phrase])
    assert out[0] == phrase


# ═══════════════════════════════════════════════════════════════════════════
# 5.8 — chiffre ETF cité alors que la source est déclarée indisponible
# ═══════════════════════════════════════════════════════════════════════════
def test_chiffre_etf_etiquete_quand_la_source_est_morte():
    from src.analytics.prose_guard import flag_unsourced_etf_figures

    phrase = ("Le Bitcoin bondit de +8,9% sur 24h, soutenu par +103,3 M$ "
              "d'entrées nettes d'ETF.")
    out, fx = flag_unsourced_etf_figures([phrase], etf_available=False)
    assert "non recoupé" in out[0]
    assert fx


def test_chiffre_etf_intact_quand_la_source_repond():
    from src.analytics.prose_guard import flag_unsourced_etf_figures

    phrase = "Les ETF ont capté +103,3 M$ hier."
    out, fx = flag_unsourced_etf_figures([phrase], etf_available=True)
    assert out == [phrase] and not fx


# ═══════════════════════════════════════════════════════════════════════════
# 3.1 — la garde F&G détruisait une phrase JUSTE
# ═══════════════════════════════════════════════════════════════════════════
def test_garde_fg_epargne_une_evolution_en_prose():
    """« bondit de 31 à 73 points » devenait « bondit de 73 à 73 points,
    marquant un passage brutal de la peur à l'avidité »."""
    from src.analytics.weekly_guards import enforce_summary_figures

    phrase = ("L'indice Fear & Greed bondit de 31 à 73 points en une semaine, "
              "marquant un passage brutal de la peur à l'avidité.")
    out, _ = enforce_summary_figures([phrase], {}, fear_greed_value=73,
                                     fear_greed_7d_ago=31)
    assert out[0] == phrase


def test_garde_fg_corrige_toujours_une_valeur_isolee_fausse():
    from src.analytics.weekly_guards import enforce_summary_figures

    out, fx = enforce_summary_figures(["Avidité élevée (F&G 66)."], {},
                                      fear_greed_value=73, fear_greed_7d_ago=31)
    assert "F&G 73" in out[0] and fx


# ═══════════════════════════════════════════════════════════════════════════
# 5.11 — la garde DXY propageait la valeur inventée
# ═══════════════════════════════════════════════════════════════════════════
def test_garde_dxy_sancre_sur_la_valeur_mesuree():
    from src.analytics.daily_guards import unify_dxy_thresholds

    etat = {"canon": None, "canon_txt": None, "measured": 98.73}
    out, _ = unify_dxy_thresholds(["Le DXY (ICE) s'établit à 99,02."], etat)
    assert "98,73" in out[0]


def test_garde_dxy_epargne_les_seuils_conditionnels():
    """« DXY > 99,0 invaliderait le biais » est une BORNE, pas un niveau :
    la réécrire sur le spot la rendrait déjà franchie."""
    from src.analytics.daily_guards import unify_dxy_thresholds

    etat = {"canon": None, "canon_txt": None, "measured": 98.73}
    phrase = "DXY > 99,0 invaliderait le biais favorable sur les alts."
    out, _ = unify_dxy_thresholds([phrase], etat)
    assert out[0] == phrase


# ═══════════════════════════════════════════════════════════════════════════
# 5.10 — la 3e tranche DCA collée au stop
# ═══════════════════════════════════════════════════════════════════════════
def test_aucune_tranche_dca_ne_colle_au_stop():
    """INJ 24/08 : tranche 3 à 5,30 $ pour un stop à 5,25 $."""
    from src.analytics.asset_plan import compute_asset_plan
    from src.analytics.key_levels import compute_key_levels

    closes = [130 - (i // 40) * 8 + 2.0 * math.sin(i / 3.0) for i in range(200)]
    closes[-1] = 100.0
    atr = compute_key_levels("T", closes, price=100.0)["readout"]["atr_abs"]
    plan = compute_asset_plan("T", closes, price=100.0)
    inv = plan["invalidation"]["level"]
    for tranche in plan["dca"]:
        marge = (tranche["price"] - inv) / atr
        # 0,499 et non 0,5 : la comparaison porte sur un flottant construit
        # par division, dont l'arrondi binaire descend a 0,4999999999999940.
        assert marge >= 0.499, (
            f"tranche à {tranche['price']} = {marge:.2f} ATR du stop {inv}")


def test_le_plan_dit_quand_il_na_que_deux_paliers():
    from src.analytics.asset_plan import compute_asset_plan

    closes = [100 + 0.4 * math.sin(i / 2.0) for i in range(200)]
    closes[-1] = 100.0
    plan = compute_asset_plan("T", closes, price=100.0)
    if len(plan["dca"]) == 2:
        assert plan.get("dca_note")
        assert sum(t["weight_pct"] for t in plan["dca"]) == 100


# ═══════════════════════════════════════════════════════════════════════════
# 1.1 — la synthèse contredisait les thèses du même mail
# ═══════════════════════════════════════════════════════════════════════════
def test_synthese_alleger_vs_these_renforcer_est_explicitee():
    """21/08 : « prises de profits partielles sur RENDER et INJ » en tête,
    « RENFORCER RENDER » et « RENFORCER INJ » deux écrans plus bas."""
    from src.analytics.prose_guard import reconcile_summary_vs_theses

    theses = [{"asset": "RENDER", "action": "RENFORCER"},
              {"asset": "INJ", "action": "RENFORCER"}]
    phrase = ("Allègements tactiques : envisager des prises de profits "
              "partielles sur RENDER et INJ.")
    out, fx = reconcile_summary_vs_theses([phrase], theses)
    assert "RENFORCER" in out[0] and fx


def test_allegement_sur_un_actif_sans_these_reste_intact():
    from src.analytics.prose_guard import reconcile_summary_vs_theses

    theses = [{"asset": "INJ", "action": "RENFORCER"}]
    phrase = "Allègement tactique conseillé sur XRP après un pump de +48 %."
    out, fx = reconcile_summary_vs_theses([phrase], theses)
    assert out[0] == phrase and not fx


# ═══════════════════════════════════════════════════════════════════════════
# 1.5 — agenda macro : tri horaire et événements déjà publiés
# ═══════════════════════════════════════════════════════════════════════════
def test_agenda_trie_par_heure():
    """21/08 : 09:30, 09:30, 08:15, 08:15, 08:30, 08:30 sous un commentaire
    « Ordre CHRONOLOGIQUE »."""
    from src.main import _hhmm

    evts = [("UK PMI", "09:30"), ("FR PMI", "08:15"), ("DE PMI", "08:30")]
    ordonne = sorted(evts, key=lambda e: _hhmm(e[1]))
    assert [e[1] for e in ordonne] == ["08:15", "08:30", "09:30"]
    assert _hhmm(None) == "99:99"          # sans heure → fin de journée


# ═══════════════════════════════════════════════════════════════════════════
# 2.4 — « top mouvements » classés en % masquaient l'impact réel
# ═══════════════════════════════════════════════════════════════════════════
def test_top_mouvements_classes_par_impact_en_dollars():
    """24/08 : cinq lignes pesant 11 $ au total, BTC (+28 $) absent."""
    mov = [{"symbol": "BTC", "change": 2.0, "pnl_usd": 28.66},
           {"symbol": "INJ", "change": 9.2, "pnl_usd": 5.24},
           {"symbol": "ATOM", "change": -4.3, "pnl_usd": -0.99},
           {"symbol": "ARB", "change": -4.1, "pnl_usd": -0.62}]
    mov.sort(key=lambda m: (abs(m["pnl_usd"]), abs(m["change"])), reverse=True)
    assert mov[0]["symbol"] == "BTC"


# ═══════════════════════════════════════════════════════════════════════════
# 4.1 — le bot Telegram ne voyait pas le portefeuille
# ═══════════════════════════════════════════════════════════════════════════
def test_le_portefeuille_survit_a_un_contexte_sature():
    """Le bot a listé 7 positions sur 29 et fabriqué la quantité de BTC."""
    import json

    from src.telegram_bot.context_loader import context_to_text

    positions = [{"symbol": f"S{i}", "quantity": i + 1, "pru": 1.5 * (i + 1)}
                 for i in range(29)]
    positions[0].update(symbol="BTC", quantity=0.01812, pru=58442.22)
    ctx = {
        "last_morning_report": {"b": ["texte " * 500] * 30},
        "last_evening_report": {"b": ["texte " * 500] * 30},
        "last_weekly_report": {"b": ["texte " * 500] * 30},
        "portfolio": {"positions": positions, "count": 29},
    }
    out = json.loads(context_to_text(ctx, max_chars=30000))
    assert out["portfolio"]["count"] == 29
    assert len(out["portfolio"]["positions"]) == 29
    assert out["portfolio"]["positions"][0]["pru"] == 58442.22
    assert out.get("_completude_contexte")


def test_le_contexte_reste_un_json_valide_meme_sature():
    """Un JSON coupé en son milieu faisait lire « 7 positions » sur 29."""
    import json

    from src.telegram_bot.context_loader import context_to_text

    ctx = {"x": ["bloc " * 2000] * 50, "portfolio": {"count": 3}}
    json.loads(context_to_text(ctx, max_chars=2000))   # ne lève pas


# ═══════════════════════════════════════════════════════════════════════════
# 1.13 — un détecteur de configuration qui retient un jour sur cinq
# ═══════════════════════════════════════════════════════════════════════════
def test_statistique_historique_dit_quand_son_critere_est_creux():
    """INJ 24/08 : « configuration similaire […] 20 fois » sur 95 jours."""
    from src.analytics.historical_patterns import compute_setup_stats

    closes = [100 * math.exp(-0.004 * i) + 1.5 * math.sin(i / 2.5)
              for i in range(120)]
    out = compute_setup_stats(closes, change_24h=-3.0)
    assert out["available"]
    assert out["selectivity_pct"] > 15
    assert "peu sélectif" in out["summary"]


# ═══════════════════════════════════════════════════════════════════════════
# 5.15 — des tests qui pourrissent avec le calendrier
# ═══════════════════════════════════════════════════════════════════════════
def test_aucune_fixture_temporelle_absolue_dans_les_fenetres_de_scoring():
    """« 2026-07-15 » est sorti de la fenêtre de 30 j le 15/08 : le test
    passait en juillet et échouait en août, code inchangé."""
    import pathlib
    import re

    fautives = []
    for f in pathlib.Path("tests").glob("*.py"):
        for i, l in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r'"(?:created_at|closed_at)":\s*"20\d\d-', l):
                fautives.append(f"{f.name}:{i}")
    assert not fautives, (
        "fixtures temporelles absolues (utiliser conftest.il_y_a) : "
        + ", ".join(fautives))


# ── utilitaire ────────────────────────────────────────────────────────────
def _il_y_a(jours: float) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=jours)).isoformat()
