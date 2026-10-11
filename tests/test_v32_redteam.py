"""RED TEAM INDÉPENDANT — les défauts trouvés en auditant la V32 elle-même.

Ces tests ne rejouent pas un mail publié : ils verrouillent des défauts trouvés
en *cherchant à casser* la V32, par fuzzing d'invariants économiques, par
scénarios adversariaux (état corrompu, portefeuille vide, réseau coupé) et par
recherche de clés lues mais jamais produites.

Deux d'entre eux invalidaient des correctifs V32 « vérifiés » par des tests
verts : les tests FABRIQUAIENT le champ que la production n'écrit jamais. D'où
la règle appliquée ici — quand un correctif dépend d'une donnée persistée, le
test la fait produire par le VRAI chemin de persistance.
"""

from __future__ import annotations

import math
import random

import pytest


# --------------------------------------------------------------------------- #
# RT-1 — ATTEIGNABILITÉ : la cible publiée s'appelle ``ct_target``
# --------------------------------------------------------------------------- #
def _memoire_en_ram(monkeypatch):
    """Remplace l'I/O d'état par un dict, sans toucher au disque."""
    import src.state.report_memory as rm
    store: dict = {}
    monkeypatch.setattr(rm, "_read",
                        lambda f, default, elements=None: store.get(
                            f, default if default is not None else []))
    monkeypatch.setattr(rm, "_write", lambda f, data: store.__setitem__(f, data))
    return rm, store


def test_rt1_le_carnet_reel_porte_une_cible_que_le_moteur_sait_lire(monkeypatch):
    """La reco écrite par le VRAI chemin doit être clôturable sur SA cible.

    ``_target_hit_level`` lisait ``target_price``. Or ``_persist_firm_recos``
    n'écrit jamais ce champ : les recos portent ``ct_target``. Le correctif
    1.3/5.1 était donc INOPÉRANT en production — le moteur retombait toujours
    sur le multiplicateur fixe — pendant que ses tests passaient au vert parce
    qu'ils fabriquaient ``target_price`` à la main.

    Ce test ne fabrique rien : il fait persister la reco par ``main``, la relit
    par ``mem``, et vérifie que le moteur reconnaît la cible du carnet.
    """
    from src.main import _persist_firm_recos
    rm, _ = _memoire_en_ram(monkeypatch)

    # v33 — seule une décision du MOTEUR est persistée ; sa cible au carnet
    # est la barre de succès (entrée × (1 + requis)) : 240 × 1,05 = 252.
    payload = {"thesis_of_the_day": [{
        "asset": "TAO", "action": "RENFORCER",
        "engine_view": {"price": 240.0, "required_pct": 5.0, "horizon_days": 365},
    }]}
    data = {"all_positions_summary": [{"asset": "TAO", "price": 240.0}]}
    _persist_firm_recos(payload, data)

    recos = rm.load_active_recommendations()
    assert len(recos) == 1, "la reco n'a pas été persistée par le vrai chemin"
    reco = recos[0]
    # Le carnet réel : ``ct_target``, pas ``target_price``.
    assert reco.get("ct_target") == pytest.approx(252.0)
    assert reco.get("target_price") is None

    from src.tracking.prediction_scoring import _target_hit_level
    seuil = _target_hit_level(reco, reco["entry_price"], "RENFORCER")
    assert seuil == pytest.approx(252.0), (
        "le moteur ne lit pas la cible du carnet : le correctif est inopérant")


def test_rt1_lallegement_du_soir_lit_la_cible_du_carnet(monkeypatch):
    """L'arbitrage du 25/08 (2.3) exigeait la cible publiée dépassée.

    Il lisait lui aussi ``target_price`` : ``_cible`` valait toujours None sur
    les recos réelles, on retombait sur le comportement v30 et la décision
    d'Omar ne s'appliquait jamais.
    """
    from src.analytics.daily_guards import reconcile_evening_actions
    from src.main import _persist_firm_recos
    rm, _ = _memoire_en_ram(monkeypatch)

    _persist_firm_recos(
        {"thesis_of_the_day": [{
            "asset": "TAO", "action": "RENFORCER",
            "engine_view": {"price": 240.0, "required_pct": 5.0,
                            "horizon_days": 365}}]},
        {"all_positions_summary": [{"asset": "TAO", "price": 240.0}]})
    recos = rm.load_active_recommendations()
    for r in recos:                      # ce que fait ``refresh_active``
        r["current_price"] = 244.0       # sous la cible 252

    gardees, fixes = reconcile_evening_actions(
        [{"action": "Alléger 30% de TAO"}], recos)
    assert not gardees, "allègement conservé alors que la cible n'est pas atteinte"
    assert any("252" in f for f in fixes), fixes

    for r in recos:
        r["current_price"] = 259.0       # cible dépassée
    gardees2, _ = reconcile_evening_actions(
        [{"action": "Alléger 30% de TAO"}], recos)
    assert gardees2, "allègement fondé supprimé alors que la cible est dépassée"


