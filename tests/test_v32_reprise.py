"""VERROUS de la REPRISE V32 — findings non corrigés du premier tour.

Même règle que ``test_v32_audit.py`` : chaque test nomme le défaut qu'il
empêche de revenir, avec la valeur RÉELLEMENT publiée en production comme cas
d'entrée.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

TZ = ZoneInfo("Africa/Casablanca")


# ═══════════════════════════════════════════════════════════════════════════
# 1.6 — le suivi mesure la cible D'ORIGINE, et le dit
# ═══════════════════════════════════════════════════════════════════════════
def test_le_suivi_nomme_la_cible_dorigine():
    """24/08 : suivi « Cible 240,00 $ » vs thèse « Cible 30j 243,47 $ »."""
    import pathlib

    gabarit = pathlib.Path(
        "src/reporting/templates/report_morning.html.j2").read_text(
        encoding="utf-8")
    assert "Cible d'origine (reste)" in gabarit


# ═══════════════════════════════════════════════════════════════════════════
# 1.8 — le fragment de score NOMME le niveau, et parle français
# ═══════════════════════════════════════════════════════════════════════════
def test_le_signal_de_support_porte_son_niveau():
    """21/08 : « à seulement 1,3 % de son support clé de 1,24 $ » pour RENDER
    coté 1,44 $ — le modèle avait nommé le STOP, pas le support du moteur."""
    from src.analytics.thesis_scoring import evaluate_thesis_eligibility

    out = evaluate_thesis_eligibility(
        {"tech_advanced": {"support_resistance": {
            "support": 1.4215, "dist_to_support_pct": 1.3}}}, tier=1)
    lbl = next(s["label"] for s in out["signals"] if "support clé" in s["label"])
    assert "1,42" in lbl, f"le niveau doit être nommé : {lbl}"
    assert "1.3%" not in lbl and "1,3%" in lbl, f"décimale anglaise : {lbl}"


@pytest.mark.parametrize("kwargs,attendu", [
    ({"mvrv": 0.87}, "MVRV 0,87"),
    ({"put_call_ratio": 0.45}, "(0,45)"),
])
def test_les_autres_fragments_de_score_sont_en_francais(kwargs, attendu):
    from src.analytics.thesis_scoring import evaluate_thesis_eligibility

    out = evaluate_thesis_eligibility({}, tier=1, **kwargs)
    assert any(attendu in s["label"] for s in out["signals"]), out["signals"]


# ═══════════════════════════════════════════════════════════════════════════
# 1.9 — plus d'espace orpheline après le « $ » retiré de la 2ᵉ borne
# ═══════════════════════════════════════════════════════════════════════════
def test_pas_d_espace_orpheline_sur_la_borne_haute():
    """« 34,37 $–52,62 » laissait une espace fine pendante après le nombre."""
    import jinja2

    from src.reporting.email_html import _fmt_price

    env = jinja2.Environment(autoescape=False)
    env.filters["fmt_price"] = _fmt_price
    rendu = env.from_string(
        "{{low|fmt_price}}–{{high|fmt_price|replace('$','')|trim}}"
    ).render(low=34.57, high=52.62)
    assert not rendu.endswith((" ", " ", " ")), repr(rendu)
    assert rendu.endswith("52,62"), repr(rendu)


# ═══════════════════════════════════════════════════════════════════════════
# 1.10 — l'icône du modèle ne s'ajoute plus à celle du gabarit
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("texte,debut", [
    ("✓ Régime macro en transition : le Bitcoin…", "Régime"),
    ("⚠ Risque principal : la hausse des taux…", "Risque"),
    ("• Rebond du portefeuille de +3,18 % sur 24 h.", "Rebond"),
    ("Rebond du portefeuille de +3,18 % sur 24 h.", "Rebond"),
])
def test_icone_en_double_retiree(texte, debut):
    """21/08 : « ⚠️✓ Régime macro » et « ⚠️⚠ Risque principal »."""
    from src.analytics.prose_guard import strip_leading_icons

    out, _ = strip_leading_icons([{"icon": "⚠", "text": texte}])
    assert out[0]["text"].startswith(debut), repr(out[0]["text"])


def test_une_puce_uniquement_faite_dicones_nest_pas_videe():
    from src.analytics.prose_guard import strip_leading_icons

    out, _ = strip_leading_icons([{"icon": "•", "text": "✓✓"}])
    assert out[0]["text"] == "✓✓"


# ═══════════════════════════════════════════════════════════════════════════
# 1.11 — l'intitulé décrit ce que le tableau contient
# ═══════════════════════════════════════════════════════════════════════════
def test_intitule_du_tableau_inclut_maintenir():
    import pathlib

    gabarit = pathlib.Path(
        "src/reporting/templates/report_morning.html.j2").read_text(
        encoding="utf-8")
    # v33 — le tableau ne contient plus que les décisions du moteur
    # (RENFORCER) et les règles de prise de profit (ALLÉGER) : l'intitulé et
    # le filtre doivent nommer EXACTEMENT ces deux postures.
    assert "RENFORCER décidé par le moteur d'allocation" in gabarit
    assert "ALLÉGER déclenché par tes règles de prise de profit" in gabarit
    assert "if t.action in ['RENFORCER','ALLÉGER','ALLEGER'] %}" in gabarit
    assert "'MAINTENIR'] %}{% set _ = _firm_theses" not in gabarit


# ═══════════════════════════════════════════════════════════════════════════
# 1.12 — un libellé de renfort ne survit pas à une action sans geste
# ═══════════════════════════════════════════════════════════════════════════
def test_libelle_de_taille_aligne_sur_laction():
    """21/08 : « Confiance · 68 % → Recharge stratégique » sur une fiche ETH
    dont le plan disait « Conserver — pas de renfort aujourd'hui »."""
    from src.analytics.prose_guard import align_size_note_with_action

    theses = [
        {"asset": "ETH", "action": "MAINTENIR", "size_note": "Recharge stratégique"},
        {"asset": "TAO", "action": "MAINTENIR",
         "size_note": "taille standard · confiance plafonnée"},
        {"asset": "INJ", "action": "RENFORCER", "size_note": "Recharge sur support"},
    ]
    out, fixes = align_size_note_with_action(theses)
    assert out[0].get("size_note") is None
    assert out[1]["size_note"] == "taille standard · confiance plafonnée"
    assert out[2]["size_note"] == "Recharge sur support"   # action qui achète
    assert fixes and "ETH" in fixes[0]


