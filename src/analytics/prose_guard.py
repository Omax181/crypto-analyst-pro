"""ASSAINISSEMENT DE LA PROSE GÉNÉRÉE — les trois rapports (v32, finding 5.7).

Constat des mails des 21-24/08/2026 : la prose libre du modèle atteint le
lecteur SANS aucun contrôle. Chaque symptôme ci-dessous a été relevé dans un
mail réellement envoyé :

  * « les interventions sur la d'ette américaine »      (matin 24/08)
  * « XRP   DXY 994 → »                                 (matin 21/08)
  * « un emploi non-agricole en déclin de -23 000k »    (matin 21/08 : -23
    MILLIONS d'emplois au lieu de -23 000, le suffixe d'échelle s'ajoutant à un
    nombre déjà développé)
  * « −37, 5% sous ATH », « rebond de +41, structure… » (hebdo 24/08 :
    décimales cassées ; le correctif ``fix_broken_decimals`` EXISTAIT mais
    n'était câblé qu'au matin)
  * « Si LINK replie sous 11,00 $ -> Renforcer »        (hebdo 24/08)
  * « Rachat visé : N/A »                               (soir 21 et 24/08)
  * « · since_morning_facts »                           (soir 24/08 : le NOM DE
    LA CLÉ interne du payload cité comme une source, trois fois)
  * « Leçon de la semaine · RSR : RSR : … »             (hebdo 24/08)

La cause est commune — aucune validation de la prose générée — donc la
réparation l'est aussi : UNE passe, appliquée aux trois rapports, plutôt qu'un
correctif par symptôme.

Principe directeur : on ne touche QU'À des motifs dont le caractère fautif est
démontré. Cette passe ne reformule jamais, ne résume jamais, et ne corrige
aucun chiffre — les valeurs relèvent des gardes chiffrées dédiées
(``daily_guards`` / ``weekly_guards``), qui savent, elles, à quelle mesure
comparer.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from src.analytics.daily_guards import walk_strings

# ── Identifiants internes : rien à faire sous les yeux du lecteur ─────────
#
# RED TEAM (RT-5) — ON REMPLACE, ON NE SUPPRIME PLUS.
# La règle effaçait l'identifiant, ce qui laissait des phrases cassées :
# « Selon since_morning_facts et eligible_theses, le biais tient. » devenait
# « Selon et , le biais tient. » — publié tel quel. Retirer un nom au milieu
# d'une phrase n'est pas une réparation. Chaque identifiant désigne une
# SECTION du payload, pas une source externe : on lui substitue son libellé
# français exact. La phrase reste lisible et rien n'est inventé.
_LIBELLES_INTERNES = {
    "since_morning_facts": "les faits relevés depuis le matin",
    "live_portfolio": "le portefeuille en direct",
    "live_market": "les cours en direct",
    "durable_memory": "la mémoire durable",
    "price_anchors": "les prix de référence",
    "header_meta": "l'en-tête du rapport",
    "eligible_theses": "les thèses éligibles",
    "portfolio_snapshot": "l'instantané du portefeuille",
    "weekly_summary": "la synthèse hebdomadaire",
    "positions_review": "la revue des positions",
    "thesis_of_the_day": "les thèses du jour",
    "active_recommendations_tracking": "le suivi des recommandations",
    "macro_impact": "l'impact macro",
    "sector_exposure_computed": "l'exposition sectorielle calculée",
}
_ID_INTERNE = re.compile(
    r"\b(" + "|".join(_LIBELLES_INTERNES) + r")\b")


def _libelle_interne(m: "re.Match[str]") -> str:
    return _LIBELLES_INTERNES[m.group(1)]

# ── Valeurs vides anglicisées livrées telles quelles ──────────────────────
_VIDE_ANGLAIS = re.compile(
    r"(?i)(?<![\w/])(?:N\s*/\s*A|None|null|undefined)(?![\w/])")

# ── Flèche ASCII dans de la prose française ───────────────────────────────
_FLECHE_ASCII = re.compile(r"\s*(?:-->|->|=>)\s*")

# ── Suffixe d'échelle COLLÉ à un nombre déjà développé ────────────────────
# « −23 000k » (matin du 21/08, emploi non-agricole) : le nombre porte déjà
# ses milliers, le « k » les compte une seconde fois.
#
# RED TEAM (RT-3) — arbitrage d'Omar (26/08/2026) : k UNIQUEMENT, et COLLÉ.
# La règle acceptait aussi « M », avec un séparateur optionnel — donc
# « 1 200 M$ » (1,2 milliard, écriture parfaitement légitime) devenait
# « 1 200$ ». Une division silencieuse par un million, sous les yeux du
# lecteur, dans les trois mails. Un nombre à milliers suivi de « M » n'a rien
# d'anormal : c'est la façon ordinaire d'écrire des millions. Seul le « k »
# COLLÉ aux chiffres reste traité — la forme exacte relevée en production —
# et « 1 200 k$ », « 2 500 Mds$ », « 45 000 M » sont désormais épargnés.
_ECHELLE_DOUBLE = re.compile(
    "(\\d{1,3}(?:[\u202f\u00a0 ]\\d{3})+)([kK])\\b")

# ── Libellé dupliqué en tête de phrase : « RSR : RSR : … » ────────────────
_LIBELLE_DOUBLE = re.compile(r"^(\s*[A-Z0-9]{2,10}\s*:\s*)\1")

# ── Apostrophe parasite au milieu d'un mot : « d'ette » ───────────────────
_APOSTROPHE_PARASITE = re.compile(
    "\\b([dlnmtsjcDLNMTSJC])['\u2019]"
    "([a-z\u00e0\u00e2\u00e4\u00e9\u00e8\u00ea\u00eb\u00ee\u00ef\u00f4\u00f6"
    "\u00f9\u00fb\u00fc\u00e7]{3,})\\b")

# Liste FERMÉE des recollages autorisés. On ne « répare » que ce dont on est
# certain : une réparation spéculative de la langue ferait plus de dégâts que
# le défaut qu'elle prétend corriger. « d'accord », « l'analyse », « d'ensemble »
# sont des élisions LÉGITIMES et ne figurent donc pas ici.
_MOTS_RECOLLES = frozenset((
    "dette", "dettes", "liquidite", "liquidité", "liquidites", "liquidités",
    "dollar", "dollars", "donnee", "donnée", "donnees", "données",
    "demande", "demandes", "detention", "détention", "declin", "déclin",
    "niveau", "niveaux", "marche", "marché", "marches", "marchés",
    "tendance", "tendances", "support", "supports", "signal", "signaux",
    "capital", "capitaux", "correction", "corrections", "cible", "cibles",
    "menace", "menaces", "montant", "montants", "mouvement", "mouvements",
    "solde", "soldes", "titre", "titres", "taux", "seuil", "seuils",
    "clotures", "clôtures", "cloture", "clôture", "cours", "creux",
    "sommet", "sommets", "sortie", "sorties", "distribution",
))


def _recolle(m: "re.Match[str]") -> str:
    """« d'ette » → « dette » ; « d'accord » reste « d'accord »."""
    tete, reste = m.group(1), m.group(2)
    mot = (tete + reste).lower()
    if mot not in _MOTS_RECOLLES:
        return m.group(0)
    recolle = tete + reste
    return recolle if tete.islower() else recolle.capitalize()