# --------------------------------------------------------------------------- #
# RT-2 — le badge nomme le seuil qui a clôturé (arbitrage du 26/08)
# --------------------------------------------------------------------------- #
def test_rt2_le_badge_ne_dit_pas_cible_atteinte_sous_la_cible(monkeypatch):
    """Entrée 100 $, cible publiée 125 $, cours 112 $ : le plafond ×1,10
    clôture la reco. Le mail ne peut pas afficher « ✅ Cible atteinte » à côté
    de « Cible d'origine 125,00 $ · reste +11,6 % »."""
    import datetime as dt
    from src.tracking import prediction_scoring as ps

    now = dt.datetime.now(dt.timezone.utc)
    reco = {"asset": "TAO", "action": "RENFORCER", "entry_price": 100.0,
            "ct_target": 125.0, "stop_loss": 92.0, "status": "validated",
            "created_at": (now - dt.timedelta(days=5)).isoformat()}
    monkeypatch.setattr(ps.mem, "load_active_recommendations", lambda: [reco])

    ligne = ps.PredictionTracker().active_for_display({"TAO": 112.0})[0]
    assert ligne["health_status"] == "✅ Seuil de succès atteint"
    assert "n'est PAS atteinte" in ligne["comment"]
    assert "125" in ligne["comment"] and "110" in ligne["comment"]
    # La cible affichée reste la cible PUBLIÉE : on ne réécrit pas la promesse.
    assert ligne["ct_target"] == 125.0


def test_rt2_cible_reellement_atteinte_garde_son_badge(monkeypatch):
    """Non-régression : quand la cible publiée EST touchée, rien ne change."""
    import datetime as dt
    from src.tracking import prediction_scoring as ps

    now = dt.datetime.now(dt.timezone.utc)
    reco = {"asset": "TAO", "action": "RENFORCER", "entry_price": 100.0,
            "ct_target": 108.0, "stop_loss": 92.0, "status": "validated",
            "created_at": (now - dt.timedelta(days=5)).isoformat()}
    monkeypatch.setattr(ps.mem, "load_active_recommendations", lambda: [reco])
    ligne = ps.PredictionTracker().active_for_display({"TAO": 111.0})[0]
    assert ligne["health_status"] == "✅ Cible atteinte"


# --------------------------------------------------------------------------- #
# RT-3 — un montant légitime en M$ n'est jamais divisé par un million
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("texte", [
    "La capitalisation atteint 1 200 M$ soit 1,2 Md$.",
    "Les encours ETF cumulent 2 500 M$ depuis janvier.",
    "Le stablecoin pèse 1 500 Mds$.",
    "Réserve de 3 000 k$ disponible.",
    "Volume de 45 000 M sur 24 h.",
])
def test_rt3_les_montants_legitimes_sont_intacts(texte):
    """« 1 200 M$ » (1,2 milliard) devenait « 1 200$ » : une division
    silencieuse par un million, dans les trois mails."""
    from src.analytics.prose_guard import sanitize_llm_prose
    assert sanitize_llm_prose(texte)[0] == texte


def test_rt3_le_suffixe_colle_reste_corrige():
    """Le cas RÉELLEMENT observé (matin du 21/08) reste traité."""
    from src.analytics.prose_guard import sanitize_llm_prose
    out, fixes = sanitize_llm_prose(
        "L'emploi non-agricole recule de -23 000k sur le mois.")
    assert "23 000k" not in out and "-23 000 " in out
    assert fixes