def test_le_plafonnement_de_confiance_survit_au_retrait():
    from src.analytics.prose_guard import align_size_note_with_action

    out, _ = align_size_note_with_action(
        [{"asset": "X", "action": "MAINTENIR",
          "size_note": "Recharge stratégique · confiance plafonnée"}])
    assert out[0]["size_note"] == "confiance plafonnée"


# ═══════════════════════════════════════════════════════════════════════════
# 2.6 — l'heure d'une news est dans le fuseau du mail
# ═══════════════════════════════════════════════════════════════════════════
def test_heure_de_news_ramenee_au_fuseau_du_mail():
    """24/08 : « Investing.com · 18h48 UTC » dans un mail « 20:06 Casablanca »
    — le lecteur croyait la news vieille d'1 h 18 pour 18 minutes réelles."""
    from src.analytics.prose_guard import normalize_news_times

    # v33 (audit 01/10) — le Maroc est à UTC+0 depuis le 20/09/2026 (IANA
    # tzdata 2026c) : un décalage « +1 » codé en dur faisait échouer ce test
    # selon la version de la base de fuseaux. On teste la CONVERSION sur un
    # fuseau UTC+1 fixe, puis la non-conversion sur UTC.
    news = [{"time": "18h48 UTC"}, {"time": "15h38 UTC"}, {"time": "19h48"}]
    out, fixes = normalize_news_times(news, ZoneInfo("Etc/GMT-1"))
    assert out[0]["time"] == "19h48"          # UTC+1
    assert out[1]["time"] == "16h38"
    assert out[2]["time"] == "19h48"          # déjà local : intouché
    assert "UTC" not in " ".join(n["time"] for n in out)
    assert fixes


def test_une_heure_absurde_nest_pas_convertie():
    from src.analytics.prose_guard import normalize_news_times

    out, _ = normalize_news_times([{"time": "48h99 UTC"}], TZ)
    assert out[0]["time"] == "48h99 UTC"      # refus plutôt qu'invention