# ── v32 (5.8) — CHIFFRE ETF CITÉ ALORS QUE LA SOURCE EST DÉCLARÉE MORTE ──
#
# Le 21/08/2026, le mail du matin affirmait en tête (« EN BREF ») que le
# Bitcoin bondissait « soutenu par +103,3 M$ d'entrées nettes d'ETF », et le
# récit macro reprenait « des entrées nettes d'ETF de +103,3 M$ le 20 août ».
# Dans le MÊME mail : « ⚠ Indisponibles · ETF flows », et l'auto-critique
# reconnaissait « l'indisponibilité des données Farside et CoinGlass ». Un
# chiffre au dixième de million présenté comme un fait, sans source capable de
# le produire.
#
# La garde v30 existait déjà — mais uniquement sur ``news_24h``. Le chiffre
# vivait ailleurs : dans la synthèse et le récit macro. On étend donc le
# contrôle à TOUTE la prose, sans jamais effacer l'analyse : on lui accole sa
# provenance réelle, et le lecteur tranche.
# Le montant peut précéder OU suivre le mot « ETF » : le 21/08 il précédait
# (« soutenu par +103,3 M$ d'entrées nettes d'ETF »). Un motif
# unidirectionnel laissait passer exactement la formulation publiée.
_MONTANT = (r"[+\-−]?\s?\d[\d\s  ,.]*\s?"
            r"(?:M\$|Mds?\$|millions?\s+de\s+dollars|milliards?\s+de\s+dollars)")
_CTX = r"[^.\n]{0,90}?"
_ETF_CHIFFRE = re.compile(
    "(?i)(?:" + r"\bETF\b" + _CTX + _MONTANT
    + "|" + _MONTANT + _CTX + r"\bETF\b" + ")")
_DEJA_ETIQUETE = re.compile(r"(?i)non recoup|non vérifi|source structur")


def flag_unsourced_etf_figures(node: Any, etf_available: bool
                               ) -> tuple[Any, list[str]]:
    """Étiquette tout chiffre ETF quand la source structurée est indisponible.

    Args:
        node: fragment de payload.
        etf_available: état RÉEL de la collecte des flux ETF.

    Returns:
        ``(node, [corrections])``. Aucun effet si la source est disponible.
    """
    if etf_available:
        return node, []
    n_vus = 0

    def _fix(text: str) -> str:
        nonlocal n_vus
        if not _ETF_CHIFFRE.search(text) or _DEJA_ETIQUETE.search(text):
            return text
        n_vus += 1
        return text.rstrip().rstrip(".") + (
            ". [chiffre ETF non recoupé — la source structurée "
            "(Farside / CoinGlass) était indisponible sur ce run]")

    out = walk_strings(node, _fix)
    return out, ([f"{n_vus} chiffre(s) ETF étiqueté(s) « non recoupé »"]
                 if n_vus else [])


# ── v32 (1.10) — ICÔNE EN DOUBLE EN TÊTE DE PUCE ─────────────────────────
#
# Le gabarit rend lui-même l'icône de chaque puce d'« EN BREF » : « ⚠️ » pour
# les points urgents, « • » sinon. Le 21/08, le mail affichait « ⚠️✓ Régime
# macro en transition » et « ⚠️⚠ Risque principal » : le modèle avait REPRIS
# une icône EN TÊTE DU TEXTE, qui s'ajoutait à celle du gabarit. D'où des
# combinaisons dépourvues de sens — un avertissement suivi d'une validation.
_ICONE_EN_TETE = re.compile(
    "^[\\s​]*[✅✔✓❌✗✘⚠️•"
    "▸▶→–—\\-*]+[\\s ]*")