# --------------------------------------------------------------------------- #
# RT-5 — un identifiant interne est TRADUIT, pas arraché de la phrase
# --------------------------------------------------------------------------- #
def test_rt5_la_phrase_reste_lisible():
    """« Selon et , le biais tient. » était publié tel quel."""
    from src.analytics.prose_guard import sanitize_llm_prose
    out, _ = sanitize_llm_prose(
        "Selon since_morning_facts et eligible_theses, le biais tient.")
    assert "since_morning_facts" not in out and "eligible_theses" not in out
    assert out == ("Selon les faits relevés depuis le matin et les thèses "
                   "éligibles, le biais tient.")
    assert " et ," not in out


# --------------------------------------------------------------------------- #
# RT-4 / RT-7 / RT-12 — invariants ÉCONOMIQUES du plan de trade
# --------------------------------------------------------------------------- #
def _serie(kind: str, px0: float, n: int = 180, graine: int = 7) -> list[float]:
    rng = random.Random(graine)
    d, v = {"krach": (-0.03, 0.06), "hausse": (0.03, 0.06),
            "plat": (0.0, 0.002), "volatil": (0.0, 0.15)}[kind]
    s = [px0]
    for _ in range(n - 1):
        s.append(max(1e-12, s[-1] * (1 + rng.gauss(d, v))))
    return s


def _controle_invariants(plan: dict) -> None:
    """Ce qu'un plan publiable ne peut JAMAIS violer."""
    if not plan.get("available"):
        return
    px = plan["price"]
    inv = plan["invalidation"]["level"]
    t30 = plan["target_30d"]
    zone = plan["accumulation_zone"]
    sc = plan["scenarios"]
    assert 0 < inv < px, f"invalidation {inv} vs prix {px}"
    assert t30["level"] > px, "cible 30 j sous le prix"
    assert zone["low"] <= zone["high"], "zone d'accumulation inversée"
    # Le cœur de RT-4 : on n'accumule jamais sous son propre stop.
    assert zone["low"] >= inv, f"zone d'accu {zone['low']} sous l'invalidation {inv}"
    for palier in plan["dca"]:
        assert palier["price"] > inv, (
            f"palier DCA {palier['price']} sous l'invalidation {inv}")
    assert sum(p["weight_pct"] for p in plan["dca"]) == 100
    assert sum(sc[k]["probability_pct"] for k in ("bull", "base", "bear")) == 100
    # RT-7 : un prix négatif n'existe pas.
    for k in ("bull", "bear"):
        assert sc[k]["level"] > 0, f"niveau {k} = {sc[k]['level']}"
        assert not sc[k]["level_label"].strip().startswith(("-", "−"))
    assert sc["base"]["low"] < sc["base"]["high"]
    if plan.get("rr_30d") is not None:
        assert px - inv > 0, "R:R publié sur un risque nul"


@pytest.mark.parametrize("kind", ["krach", "hausse", "plat", "volatil"])
@pytest.mark.parametrize("px0", [0.000012, 0.000166, 0.0052, 5.3, 3200.0])
def test_rt4_le_plan_ne_publie_jamais_un_niveau_incoherent(kind, px0):
    """RT-4 — reproduit par fuzzing sur des prix ORDINAIRES : « Invalidation
    0,1843 $ · Zone d'accu 0,1803–0,1903 $ · DCA 50 % à 0,1803 $ » — le plan
    faisait acheter la moitié de la position 2 % SOUS son propre stop.

    Cause : quand aucun support n'est détecté, l'invalidation était ancrée sur
    un −8 % fixe et le repère d'accumulation sur l'ATR. Deux ancrages
    indépendants, donc un ordre non garanti dès que l'ATR dépasse 8 % du prix.
    """
    from src.analytics.asset_plan import compute_asset_plan
    for graine in range(12):
        s = _serie(kind, px0, graine=graine)
        for funding in (None, -80.0, 10.95, 200.0):
            _controle_invariants(compute_asset_plan(
                "FUZZ", s, price=s[-1], funding_annualized_pct=funding))


