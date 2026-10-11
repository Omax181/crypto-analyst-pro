"""RÉ-AUDIT SUR LA CHAÎNE RÉELLE — les payloads défectueux des mails publiés.

Les tests de `test_v32_audit.py` verrouillent chaque garde ISOLÉMENT. Celui-ci
répond à une autre question, celle que l'audit exige : **les défauts observés
en production disparaissent-ils quand on rejoue les payloads fautifs dans la
vraie chaîne de gardes du mail ?**

La preuve d'exécutabilité hors ligne ne peut pas y répondre : sans clé Gemini,
il n'y a aucune prose de modèle à nettoyer, donc les gardes ne s'exercent pas.
On rejoue donc ici la SÉQUENCE RÉELLE de ``run_morning`` — ``_merge_python_facts``
puis ``_apply_morning_guards`` — sur les chaînes EXACTES des mails des 21 et
24/08/2026, et on vérifie la sortie.

Ce test a déjà payé : il a révélé que les gardes de prose renvoyaient une
NOUVELLE structure sans que ``_apply_morning_guards`` (qui ne retourne que la
liste des corrections) la propage. Le journal annonçait « 4 correction(s) » sur
un mail qui partait NON CORRIGÉ. Les tests unitaires, eux, passaient tous : ils
appelaient les gardes directement et lisaient leur valeur de retour.
"""

from __future__ import annotations


def _chaine_matin(payload: dict, data: dict) -> list[str]:
    """La séquence de run_morning, dans l'ordre : faits Python, puis gardes."""
    from src.main import _apply_morning_guards, _merge_python_facts, _now_str
    fusionne = _merge_python_facts(payload, data, _now_str())
    # ``_merge_python_facts`` renvoie l'objet REÇU : un clear() aveugle viderait
    # aussi ``fusionne``, qui est la même référence.
    if fusionne is not payload:
        payload.clear()
        payload.update(fusionne)
    return _apply_morning_guards(payload, data)



def _donnees_min(actifs: tuple[str, ...] = (),
                 fermes: tuple[str, ...] | None = None) -> dict:
    """Le strict nécessaire pour que les gardes trouvent leurs références.

    ``eligible_theses`` n'est pas décoratif : ``_merge_python_facts`` FILTRE
    ``thesis_of_the_day`` sur cette liste (une thèse portant sur un actif non
    éligible est écartée — comportement de production correct). Un fixture qui
    l'oublie fait disparaître les thèses avant les gardes, et le test mesure
    alors le vide.
    """
    # v33 — ``opportunity`` n'est pas décoratif non plus : depuis v33 le
    # moteur est SOUVERAIN sur l'action, et une thèse sans décision ferme de
    # sa part est ramenée à SURVEILLER avant même les gardes. Un fixture qui
    # l'oublie neutralise les thèses et le test mesure de nouveau le vide.
    # ``fermes`` est EXPLICITE : un test qui porte sur une thèse non ferme
    # doit pouvoir dire que le moteur n'a rien décidé pour cet actif. Sinon la
    # décision du moteur PROMEUT la thèse — comportement correct en v33, mais
    # qui vide le test de son objet.
    _fermes = actifs if fermes is None else fermes
    _dec = [{"asset": a, "action": "RENFORCER", "decided": True,
             "channel": "conviction",
             "reason": f"décision déterministe du moteur pour {a}",
             "size": {"pct": 5.0, "usd": 143.0, "band_label": "normale",
                      "kind": "core"}}
            for a in _fermes]
    return {
        "macro_context": {"dxy": 98.73, "fear_greed": 72},
        "etf_flows": {"available": False},
        "active_recommendations": [],
        "eligible_theses": [{"asset": a, "tier_label": "Tier 1 · large cap"}
                            for a in actifs],
        "opportunity": {"available": True, "count": len(_dec), "firm": _dec,
                        "decisions": list(_dec), "reduce_firm": []},
        "onchain_advanced": {"assets": {}},
        "upcoming_calendar": {"available": False, "events": []},
    }