# ═══════════════════════════════════════════════════════════════════════════
# 3.6 — la note du backtest ne contredit plus ses propres lignes
# ═══════════════════════════════════════════════════════════════════════════
def test_la_note_du_backtest_couvre_tous_les_horizons():
    """24/08 : « 7j (n=5) · 30j (n=3) » suivi de « échantillon de 5 »."""
    import math
    import random

    from src.analytics.strategy_backtest import compute_dip_buy_stats

    random.seed(3)
    serie = [100 + 12 * math.sin(i / 6.0) + random.uniform(-3, 3)
             for i in range(120)]
    out = compute_dip_buy_stats(serie)
    assert out["available"]
    ns = {s["n"] for s in out["horizons"].values()}
    if len(ns) > 1:
        assert f"de {min(ns)} à {max(ns)}" in out["note"], out["note"]
    else:
        assert f"de {ns.pop()} événement" in out["note"], out["note"]


# ═══════════════════════════════════════════════════════════════════════════
# 3.7 / 5.17 — décimales françaises partout dans les repères chiffrés
# ═══════════════════════════════════════════════════════════════════════════
def test_les_lectures_on_chain_sont_en_francais():
    """« SOPR 1.005 » et « NVT 33.1 » côtoyaient « NUPL 0,32 »."""
    import pathlib

    src = pathlib.Path("src/data_sources/bitcoin_data.py").read_text(
        encoding="utf-8")
    for motif in ('f"SOPR {s:.3f}"', "f\"NVT {out['nvt']:.1f}\""):
        i = src.index(motif)
        assert '.replace(".", ",")' in src[i:i + 120], motif


def test_les_reperes_hebdo_sont_en_francais():
    import pathlib

    src = pathlib.Path("src/main.py").read_text(encoding="utf-8")
    for motif in ("ETH/BTC {_ms['eth_btc_ratio']:.5f}",
                  "L/S {_dv_btc['long_short_ratio']}",
                  "put/call {_optb['put_call_ratio']}",
                  "DVOL {_optb['dvol']}%"):
        i = src.index(motif)
        assert '.replace(".", ",")' in src[i:i + 90], motif


def test_indice_dollar_elargi_en_francais_et_date_si_perime():
    from datetime import datetime, timedelta

    from src.main import _fraicheur_si_perimee

    assert _fraicheur_si_perimee(datetime.now().strftime("%Y-%m-%d")) is None
    vieux = (datetime.now() - timedelta(days=9)).strftime("%Y-%m-%d")
    assert _fraicheur_si_perimee(vieux, jours=4).startswith(" · au ")
    assert _fraicheur_si_perimee(None) is None
    assert _fraicheur_si_perimee("pas une date") is None


# ═══════════════════════════════════════════════════════════════════════════
# 4.6 — le relais avance l'offset de polling
# ═══════════════════════════════════════════════════════════════════════════
def test_le_relais_avance_loffset_de_polling(monkeypatch):
    """Sans cela, tout repli du relais vers le polling REJOUE l'arriéré déjà
    traité — et y répond une seconde fois."""
    from src.telegram_bot import bot as b

    vu: dict[str, int] = {}
    monkeypatch.setattr(b.telegram_api, "bot_configured", lambda: True)
    monkeypatch.setattr(b.telegram_api, "pull_relay_updates", lambda: [
        {"update_id": 4242, "message": {"chat": {"id": 1}, "text": "salut",
                                        "message_id": 7, "date": 0}}])
    monkeypatch.setattr(b.telegram_api, "extract_text_messages",
                        lambda u, c: ([], 4242))
    monkeypatch.setattr(b.mem, "save_telegram_offset",
                        lambda v: vu.__setitem__("offset", v))
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    assert b.run_from_relay() == 0
    assert vu.get("offset") == 4243


# ═══════════════════════════════════════════════════════════════════════════
# 4.3 — le bot a enfin une consigne de format
# ═══════════════════════════════════════════════════════════════════════════
def test_le_bot_a_une_consigne_de_format_francais():
    """« 1 341.38 $ », « 0.006487 $ », « 12:04 AM UTC » : aucune consigne de
    format n'existait dans le prompt avant la v32."""
    import pathlib

    src = pathlib.Path("src/telegram_bot/assistant.py").read_text(
        encoding="utf-8")
    assert "FORMAT DES NOMBRES" in src
    for regle in ("Décimale = VIRGULE", "Milliers = espace",
                  "chiffres SIGNIFICATIFS", "fuseau Casablanca"):
        assert regle in src, regle