def strip_leading_icons(bullets: Any) -> tuple[Any, list[str]]:
    """Retire l'icône que le modèle place en tête du TEXTE d'une puce.

    Args:
        bullets: liste de puces ``{icon, text}`` (ou de chaînes).

    Returns:
        ``(bullets, [corrections])``.
    """
    if not isinstance(bullets, list):
        return bullets, []
    n = 0
    for b in bullets:
        cle = "text" if isinstance(b, dict) and isinstance(b.get("text"), str) else None
        if cle is None:
            continue
        neuf = _ICONE_EN_TETE.sub("", b[cle])
        # Une puce QUE d'icônes serait vidée : on ne touche pas.
        if neuf and neuf != b[cle]:
            b[cle] = neuf
            n += 1
    return bullets, ([f"{n} icône(s) en double retirée(s) en tête de puce"]
                     if n else [])


# ── v32 (1.12) — LE LIBELLÉ DE TAILLE CONTREDIT L'ACTION ─────────────────
#
# ``size_note`` est du texte libre du modèle, rendu juste après la confiance.
# Le 21/08, la fiche ETH portait « Confiance · 68 % → Recharge stratégique »
# alors que son plan disait, six lignes plus bas, « Conserver — PAS DE RENFORT
# aujourd'hui : déjà 22,4 % du PTF (plafond 20 % cœur) ». Le libellé annonçait
# le geste que le plan refusait.
_LIBELLE_RENFORT = re.compile(
    "(?i)(recharge|renfort|renforc|accumul|achat|acheter|ajout|moyenner|dca)")
_ACTIONS_SANS_GESTE = frozenset(("MAINTENIR", "SURVEILLER", "CONSERVER"))


def align_size_note_with_action(theses: Any) -> tuple[Any, list[str]]:
    """Neutralise un ``size_note`` d'achat sur une thèse qui n'achète pas.

    On ne réécrit pas la note en autre chose — on la retire, parce qu'aucune
    formulation de remplacement ne serait fondée sur une mesure.
    """
    if not isinstance(theses, list):
        return theses, []
    fixes: list[str] = []
    for t in theses:
        if not isinstance(t, dict):
            continue
        action = str(t.get("action") or "").upper().strip()
        note = t.get("size_note")
        if action not in _ACTIONS_SANS_GESTE or not isinstance(note, str):
            continue
        if not _LIBELLE_RENFORT.search(note):
            continue
        # On préserve le suffixe « · confiance plafonnée » posé en Python.
        suffixe = " · confiance plafonnée" if "confiance plafonnée" in note else ""
        fixes.append(f"{t.get('asset')} : libellé de taille « {note.strip()} » "
                     f"retiré (action {action}, aucun geste proposé)")
        if suffixe:
            t["size_note"] = suffixe.lstrip(" ·").strip()
        else:
            t.pop("size_note", None)
    return theses, fixes


# ── v32 (2.6) — L'HEURE D'UNE NEWS, DANS LE FUSEAU DU MAIL ───────────────
#
# Le repli Python localise correctement (``fmt_time_local`` → « 14h58 » heure
# de Casablanca). Le chemin LLM, lui, a publié le 24/08 « Investing.com ·
# Financial Times · 18h48 UTC » dans un mail dont l'en-tête dit « 20:06
# Casablanca » : le lecteur croit la news vieille d'1 h 18 alors qu'elle a
# 18 minutes. Aucun autre horaire du mail ne porte de fuseau.
_HEURE_UTC = re.compile(
    r"(?i)\b(\d{1,2})\s*[h:]\s*(\d{2})\s*(?:UTC|GMT|Z)\b")


def normalize_news_times(items: Any, tz: Any) -> tuple[Any, list[str]]:
    """Convertit « 18h48 UTC » en heure locale et retire l'étiquette.

    Args:
        items: liste de news portant ``time`` ou ``timestamp``.
        tz: fuseau d'affichage du mail (``TZ``).

    Returns:
        ``(items, [corrections])``.
    """
    if not isinstance(items, list) or tz is None:
        return items, []
    from datetime import datetime, timezone

    n = 0

    def _conv(m: "re.Match[str]") -> str:
        nonlocal n
        try:
            h, mn = int(m.group(1)), int(m.group(2))
            if not (0 <= h <= 23 and 0 <= mn <= 59):
                return m.group(0)
            base = datetime.now(timezone.utc).replace(
                hour=h, minute=mn, second=0, microsecond=0)
            n += 1
            return base.astimezone(tz).strftime("%Hh%M")
        except (TypeError, ValueError):
            return m.group(0)

    for it in items:
        if not isinstance(it, dict):
            continue
        for cle in ("time", "timestamp"):
            v = it.get(cle)
            if isinstance(v, str) and v:
                it[cle] = _HEURE_UTC.sub(_conv, v)
    return items, ([f"{n} horodatage(s) de news converti(s) en heure locale"]
                   if n else [])