def test_le_mail_du_21_08_ne_peut_plus_se_reproduire():
    """Cinq défauts du mail du 21/08, rejoués dans la vraie chaîne."""
    payload = {
        "header": {},
        "executive_summary": [
            {"icon": "⚠", "text":
                "Régime macro en transition : le Bitcoin s'affranchit de la "
                "correction des actions US et bondit de +8,9% sur 24h à "
                "76 368 $, soutenu par +103,3 M$ d'entrées nettes d'ETF."},
            {"icon": "⚠", "text":
                "Allègements tactiques : envisager des prises de profits "
                "partielles sur RENDER et INJ dont les cibles de court terme "
                "ont été atteintes."},
        ],
        "macro_context": {
            "narrative":
                "Le marché du travail américain montre des signes "
                "d'affaiblissement avec un emploi non-agricole en déclin de "
                "-23 000k, ce qui pourrait forcer un pivot dovish."},
        # ``confidence`` n'est pas décoratif non plus : _merge_python_facts
        # écarte toute thèse sous THESIS_CONFIDENCE_FLOOR (75 %) AVANT que la
        # calibration ne la rabaisse pour l'affichage — c'est ainsi qu'une reco
        # ferme peut s'afficher « C.62% » tout en ayant passé un plancher à 75.
        # Un plan SANS stop exploitable est retrogradé en SURVEILLER par
        # _merge_python_facts — comportement de production correct. Les plans
        # ci-dessous reprennent ceux du mail du 21/08 (RENDER : entrée 1,44 $,
        # stop 1,25 $, cible 1,50 $ ; INJ : 4,84 / 4,67 / 5,01).
        "thesis_of_the_day": [
            {"asset": "RENDER", "action": "RENFORCER", "confidence": 80,
             "sources_timestamps": "CoinGecko 08h12 · TradingView 08h15",
             "action_plan": {"entry": 1.44, "stop_loss": 1.25,
                             "take_profit": {"40pct": 1.50},
                             "position_size_pct": 1}},
            {"asset": "INJ", "action": "RENFORCER", "confidence": 80,
             "sources_timestamps": "CoinGecko 08h12 · TradingView 08h15",
             "action_plan": {"entry": 4.84, "stop_loss": 4.67,
                             "take_profit": {"40pct": 5.01},
                             "position_size_pct": 1}},
        ],
        "today_watch": "DXY > 99,0 invaliderait le biais favorable.",
    }
    _chaine_matin(payload, _donnees_min(("RENDER", "INJ")))
    rendu = repr(payload)

    # 5.8 — le chiffre ETF est cité alors que la source est déclarée morte.
    assert "non recoupé" in rendu, "chiffre ETF non étiqueté"

    # 5.7 — « -23 000k » = -23 millions d'emplois.
    assert "-23 000k" not in rendu
    assert "-23 000" in rendu

    # 1.1 — la synthèse conseillait d'alléger deux actifs sous RENFORCER.
    assert "RENFORCER" in payload["executive_summary"][1]["text"]

    # 5.2 — les horodatages de provenance étaient l'EXEMPLE du prompt.
    assert "08h12" not in rendu, "exemple du prompt encore présent"
    assert "TradingView 08h15" not in rendu

    # 5.11 — « DXY > 99,0 » est une BORNE : elle ne doit pas être réécrite
    # sur le spot mesuré (98,73), sinon elle devient déjà franchie.
    assert "99,0" in payload["today_watch"]


def test_le_mail_du_24_08_ne_peut_plus_se_reproduire():
    """« d'ette », flèche ASCII, identifiant interne, libellé dupliqué."""
    payload = {
        "header": {},
        "news_24h": [{
            "title": "Le rachat d'obligations de Bessent",
            "impact": "Preuve de la sensibilité du Bitcoin aux interventions "
                      "sur la d'ette américaine.",
        }],
        "today_watch":
            "RSR : RSR : à 0,4% de l'invalidation — surveiller la clôture. "
            "Si LINK replie sous 11,00 $ -> Renforcer. "
            "Le DXY a progressé depuis le matin. · since_morning_facts",
        "thesis_of_the_day": [],
    }
    _chaine_matin(payload, _donnees_min())
    rendu = repr(payload)

    assert "d'ette" not in rendu and "dette" in rendu
    assert "->" not in rendu and "→" in rendu
    assert "since_morning_facts" not in rendu
    assert "RSR : RSR :" not in rendu


def test_lhebdo_du_24_08_ne_peut_plus_se_reproduire():
    """« bondit de 73 à 73 points » et « −37, 5% sous ATH »."""
    from src.analytics.daily_guards import fix_broken_decimals
    from src.analytics.weekly_guards import enforce_summary_figures

    phrase_fg = ("L'indice Fear & Greed bondit de 31 à 73 points en une "
                 "semaine, marquant un passage brutal de la peur à l'avidité.")
    out, _ = enforce_summary_figures([phrase_fg], {}, fear_greed_value=73,
                                     fear_greed_7d_ago=31)
    assert out[0] == phrase_fg, "la garde F&G détruit encore une phrase juste"

    lignes = ["⚙ −37, 5% sous ATH, domination saine, phase de consolidation.",
              "⚙ −89, 1% sous ATH, puissant rebond de +41, 1% sur 7j."]
    out2, fx = fix_broken_decimals(lignes)
    assert all(", 5%" not in l and ", 1%" not in l for l in out2), out2
    assert "−37,5%" in out2[0] and fx