def test_rt4_aucun_support_detecte_et_atr_large():
    """Le cas exact du fuzzing, reconstruit à la main."""
    from src.analytics.asset_plan import compute_asset_plan
    # Chute continue : tous les niveaux candidats restent AU-DESSUS du prix.
    closes = [100.0 * (0.96 ** i) for i in range(120)]
    plan = compute_asset_plan("X", closes, price=closes[-1])
    assert plan["available"]
    _controle_invariants(plan)
    # Le plan reste publiable : invalidation sous le prix, paliers au-dessus.
    assert plan["invalidation"]["basis"]
    assert plan["dca"][-1]["price"] > plan["invalidation"]["level"]


def test_rt12_un_micro_prix_garde_ses_chiffres_significatifs():
    """``round(x, 6)`` sur un actif à 1,66e-4 $ (1000SATS, position réelle)
    quantifiait les niveaux par pas de 0,6 % du prix ; sous 1e-5 $ la cible
    devenait égale au prix et le R:R se calculait sur un risque nul."""
    from src.utils.numfmt import arrondi_niveau
    assert arrondi_niveau(0.0000123456789) == pytest.approx(0.0000123457, rel=1e-9)
    assert arrondi_niveau(1.2e-9) == pytest.approx(1.2e-9, rel=1e-9)
    # Aucune régression sur les prix ordinaires : 6 décimales, comme avant.
    assert arrondi_niveau(61949.371234567) == pytest.approx(61949.371235, rel=1e-12)
    assert arrondi_niveau(None) is None
    assert arrondi_niveau(float("nan")) is None
    assert arrondi_niveau(0) == 0.0