# ── v32 (5.7b) — UN LIEN MACRO DONT LE CHIFFRE NE CORRESPOND À RIEN ──────
#
# Arbitrage d'Omar (25/08/2026) : on RETIRE la ligne plutôt que de publier un
# nombre faux, et sans tenter de deviner ce que le modèle visait.
#
# Le 21/08, le bloc « Liens chiffrés sur ton portefeuille » affichait
# « XRP   DXY 994 →   Soutien technique modéré par affaiblissement du dollar ».
# Le DXY valait 98,73. « 994 » ne correspond à rien de mesuré — ni au niveau,
# ni au delta, ni à un seuil plausible. Le champ ``driver`` est du texte libre
# du modèle, rendu tel quel par le gabarit.
#
# On ne corrige PAS le chiffre : « 994 » pouvait viser 99,4 (un seuil) comme
# 98,73 (le niveau). Réécrire reviendrait à faire parler le modèle — c'est
# exactement le piège dans lequel est tombée la garde F&G, qui a transformé
# « bondit de 31 à 73 » en « bondit de 73 à 73 ».
_INDICATEURS = {
    "dxy": ("dxy", "dxy_broad"),
    "nasdaq": ("nasdaq",),
    "s&p": ("sp500",), "sp500": ("sp500",), "s&p 500": ("sp500",),
    "vix": ("vix",),
    "or": ("gold_usd",), "gold": ("gold_usd",),
    "brent": ("brent_usd",), "wti": ("wti_usd",),
    "10y": ("us_10y",), "2y": ("us_2y",),
    "fear": ("fear_greed",), "f&g": ("fear_greed",),
}
_ECART_MAX = 0.20      # 20 % : large, pour ne retirer QUE l'aberrant


def _nombres(texte: str) -> list[float]:
    """Nombres lisibles dans un fragment (formats FR et US)."""
    out: list[float] = []
    for brut in re.findall(r"[+\-−]?\d[\d   ]*(?:[.,]\d+)?", texte):
        n = (brut.replace("−", "-").replace(" ", "").replace(" ", "")
                 .replace(" ", "").replace(",", "."))
        try:
            out.append(abs(float(n)))
        except ValueError:
            continue
    return out


def drop_unmatched_macro_drivers(
    exposed: Any, mesures: Any
) -> tuple[Any, list[str]]:
    """Retire un lien macro dont le chiffre ne colle à aucune mesure.

    Args:
        exposed: ``macro_impact.exposed_positions`` (liste de ``{asset, driver,
            effect}``).
        mesures: ``data["macro_context"]`` — les valeurs RÉELLEMENT mesurées.

    Returns:
        ``(liste filtrée, [corrections])``.
    """
    if not isinstance(exposed, list) or not isinstance(mesures, dict):
        return exposed, []
    fixes: list[str] = []
    gardes: list[Any] = []
    for ep in exposed:
        if not isinstance(ep, dict):
            gardes.append(ep)
            continue
        driver = str(ep.get("driver") or "")
        bas = driver.lower()
        cle = next((c for m, cles in _INDICATEURS.items() if m in bas
                    for c in cles if mesures.get(c) is not None), None)
        vus = _nombres(driver)
        if cle is None or not vus:
            gardes.append(ep)          # rien de vérifiable : on ne touche pas
            continue
        # Références acceptables : le NIVEAU mesuré et son DELTA. RIEN
        # d'autre — surtout pas le niveau ×10, qui « rattraperait » 994 pour
        # 99,4 et laisserait passer exactement le cas à retirer. Un driver
        # cite une valeur réelle ou il ne cite rien.
        refs: list[float] = []
        for c in _INDICATEURS[next(m for m in _INDICATEURS if m in bas)]:
            v = mesures.get(c)
            if isinstance(v, (int, float)):
                refs.append(abs(v))
            d = mesures.get(f"{c}_delta")
            if isinstance(d, (int, float)):
                refs.append(abs(d))
        if not refs:
            gardes.append(ep)
            continue
        colle = any(
            any(r > 0 and abs(n - r) / r <= _ECART_MAX for r in refs)
            for n in vus)
        if colle:
            gardes.append(ep)
            continue
        fixes.append(
            f"lien macro « {ep.get('asset')} · {driver.strip()} » RETIRÉ : "
            f"aucun chiffre mesuré ne correspond")
    return gardes, fixes


def sanitize_llm_prose(node: Any) -> tuple[Any, list[str]]:
    """Nettoie la prose générée avant qu'elle n'atteigne le lecteur.

    Args:
        node: n'importe quel fragment de payload (dict / list / str imbriqués).

    Returns:
        ``(node_nettoyé, [corrections lisibles])``. Liste vide si rien à faire.
    """
    compteurs: dict[str, int] = {}

    def _compte(quoi: str, n: int = 1) -> None:
        compteurs[quoi] = compteurs.get(quoi, 0) + n

    def _fix(text: str) -> str:
        neuf = text

        neuf, n = _ID_INTERNE.subn(_libelle_interne, neuf)
        if n:
            _compte("identifiant interne traduit", n)

        neuf, n = _ECHELLE_DOUBLE.subn(r"\1", neuf)
        if n:
            _compte("suffixe d'échelle en double retiré", n)

        neuf, n = _FLECHE_ASCII.subn(" → ", neuf)
        if n:
            _compte("flèche ASCII francisée", n)

        # NB : ``subn`` compte les CORRESPONDANCES, pas les modifications —
        # or _recolle laisse passer toutes les élisions légitimes. Compter n
        # annoncerait « 9 apostrophes recollées » pour une seule réparation.
        # Un rapport de correction qui exagère son propre travail est
        # précisément le genre de défaut que cet audit traque.
        _avant_apo = neuf
        neuf = _APOSTROPHE_PARASITE.sub(_recolle, neuf)
        if neuf != _avant_apo:
            _compte("apostrophe parasite recollée",
                    sum(1 for a, b in zip(_avant_apo.split(), neuf.split())
                        if a != b) or 1)

        avant = neuf
        neuf = _VIDE_ANGLAIS.sub("—", neuf)
        if neuf != avant:
            _compte("valeur vide anglicisée remplacée")

        avant = neuf
        neuf = _LIBELLE_DOUBLE.sub(r"\1", neuf)
        if neuf != avant:
            _compte("libellé dupliqué retiré")

        if neuf == text:
            return text
        # Espaces doubles laissés par les suppressions.
        return re.sub(r"[ \t]{2,}", " ", neuf).strip()

    out = walk_strings(node, _fix)
    fixes = [f"{n} {quoi}" for quoi, n in sorted(compteurs.items())]
    return out, fixes