def test_le_soir_du_24_08_ne_peut_plus_se_reproduire():
    """ATR d'un micro-prix affiché « 0.0000 $/j » (zéro, et en anglais)."""
    from src.analytics.key_levels import compute_key_levels

    # RSR réel : ~0,001456 $, ATR ~3 %.
    closes = [0.001456 * (1 + 0.03 * ((i * 5 % 17) - 8) / 8)
              for i in range(160)]
    closes[-1] = 0.001456
    kl = compute_key_levels("RSR", closes, price=0.001456)
    ligne = kl["readout_line"]
    assert "0.0000" not in ligne and "0,0000 " not in ligne, ligne
    assert "$/j)" in ligne
    # Aucun point décimal dans la partie ATR de la ligne rendue.
    seg = ligne.split("ATR", 1)[1].split("·")[0]
    assert "." not in seg, f"décimale anglaise dans l'ATR : {seg!r}"


# ═══════════════════════════════════════════════════════════════════════════
# REPRISE V32 — les correctifs du second tour, sur la chaîne réelle
# ═══════════════════════════════════════════════════════════════════════════
def test_les_correctifs_du_second_tour_tiennent_dans_la_chaine():
    """Rejoue, en une seule passe de ``run_morning``, quatre défauts du 21/08 :
    l'icône en double, le libellé de taille contredisant l'action, l'heure de
    news étiquetée UTC, et le lien macro « DXY 994 »."""
    payload = {
        "header": {},
        "executive_summary": {"bullets": [
            {"icon": "⚠", "text": "✓ Régime macro en transition : le Bitcoin…"},
            {"icon": "⚠", "text": "⚠ Risque principal : la hausse des taux…"},
        ]},
        "macro_impact": {"exposed_positions": [
            {"asset": "XRP", "driver": "DXY 994",
             "effect": "Soutien technique modéré par affaiblissement du dollar."},
            {"asset": "STX", "driver": "Nasdaq -263,92 points",
             "effect": "Pression baissière par corrélation technologique."},
        ]},
        "news_24h": [{"title": "Trump menace…", "timestamp": "18h48 UTC",
                      "impact_on_ptf": "Incertitude géopolitique."}],
        "thesis_of_the_day": [
            {"asset": "ETH", "action": "MAINTENIR", "confidence": 80,
             "size_note": "Recharge stratégique",
             "action_plan": {"entry": 2380.0, "stop_loss": 2194.0,
                             "take_profit": {"40pct": 2400.0},
                             "position_size_pct": 1}},
        ],
    }
    # Le moteur n'a AUCUNE décision ferme sur ETH : la thèse reste
    # MAINTENIR, et c'est bien le libellé de renfort résiduel qui est
    # sous test, pas la souveraineté du moteur.
    donnees = _donnees_min(("ETH",), fermes=())
    donnees["macro_context"] = {"dxy": 98.73, "dxy_delta": -0.35,
                                "nasdaq": 26067.0, "nasdaq_delta": -263.92,
                                "fear_greed": 72}
    _chaine_matin(payload, donnees)

    # 1.10 — l'icône du modèle ne s'ajoute plus à celle du gabarit.
    textes = [b["text"] for b in payload["executive_summary"]["bullets"]]
    assert textes[0].startswith("Régime") and textes[1].startswith("Risque")

    # 5.7b — le lien macro aberrant est retiré, le lien vérifiable conservé.
    vus = [e["asset"] for e in payload["macro_impact"]["exposed_positions"]]
    assert "XRP" not in vus and "STX" in vus

    # 2.6 — l'heure de news est ramenée au fuseau du mail, sans étiquette.
    from datetime import datetime as _dt, timezone as _tz
    import src.main as _main
    _now = _dt.now(_tz.utc)
    _loc = _dt(_now.year, _now.month, _now.day, 18, 48, tzinfo=_tz.utc).astimezone(_main.TZ)
    assert payload["news_24h"][0]["timestamp"] == f"{_loc:%H}h{_loc:%M}"

    # 1.12 — le libellé de renfort ne survit pas à une action sans geste.
    _eth = next((t for t in payload["thesis_of_the_day"]
                 if t.get("asset") == "ETH"), None)
    if _eth is not None:              # la thèse peut être écartée en amont
        assert "Recharge" not in str(_eth.get("size_note") or "")