# --------------------------------------------------------------------------- #
# RT-6 — un état corrompu dégrade le rapport, il ne l'empêche pas
# --------------------------------------------------------------------------- #
def test_rt6_un_element_detat_du_mauvais_type_ne_tue_pas_lhebdo(tmp_path,
                                                                monkeypatch):
    """``weekly_snapshots.json`` contenant ``[1, 2, "trois"]`` est bien une
    liste : la validation OB23 la laissait passer, puis
    ``record_weekly_snapshot`` faisait ``.get()`` sur un entier. Mesuré :
    AttributeError, et AUCUN mail hebdo ne part."""
    import src.state.report_memory as rm
    monkeypatch.setattr(rm, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(rm, "_path", lambda nom: tmp_path / nom)
    (tmp_path / rm.WEEKLY_SNAPSHOTS_FILE).write_text(
        '[1, 2, "trois"]', encoding="utf-8")

    assert rm.load_weekly_snapshots() == []      # éléments écartés, pas de crash
    rm.record_weekly_snapshot(1234.5, 61000.0, drawdown_ath_pct=-12.0)
    snaps = rm.load_weekly_snapshots()
    assert snaps and isinstance(snaps[0], dict)


def test_rt6_les_recos_du_mauvais_type_sont_ecartees(tmp_path, monkeypatch):
    import src.state.report_memory as rm
    monkeypatch.setattr(rm, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(rm, "_path", lambda nom: tmp_path / nom)
    (tmp_path / rm.ACTIVE_RECOS_FILE).write_text(
        '[{"asset": "BTC", "action": "RENFORCER"}, 42, null, "x"]',
        encoding="utf-8")
    recos = rm.load_active_recommendations()
    assert recos == [{"asset": "BTC", "action": "RENFORCER"}]


# --------------------------------------------------------------------------- #
# RT-8 — pas de graphique sur une série qui ne montre rien
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("serie", [
    [0.0] * 60,
    [-1.0] * 60,
    [float("nan")] * 60,
    [float("inf")] * 60,
])
def test_rt8_aucun_graphique_sur_une_serie_non_exploitable(serie, monkeypatch):
    """Un PNG de 5,5 ko parfaitement vide était envoyé dans le mail comme s'il
    montrait quelque chose."""
    from src.data_sources import coingecko
    from src.reporting import charts
    monkeypatch.setattr(coingecko, "get_price_volume_series",
                        lambda sym, days=90: {"closes": serie})
    assert charts.price_bollinger_png("X") is None


def test_rt8_une_serie_normale_produit_toujours_un_graphique(monkeypatch):
    from src.data_sources import coingecko
    from src.reporting import charts
    monkeypatch.setattr(coingecko, "get_price_volume_series",
                        lambda sym, days=90: {"closes": [100 + (i % 7) for i in range(60)]})
    png = charts.price_bollinger_png("X")
    assert png and png[:4] == b"\x89PNG"


# --------------------------------------------------------------------------- #
# Garde-fou permanent : aucune garde ne réécrit un chiffre sans y être autorisée
# --------------------------------------------------------------------------- #
def test_aucune_garde_de_prose_ne_touche_a_un_nombre_sauf_les_deux_admises():
    """Principe posé par l'audit V32 pour « DXY 994 » : sans moyen déterministe
    de savoir ce que le modèle visait, on ne réécrit pas son chiffre. Deux
    exceptions ASSUMÉES : le suffixe d'échelle collé (« 23 000k ») et la
    décimale disloquée (« −37, 5% »). Ce test échoue si une troisième
    réécriture de nombre apparaît sans décision explicite.
    """
    from src.analytics.prose_guard import sanitize_llm_prose
    echantillon = [
        "Le DXY est à 994 selon le modèle.",
        "BTC vise 76 368 $ après +8,9 % sur 24 h.",
        "Le PCE core reste à +3,3 % et le Nikkei perd 200,43 points.",
        "Les entrées ETF atteignent +103,3 M$ le 20 août.",
        "Le ratio MVRV est de 2,15 pour un seuil de 3,7.",
    ]
    for texte in echantillon:
        chiffres_avant = set(re_nombres(texte))
        out, _ = sanitize_llm_prose(texte)
        assert set(re_nombres(out)) == chiffres_avant, (
            f"un nombre a été réécrit : {texte!r} -> {out!r}")


def re_nombres(texte: str) -> list[str]:
    import re
    return re.findall(r"\d[\d   ,.]*", texte)


def test_les_bornes_de_prob_up_restent_celles_documentees():
    """``_prob_up_30d`` promet [0,30 ; 0,70] : une pondération mal ajustée
    sortirait de la borne sans que rien ne le signale."""
    from src.analytics.asset_plan import _prob_up_30d
    extremes = []
    for rsi in (0, 30, 50, 70, 100):
        for trend in (-50, -5, 0, 5, 50):
            for ma in (-90, 0, 90):
                for fund in (None, -500, 0, 10.95, 500):
                    for tilt in (None, -1, 0, 1):
                        p = _prob_up_30d(
                            {"rsi": rsi, "trend_7d_pct": trend,
                             "ma200_rel_pct": ma},
                            funding_annualized_pct=fund, market_net_tilt=tilt)
                        extremes.append(p)
                        assert 0.30 <= p <= 0.70, p
                        assert not math.isnan(p)
    assert min(extremes) < 0.45 < max(extremes)   # le signal bouge vraiment


# --------------------------------------------------------------------------- #
# RT-13 / RT-14 — la note de santé du portefeuille
# --------------------------------------------------------------------------- #
def test_rt13_un_seul_axe_ne_se_penalise_pas_lui_meme():
    """« Portée par Solidité (vs ATH) (5,8/10), pénalisée par Solidité (vs ATH)
    (5,8/10). » — le même axe portait ET pénalisait, dès que deux des trois
    entrées manquaient."""
    from src.main import _compute_portfolio_health

    r = _compute_portfolio_health({"drawdown_ath_pct": -40.0}, {})
    assert len(r["axes"]) == 1
    d = r["driver"]
    assert "Portée par" not in d and "pénalisée par" not in d
    assert "un seul axe" in d
    # Trois axes : la phrase d'origine est intacte.
    r3 = _compute_portfolio_health(
        {"vs_btc_7d_pct": -2.0, "drawdown_ath_pct": -40.0},
        {"sectors": [{"ptf_pct": 47.0}]})
    assert "Portée par" in r3["driver"] and "pénalisée par" in r3["driver"]


def test_rt14_la_note_de_sante_est_en_decimale_francaise():
    """Le bilan hebdo RÉELLEMENT produit affichait « Santé du portefeuille
    4.9 /10 · Diversification 5.0/10 » : le float Python se rend avec un POINT.
    C'est le défaut que numfmt avait supprimé sur les prix, resté intact sur le
    chiffre le plus visible du bloc santé."""
    from src.main import _compute_portfolio_health
    from src.reporting.email_html import _env

    note = _env.filters["note"]
    assert note(4.9) == "4,9" and note(5.0) == "5,0" and note(0.3) == "0,3"
    assert note(None) == "—" and note("n/d") == "—"

    r = _compute_portfolio_health(
        {"vs_btc_7d_pct": -2.0, "drawdown_ath_pct": -40.0},
        {"sectors": [{"ptf_pct": 47.0}]})
    for champ in ("driver", "improve"):
        texte = r.get(champ) or ""
        import re
        assert not re.search(r"\d\.\d\s*/\s*10", texte), (champ, texte)


def test_rt14_les_gabarits_ne_rendent_plus_la_note_brute():
    """Le rendu HTML des trois mails ne contient aucune note « X.Y/10 »."""
    import re

    from src.reporting.email_html import render

    sante = {"score": 4.9, "level": "correct", "level_color": "#6B8E23",
             "axes": [{"label": "Diversification", "score": 5.0, "max": 10.0,
                       "detail": "top secteur 30% du PTF"},
                      {"label": "Momentum vs BTC", "score": 4.0, "max": 10.0,
                       "detail": "−2,0 pts vs BTC 7j"}],
             "driver": "Portée par Diversification (5,0/10), "
                       "pénalisée par Momentum vs BTC (4,0/10).",
             "improve": "Diversifier hors L1 (axe Momentum vs BTC à 4,0/10 ce jour)."}
    html = render({"header": {"date": "26/08"}, "health_score": sante}, "morning")
    assert not re.search(r"\d\.\d\s*/?\s*<?/?span?>?\s*/10", html)
    assert "4,9" in html and "5,0" in html
    assert not re.search(r">\s*4\.9\s*<", html)

    # Le gabarit hebdo lit ``ptf_quality_score`` : se tromper de clé rendrait
    # le bloc absent et l'assertion vide — le test ne prouverait rien.
    hebdo = render({"header": {"date": "26/08"},
                    "portfolio_snapshot": {"value_usd": 2861.0},
                    "ptf_quality_score": dict(sante, delta_wow=0.3)}, "weekly")
    assert "Santé du portefeuille" in hebdo, "le bloc santé n'a pas été rendu"
    assert not re.search(r">\s*4\.9\s*<", hebdo)
    assert not re.search(r"\d\.\d/10", hebdo)
    assert "4,9" in hebdo and "5,0/10" in hebdo


# --------------------------------------------------------------------------- #
# RT-14 (classe entière) — aucune décimale anglaise visible dans un mail
# --------------------------------------------------------------------------- #
def _texte_visible(html: str) -> str:
    import html as _h
    import re
    v = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    v = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", v, flags=re.S | re.I)
    v = re.sub(r"<[^>]+>", " ", v)
    return re.sub(r"\s+", " ", _h.unescape(v))


def test_rt14_les_gabarits_localisent_tous_leurs_formats_decimaux():
    """Contrôle STATIQUE : tout ``'%.Nf'|format(...)`` (N ≥ 1) doit être
    localisé. Un seul des trente l'était (``|replace('.', ',')`` posé à la
    main) — la règle existait, elle n'était pas appliquée."""
    import pathlib
    import re

    for p in sorted(pathlib.Path("src/reporting/templates").glob("*.j2")):
        txt = p.read_text(encoding="utf-8")
        for m in re.finditer(r"'%\.[1-9]f'\|format\(", txt):
            # on referme la parenthèse pour lire ce qui suit immédiatement
            i, prof = m.end() - 1, 0
            while i < len(txt):
                if txt[i] == "(":
                    prof += 1
                elif txt[i] == ")":
                    prof -= 1
                    if prof == 0:
                        break
                i += 1
            suite = txt[i + 1:i + 26]
            assert "|virgule" in suite or "replace('.'" in suite, (
                f"{p.name}, position {m.start()} : format décimal non localisé "
                f"→ {txt[m.start():i + 26]!r}")


def _these_moteur() -> dict:
    """Thèse ferme telle que la chaîne v33 la produit (décision réelle)."""
    from src.analytics import opportunity as O
    from src.analytics.forecast import engine_view
    from tests.test_v33_moteur import btc, univers
    c = btc(mvrv=0.9)
    d = O.decide(c, O.measure_universe(univers(c)), ptf_value_usd=2861.0)
    return {"asset": "BTC", "action": "RENFORCER", "action_type": "bullish",
            "thesis_type": "conviction", "engine_view": engine_view(d, c),
            "v33_decision": d, "_expand": True}


def test_rt14_aucune_decimale_anglaise_dans_les_trois_rendus():
    """Contrôle de SORTIE, sur les champs qui ont réellement fauté :
    « BTC — 36.6% PTF », « ~5.2 paris EFFECTIFs », « Santé du portefeuille
    4.9 /10 », « R:R 1.2:1 », « +0.04% depuis matin »."""
    import re

    from src.reporting.email_html import render

    sante = {"score": 4.9, "level": "correct", "level_color": "#6B8E23",
             "axes": [{"label": "Diversification", "score": 5.0, "max": 10.0,
                       "detail": "top secteur 36,6% du PTF"},
                      {"label": "Momentum vs BTC", "score": 4.2, "max": 10.0,
                       "detail": "−2,0 pts vs BTC 7j"}],
             "driver": "Portée par Diversification (5,0/10), "
                       "pénalisée par Momentum vs BTC (4,2/10).",
             "improve": "Diversifier hors L1 (axe Momentum vs BTC à 4,2/10)."}
    heatmap = {"cells": [{"symbol": "BTC", "change_24h": 1.3, "ptf_pct": 36.6},
                         {"symbol": "ETH", "change_24h": -0.7, "ptf_pct": 17.5}],
               "extra": {"count": 14, "avg_change_24h": 0.4, "ptf_pct": 6.1,
                         "value_usd": 174.0}}
    matin = {
        "header": {"date": "26/08"},
        "health_score": sante,
        "portfolio_heatmap": heatmap,
        "portfolio_snapshot": {"value_usd": 2861.0, "usdc_usd": 120.0,
                               "usdc_pct": 4.2},
        "portfolio_risk": {"available": True, "readings": [
            "Concentration : 29 positions mais seulement ~5,2 paris EFFECTIFs "
            "(HHI) — BTC pèse 36,6%."]},
        # v33 — la ligne de thèse porte les chiffres du MOTEUR (la colonne
        # R:R, ancien témoin de ce test, n'existe plus).
        "thesis_of_the_day": [_these_moteur()],
    }
    soir = {
        "header": {"date": "26/08"},
        "health_score": sante,
        "portfolio_snapshot": {"value_usd": 2861.0,
                               "change_since_morning_pct": 0.04},
        "daily_pnl": {"day_change_label": "neutre", "day_change_pct": 0.04,
                      "day_change_usd": 1.2},
        "macro_context": {"dxy": 98.73, "vix": 17.4, "usd_jpy": 147.25},
    }
    hebdo = {
        "header": {"date": "26/08"},
        "portfolio_snapshot": {"value_usd": 2861.0},
        "ptf_quality_score": dict(sante, delta_wow=0.3),
        "portfolio_heatmap_7d": heatmap,
        "sector_exposure_computed": {"available": True, "sectors": [
            {"sector": "L1", "ptf_pct": 46.6, "market_change_24h": -1.3,
             "holdings": ["BTC", "ETH"]}]},
    }
    rendus = {}
    for payload, genre in ((matin, "morning"), (soir, "evening"),
                           (hebdo, "weekly")):
        rendus[genre] = render(payload, genre)
        vis = _texte_visible(rendus[genre])
        fautes = [m.group(0) for m in
                  re.finditer(r"(?<![\w/.])\d+\.\d+(?![\w/.])", vis)
                  if not re.match(r"^\d+\.\d+\.\d+$", m.group(0))]
        assert not fautes, f"{genre} : décimale(s) anglaise(s) {fautes[:6]}"

    # Le test doit ÉCHOUER si les blocs fautifs cessent d'être rendus : sans
    # ces ancres, il passerait au vert sur trois mails vides.
    for attendu in ("36,6% PTF", "4,9", "4,2% du PTF", "6,1% du PTF",
                    matin["thesis_of_the_day"][0]["engine_view"]["display"]["pot_req"]):
        # _texte_visible normalise les espaces fines ( ) en espaces.
        assert attendu.replace(" ", " ") in _texte_visible(rendus["morning"]), attendu
    assert "4,9" in rendus["evening"]
    assert "36,6% PTF" in rendus["weekly"] and "46,6% PTF" in rendus["weekly"]