# ── v32 (1.1) — LA SYNTHÈSE NE PEUT PAS CONTREDIRE LES THÈSES DU MÊME MAIL ──
#
# Le 21/08/2026, « EN BREF » recommandait en quatrième puce :
#     « Allègements tactiques : envisager des prises de profits partielles sur
#       RENDER et INJ dont les cibles de court terme ont été atteintes »
# pendant que le tableau « Thèses du jour », deux écrans plus bas, portait
#     « RENDER  RENFORCER (C.62%) »  et  « INJ  RENFORCER (C.62%) »,
# avec plan d'entrée, stop et DCA. Le même mail dit d'alléger et de renforcer
# les deux mêmes lignes, sans un mot pour réconcilier les deux.
#
# Le soir dispose depuis la v30 d'une garde exactement pour cela
# (``reconcile_evening_actions`` : une action CT contraire à une thèse LT
# active est requalifiée « couverture tactique »). Le matin n'en avait aucune,
# alors que c'est LE mail qui porte les deux affirmations côte à côte.
#
# On ne supprime pas la puce — l'arbitrage « prendre une partie du gain sur un
# actif qu'on accumule » est légitime. On l'ÉNONCE, pour que le lecteur ne
# reste pas devant deux ordres opposés sans clé de lecture.

_ALLEGEMENT = re.compile(
    r"(?i)(all[ée]g|prise?s?\s+de\s+(?:profit|b[ée]n[ée]fice)|"
    r"offload|sortir|vendre|r[ée]duire\s+(?:la\s+)?position|"
    r"s[ée]curiser\s+(?:des\s+|les\s+)?gains|take\s*profit)")


def reconcile_summary_vs_theses(
    node: Any, theses: Any
) -> tuple[Any, list[str]]:
    """Annote toute phrase qui conseille d'alléger un actif sous RENFORCER.

    Args:
        node: bloc de prose (``executive_summary``, ``synthesis``…).
        theses: ``payload["thesis_of_the_day"]``.

    Returns:
        ``(node, [corrections])``.
    """
    renforces: set[str] = set()
    for t in (theses or []):
        if not isinstance(t, dict):
            continue
        if "RENFORC" in str(t.get("action") or "").upper():
            sym = str(t.get("asset") or "").upper().strip()
            if sym:
                renforces.add(sym)
    if not renforces:
        return node, []

    fixes: list[str] = []

    def _fix(text: str) -> str:
        if not _ALLEGEMENT.search(text) or "thèse du jour" in text:
            return text
        vises = sorted(
            s for s in renforces
            if re.search(rf"\b{re.escape(s)}\b", text, re.IGNORECASE))
        if not vises:
            return text
        liste = " et ".join(vises)
        fixes.append(
            f"synthèse : allègement sur {liste} explicité comme geste "
            f"tactique (thèse du jour RENFORCER sur {liste})")
        return text.rstrip().rstrip(".") + (
            f". À lire comme un geste tactique sur la force : la thèse du "
            f"jour sur {liste} reste RENFORCER (accumulation), et le plan "
            f"détaillé plus bas la chiffre.")

    return walk_strings(node, _fix), fixes


# ── v33 — PROSE : aucun geste prescrit sans décision déterministe ─────────
#
# Audit zero-trust (01/10). Les gestes structurés (actions du soir, plan
# hebdo, watchlist) sont filtrés par ``daily_guards.restrict_llm_gestures``.
# Restait la prose : « Allège TAO sur ce rebond », « renforcer le cœur sous
# 1 500 $ » dans une synthèse ou un plan de sortie — une recommandation émise
# par le modèle de langage, hors de tout moteur. « Le LLM ne peut pas décider
# RENFORCER ni ALLÉGER » (Omar) : une phrase PRESCRIPTIVE (infinitif ou
# impératif) visant un actif sans décision déterministe du jour est retirée.
# Une phrase NÉGATIVE (« ne pas renforcer », « aucun allègement ») est
# conservée : s'abstenir n'est jamais interdit. Les constats au passé
# (« a été allégé ») ne sont pas des prescriptions et ne sont pas touchés.

_GESTE_ACHAT = re.compile(
    r"(?i)\b(renforcer|renforce|acheter|ach[èe]te|accumuler|accumule|"
    r"recharger|recharge|moyenner\s+(?:à|a)\s+la\s+baisse|"
    r"envisager\s+(?:un\s+|le\s+|des\s+)?(?:renforts?|achats?|accumulation)|"
    # nom de geste + modalité (hebdo réel rejoué le 02/10 : « structure
    # haussière daily : accumulation tactique du leader GPU ») ; « en
    # accumulation », « zone d'accumulation » restent des PHASES de cycle.
    r"(?:accumulation|renforcement|renfort|achats?)\s+(?:tactique|progressive?|"
    r"progressif|actif|active|agressive?|agressif|partielle?|massive?|massif|"
    r"sur\s+(?:repli|faiblesse|baisse|support|creux)))\b")
_GESTE_VENTE = re.compile(
    r"(?i)\b(all[ée]ger|all[èe]ge|vendre|vends|sortir|sors|liquider|liquide|"
    r"offloader|couper|coupe|[ée]cr[êe]ter|[ée]cr[êe]te|"
    r"r[ée]duire\s+(?:la\s+position|l'exposition)|"
    r"r[ée]duis\s+(?:la\s+position|l'exposition)|"
    r"prendre\s+(?:des\s+|les\s+|ses\s+)?(?:profits?|b[ée]n[ée]fices?)|"
    r"s[ée]curiser\s+(?:des\s+|les\s+)?gains|"
    # forme NOMINALE d'une prescription (« statuer (sortie ou réduction) »,
    # « envisager une prise de profit ») — audit 01/10
    r"statuer\s*\(?\s*(?:sur\s+(?:la|une)\s+)?(?:sortie|vente|r[ée]duction|all[èe]gement)|"
    r"envisager\s+(?:une\s+|la\s+|des\s+)?(?:sortie|vente|r[ée]duction|all[èe]gement|"
    r"prises?\s+de\s+profits?)|"
    # nom de geste + modalité d'exécution (« sortie progressive sur rebond »,
    # hebdo réel rejoué le 02/10) ; « sortie de capitaux », « réduction du
    # bilan » restent des constats (pas de modalité, ou pas un geste).
    r"(?:sortie|vente|all[èe]gement)\s+(?:progressive?|progressif|partielle?|"
    r"totale?|compl[èe]te?|sur\s+(?:rebond|force|la\s+force)))\b")
_NEGATION = re.compile(
    r"(?i)(\bne\s+pas\b|\bne\s+\w+\s+pas\b|\bn['’]|\bpas\s+de\b|\baucun(?:e)?\b|\bjamais\b|"
    r"\b[ée]viter\s+(?:de|d['’])|"
    r"\bsans\s+(?:renfor|all[ée]g|vend|achet))")
_PHRASES = re.compile(r"(?<=[.!?;])\s+")
# v33 (audit 01/10) — une TAILLE est une décision du moteur (bande) ou du
# radar (tranche) : un geste du modèle qui fixe sa propre taille n'est jamais
# porté, même dans le bon sens (hebdo réel : « Prendre des profits partiels
# sur QNT à 180 $ (30 % de la position) » quand le radar dit 50 %).
TAILLE_DU_MODELE = re.compile(
    r"\d+(?:[.,]\d+)?\s?%\s*(?:"
    r"de\s+(?:la|ta|sa|ma|cette|une)\s+(?:position|ligne)"
    r"|du\s+(?:portefeuille|PTF|capital)"
    r"|de\s+(?:ton|mon|son)\s+(?:portefeuille|PTF|capital)"
    r"|de\s+[A-Z][A-Z0-9]{1,9}\b)"
    # « alléger la position de 30 % » (hebdo réel rejoué, plan de la semaine)
    r"|(?i:\b(?:position|ligne|exposition)(?:\s+(?:de|d['’])\s*\w+)?"
    r"\s+de\s+[+−-]?\d+(?:[.,]\d+)?\s?%)")

# Audit 02/10 — MÉTRIQUES V30. Le plan V30 (R:R, EV pondérée par une
# probabilité heuristique p↑, take-profit échelonnés, scénarios bull/base/bear
# pondérés) et la confiance chiffrée du modèle ne sont plus des variables du
# système (Omar). Le modèle les recevait pourtant dans ses données, avec la
# consigne de les citer, et aucune garde ne retirait une phrase qui les citait
# sans verbe de geste (« BTC : R:R 0,7, EV 30j −0,9 % (p↑ 47 %) »). Une phrase
# qui les cite est retirée, qu'elle prescrive un geste ou non. Les
# probabilités de MARCHÉ (Polymarket, « probabilités de taux de la Fed ») ne
# sont pas visées.
METRIQUE_V30 = re.compile(
    r"(?i)\bR\s?[:/]\s?R\b"
    r"|\bratio\s+risque\s*[/-]\s*r[ée]compense\b|\brisk[\s/-]*reward\b"
    r"|\bEV\b\s*(?:\(?\s*30\s?j(?:ours)?\s*\)?)?\s*[:=]?\s*[+−-]?\s?\d"
    r"|\bp\s?↑"
    r"|\bTP\s?[123]\b|\btake[\s-]?profits?\b"
    r"|\bsc[ée]nario\s+(?:bull|base|bear|haussier|neutre|central|baissier)\b"
    r"[^.;\n]{0,25}?\d{1,3}(?:[.,]\d+)?\s?%"
    r"|\b(?:ma|sa|la)?\s?confiance\s+(?:(?:plafonn[ée]e|ramen[ée]e|limit[ée]e|"
    r"r[ée]elle|de)\s+)?(?:à\s+)?\d{1,3}(?:[.,]\d+)?\s?%")


# Position PRESCRIPTIVE d'un verbe de geste (audit 01/10, rejeu V30→V32) :
# « Cette annonce renforce l'adoption… », « les flux ETF renforcent le
# soutien du marché », « les ETF pourraient renforcer la demande » sont des
# CONSTATS — la garde les supprimait (le verbe a un sujet). Un geste est
# prescrit quand le verbe ouvre la phrase ou la proposition (après « : »,
# « → », « — », « , », « · », « alors »), suit une formule de conseil
# (« il faut », « je recommande de », « mieux vaut »…), ou prolonge par
# « et / puis » un geste déjà prescrit dans la même phrase.
_PUCES = " \t*•·-–—▸►→✓☐⚡📌🎯✅⚠️❗👉"
_AMORCE = re.compile(
    r"(?i)(?:[:→—–,;(·…]|\balors|\bil\s+faut|\bfaut|\bfaudrait|"
    r"\brecommand\w*\s+(?:de|d['’])|\bconseill\w*\s+(?:de|d['’])|"
    r"\benvisage\w*\s+(?:de|d['’])|\bpense[rz]?\s+à|"
    r"\bn['’]h[ée]site[rz]?\s+pas\s+à|\bmieux\s+vaut|\bon\s+peut|"
    r"\btu\s+peux|\bvous\s+pouvez|\bil\s+est\s+temps\s+(?:de|d['’])|"
    r"\b(?:opportun|prudent|judicieux|pertinent|sage|temps)\s+(?:de|d['’])|"
    r"\bil\s+convient\s+(?:de|d['’])|\bprivil[ée]gie[rz]?|\bplut[ôo]t|"
    # « rebond à exploiter pour couper », « profite du rebond pour alléger »
    r"\bà\s+exploiter\s+pour|\bexploite[rz]?\s+[^.]{0,40}?\bpour|"
    r"\b(?:profite[rz]?|utilise[rz]?)\s+[^.]{0,40}?\bpour)\s*$")
_LIAISON = re.compile(r"(?i)\b(?:et|puis|ou|avant\s+(?:de|d['’]))\s*$")


# Audit 02/10 — PRIX INVRAISEMBLABLES dans la narration. Les chiffres de la
# prose n'étaient vérifiés que pour quelques indicateurs (DXY, CPI, Fed, 7 j,
# RSI) : un « BTC 888 888 $ » (modèle hostile, analyse de la revue hebdo)
# restait publié. Un montant en dollars est jugé quand il est en POSITION DE
# PRIX : collé au ticker qui le précède (« BTC 84 166 $ », « BTC : … ») ou
# introduit par un mot de prix (« à », « sous », « support », « ATH »…). Une
# VALEUR de position (« BTC (38,5 % du PTF, 1 531 $) », « JASMY pèse 72 $ »)
# n'est jamais jugée comme un prix. Bornes : [cours / 10 ; 3 × max(cours,
# ATH)] — sans ATH mesuré, seule la borne basse s'applique (un ATH à 50× le
# cours est réel pour un satellite à −98 %).
_MONTANT_DOLLAR = re.compile(
    r"(?<![\d,.])(\d{1,3}(?:[  ]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)\s?\$")
_INDICE_PRIX = re.compile(
    r"(?i)\b(?:à|sous|au-dessus|au-dessous|vers|cours|prix|support|"
    r"r[ée]sistance|niveau|cible|ath|plus\s+haut|plus\s+bas|plancher|"
    r"teste|test|casse|cassure|franchi\w*|repli|rebond|touch\w*|cl[ôo]ture|"
    r"entre)\b")
_INDICE_VALEUR = re.compile(
    r"(?i)%|\b(?:valeur|valant|vaut|p[èe]se|position|ligne|ptf|portefeuille|"
    r"investi\w*|soit|total\w*|capitalisation|volume|tvl|flux|frais|gain\w*|"
    r"perte\w*|pnl|profit\w*|co[ûu]t\w*)\b")
_BORNE_BASSE, _BORNE_HAUTE = 0.1, 3.0


def _montant(txt: str) -> Optional[float]:
    t = re.sub(r"[  ]", "", txt).replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def prix_invraisemblable(
    phrase: str, prix: dict[str, tuple[Optional[float], Optional[float]]]
) -> Optional[str]:
    """Ticker dont un prix cité sort des bornes plausibles, sinon ``None``.

    Args:
        phrase: une phrase de prose.
        prix: ``{TICKER: (cours, ATH)}`` mesurés (ATH facultatif).
    """
    if not prix:
        return None
    reperes = sorted(
        (m.start(), m.end(), s) for s in prix
        for m in re.finditer(rf"(?<![A-Za-z0-9]){re.escape(s)}(?![A-Za-z0-9])", phrase))
    if not reperes:
        return None
    for m in _MONTANT_DOLLAR.finditer(phrase):
        avant = [r for r in reperes if r[1] <= m.start()]
        if not avant:
            continue
        _, fin, sym = avant[-1]
        segment = phrase[fin:m.start()]
        if len(segment) > 45 or re.search(r"[.;!?]\s", segment):
            continue
        if _INDICE_VALEUR.search(segment):
            continue
        colle = not segment.strip(" :·—–-(")
        if not (colle or _INDICE_PRIX.search(segment)):
            continue
        v = _montant(m.group(1))
        cours, ath = prix.get(sym) or (None, None)
        if v is None or not cours or cours <= 0:
            continue
        if v < cours * _BORNE_BASSE:
            return sym
        if ath and ath > 0 and v > _BORNE_HAUTE * max(cours, ath):
            return sym
    return None


def _prescrit(phrase: str, verbe: "re.Pattern[str]") -> bool:
    """Vrai si un verbe de ``verbe`` est en position prescriptive."""
    deja = False
    for m in sorted(list(_GESTE_ACHAT.finditer(phrase)) + list(_GESTE_VENTE.finditer(phrase)),
                    key=lambda x: x.start()):
        avant = phrase[:m.start()].rstrip()
        # Négation DE CE GESTE (dans sa proposition) : « ne pas renforcer »
        # s'abstient. Une négation ailleurs dans la phrase (« statuer (sortie
        # ou réduction), ne pas laisser dériver ») ne neutralise rien.
        if _NEGATION.search(re.split(r"[.;:—–→,(]", avant)[-1]):
            continue
        ouvre = not avant.strip(_PUCES) or bool(_AMORCE.search(avant))
        lie = deja and bool(_LIAISON.search(avant))
        if ouvre or lie:
            deja = True
            if verbe.match(phrase, m.start()):
                return True
    return False


def strip_unbacked_gestures(
    node: Any, allowed: dict[str, set[str]], univers: set[str],
    prix: Optional[dict[str, tuple[Optional[float], Optional[float]]]] = None,
) -> tuple[Any, list[str]]:
    """Retire les phrases prescrivant un geste non porté par une décision.

    Retire aussi (audit 02/10) les phrases qui citent une métrique V30 et
    celles qui citent un prix invraisemblable (``prix_invraisemblable``).

    Args:
        node: bloc de prose (chaîne, liste ou dict imbriqué).
        allowed: ``{ACTIF: {"RENFORCER", "ALLEGER"}}`` décidés aujourd'hui.
        univers: tickers connus, pour attribuer une phrase à un actif.
        prix: ``{TICKER: (cours, ATH)}`` mesurés, pour la plausibilité.

    Returns:
        ``(node, [corrections])``.
    """
    fixes: list[str] = []
    univ = {str(u).upper() for u in univers if u}

    def _porte(phrase: str) -> bool:
        if METRIQUE_V30.search(phrase):
            return False
        if prix and prix_invraisemblable(phrase, prix):
            return False
        achat = _prescrit(phrase, _GESTE_ACHAT)
        vente = _prescrit(phrase, _GESTE_VENTE)
        if not (achat or vente):
            return True
        if TAILLE_DU_MODELE.search(phrase):
            return False
        haut = phrase.upper()
        actifs = {s for s in univ
                  if re.search(rf"(?<![A-Z0-9]){re.escape(s)}(?![A-Z0-9])", haut)}
        if not actifs:
            return False
        sens = set()
        if achat:
            sens.add("RENFORCER")
        if vente:
            sens.add("ALLEGER")
        return all(sens <= allowed.get(a, set()) for a in actifs)

    def _fix(text: str) -> str:
        phrases = _PHRASES.split(text)
        gardees = [p for p in phrases if _porte(p)]
        if len(gardees) == len(phrases):
            return text
        for p in phrases:
            if p not in gardees:
                motif = ("métrique V30 retirée" if METRIQUE_V30.search(p)
                         else "prix invraisemblable retiré"
                         if prix and prix_invraisemblable(p, prix)
                         else "geste prescrit sans décision retiré")
                fixes.append(f"{motif} : « {p.strip()[:80]} »")
        return " ".join(gardees).strip()

    # Un élément de liste dont le texte PRINCIPAL a été entièrement retiré
    # disparaît avec lui : sinon une puce vide resterait rendue.
    principaux = ("text", "action", "trigger", "title", "detail",
                  "description", "analysis")

    def _walk(n: Any) -> Any:
        if isinstance(n, str):
            return _fix(n)
        if isinstance(n, list):
            out = []
            for x in n:
                new = _walk(x)
                if isinstance(x, str) and new != x and not str(new).strip():
                    continue
                if isinstance(x, dict) and isinstance(new, dict) and any(
                        isinstance(x.get(k), str) and x[k].strip()
                        and not str(new.get(k) or "").strip()
                        for k in principaux):
                    continue
                out.append(new)
            return out
        if isinstance(n, dict):
            return {k: (v if k in _CLES_INTACTES else _walk(v))
                    for k, v in n.items()}
        return n

    return _walk(node), fixes


# Audit 02/10 — décision d'Omar : la section des poussières de l'hebdo est
# une LISTE INFORMATIVE. Sa prose ne porte AUCUNE consigne de vente, même
# nominale ou en position de constat (« l'objectif est une liquidation
# immédiate sur tout sursaut de +30 % », « exécuter les ordres de vente sans
# état d'âme » — hebdo réel rejoué). Filtre plus strict que la garde de
# gestes, réservé à cette section : toute phrase qui parle de vendre sort.
_VENTE_LARGE = re.compile(
    r"(?i)\b(?:liquid(?:er|ez|ons|ation|ations)|vend(?:re|s|ez|ons|u|ue|us|ues)|"
    r"ventes?|c[ée]der|abandonn(?:er|ez|ons|é|ée|és|ées)|offload\w*|"
    r"all[ée]g(?:er|ez|ement|ements)|all[èe]ge|sortir|sorties?|sors|"
    r"prises?\s+de\s+(?:profits?|b[ée]n[ée]fices?)|nettoy(?:er|age))\b")


def retirer_consignes_de_vente(node: Any) -> tuple[Any, list[str]]:
    """Retire toute phrase parlant de vendre (section des poussières)."""
    fixes: list[str] = []

    def _fix(text: str) -> str:
        phrases = _PHRASES.split(text)
        gardees = [p for p in phrases if not _VENTE_LARGE.search(p)]
        for p in phrases:
            if p not in gardees:
                fixes.append(f"poussières, consigne de vente retirée : « {p.strip()[:80]} »")
        return " ".join(gardees).strip()

    def _walk(n: Any) -> Any:
        if isinstance(n, str):
            return _fix(n)
        if isinstance(n, list):
            return [y for y in (_walk(x) for x in n) if not (isinstance(y, str) and not y)]
        if isinstance(n, dict):
            return {k: _walk(v) for k, v in n.items()}
        return n

    return _walk(node), fixes


# Clés jamais réécrites par la garde de gestes (métadonnées et champs
# structurés déterministes).
_CLES_INTACTES = {"id", "date", "time_casablanca", "next_report_at", "asset",
                  "symbol", "source", "url", "cid", "label", "icon", "type",
                  "status", "tag", "direction", "engine_view", "v33_decision",
                  "action_type", "thesis_type"}
