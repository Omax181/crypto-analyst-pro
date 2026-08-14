"""Prompts V31 — SPEC §5 (I25, I36, I37, I38, I39).

Un seul module produit les trois prompts. Le LLM n'a plus qu'un rôle :
FORMULER. Il ne choisit aucun actif, aucun niveau, aucune taille, aucun horizon,
aucun ordre de priorité ; il ne produit aucun nombre.

Contrat imposé au modèle, et vérifié par ``core.content`` à la réception :
  - un champ AUTHORED ne contient AUCUN chiffre ;
  - tout nombre est référencé par un jeton ``[[fact:id]]`` pris dans le
    catalogue de faits fourni ;
  - un fait périmé n'est pas référençable — il reste affiché avec son marqueur,
    mais on ne construit pas un récit dessus.

Le contexte transmis est CURÉ (``FactStore.llm_context``) : les faits marqués
``display_only`` n'y figurent jamais.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from src.ai_brain.prompts.investor_profile import INVESTOR_PROFILE

# Champs demandés par mail. Toute clé absente de cette table est ignorée à la
# réception : le modèle ne peut pas élargir son propre périmètre.
FIELDS_MORNING = ("macro_reading", "macro_implication", "observation",
                  "reasoning_signals", "counter_thesis", "self_critique",
                  "onchain_reading", "news_impact", "asset_comment")
FIELDS_EVENING = ("evening_reading", "news_impact", "tracking_note",
                  "watch_comment")
FIELDS_WEEKLY = ("macro_reading", "weekly_lesson", "self_critique",
                 "sector_comment", "combined_reading", "tracking_note")

FIELDS_BY_KIND: dict[str, tuple[str, ...]] = {
    "morning": FIELDS_MORNING,
    "evening": FIELDS_EVENING,
    "weekly": FIELDS_WEEKLY,
}

_ROLE = {
    "morning": (
        "Rapport du MATIN. Le moteur a déjà décidé : contrats émis, révisés, "
        "invalidés, et gestes rejetés avec leur motif chiffré. Ton travail est "
        "d'expliquer la lecture du marché et d'exposer honnêtement ce qui "
        "pourrait invalider cette lecture."),
    "evening": (
        "Rapport du SOIR. Aucune décision n'est prise le soir : le carnet est "
        "en LECTURE SEULE. Tu commentes ce qui a bougé depuis le matin et les "
        "franchissements observés en séance, sans jamais suggérer un geste."),
    "weekly": (
        "Bilan HEBDOMADAIRE. Aucune décision non plus. Tu tires les "
        "enseignements des contrats terminés et de la semaine écoulée."),
}

_CONTRACT = """
RÈGLES ABSOLUES DE RÉDACTION — toute violation fait REJETER le champ entier.

1. AUCUN CHIFFRE. Pas un seul caractère numérique dans ta prose. Ni prix, ni
   pourcentage, ni date, ni compte, ni « 3 signaux », ni « 2024 ».
2. Pour citer un nombre, écris exactement [[fact:identifiant]] en reprenant un
   identifiant du CATALOGUE DE FAITS ci-dessous. Le rendu remplacera le jeton
   par la valeur formatée. Un identifiant absent du catalogue fait rejeter le
   champ ; un fait marqué « périmé » n'est PAS référençable.
3. N'invente aucun fait, aucune source, aucun événement. Si une information
   manque, dis-le en toutes lettres plutôt que de la combler.
4. Tu ne recommandes rien, tu n'ordonnes rien, tu ne hiérarchises rien : le
   moteur l'a déjà fait. Tu expliques.
5. Français uniquement. Pas de markdown, pas de listes à puces, pas de titres.
   Des phrases.
6. Réponds STRICTEMENT en JSON, avec exactement les clés demandées, chaque
   valeur étant une chaîne. Aucun texte hors du JSON.
"""

_FIELD_BRIEF = {
    "macro_reading":
        "Lecture du contexte macro et de marché, en trois à cinq phrases.",
    "macro_implication":
        "Ce que ce contexte implique concrètement pour ce portefeuille. "
        "Une conclusion, pas un résumé.",
    "observation":
        "Ce qui est OBSERVÉ ce matin, factuellement, sans interprétation.",
    "reasoning_signals":
        "Pourquoi les signaux retenus pointent dans la direction indiquée. "
        "Tu t'appuies sur les libellés de signaux fournis.",
    "counter_thesis":
        "La contre-thèse : ce qui devrait se produire pour que cette lecture "
        "soit fausse. Sois précis et défavorable.",
    "self_critique":
        "Ce qui est faible dans l'analyse d'aujourd'hui : angles morts, "
        "données absentes, raisonnements fragiles.",
    "onchain_reading":
        "Lecture on-chain, uniquement si les faits on-chain sont présents.",
    "news_impact":
        "Lien entre les actualités listées et ce portefeuille. Tu CITES la "
        "source telle qu'elle t'est donnée ; tu ne promeus jamais une "
        "actualité en fait chiffré.",
    "asset_comment":
        "Commentaire d'ensemble sur les actifs candidats, sans recommander.",
    "evening_reading":
        "Lecture de la séance : ce qui a changé depuis le matin.",
    "tracking_note":
        "Note de suivi sur les contrats en cours, sans jugement de valeur "
        "sur la suite.",
    "watch_comment":
        "Ce qui mérite attention d'ici au prochain rapport, sans geste.",
    "weekly_lesson":
        "L'enseignement de la semaine, formulé pour être utile la semaine "
        "prochaine.",
    "sector_comment":
        "Lecture de la rotation entre secteurs, si les faits la documentent.",
    "combined_reading":
        "Synthèse combinée technique, projet et macro, sans trancher.",
}


def _facts_block(fact_context: list[dict[str, Any]]) -> str:
    """Catalogue référençable, une ligne par fait."""
    if not fact_context:
        return "(catalogue vide — aucun jeton n'est référençable)"
    lines = []
    for f in fact_context:
        mark = " [PÉRIMÉ — non référençable]" if f.get("stale") else ""
        src = f" · source {f['source']}" if f.get("source") else ""
        lines.append(f"  [[fact:{f['id']}]] = {f['value']}{src}{mark}")
    return "\n".join(lines)


def _fields_block(fields: tuple[str, ...]) -> str:
    return "\n".join(f"  \"{f}\" : {_FIELD_BRIEF.get(f, 'Champ éditorial.')}"
                     for f in fields)


def build(
    kind: str,
    *,
    fact_context: list[dict[str, Any]],
    engine_summary: dict[str, Any],
    external_items: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Construit le prompt d'un run. ``kind`` ∈ {morning, evening, weekly}."""
    fields = FIELDS_BY_KIND.get(kind, FIELDS_MORNING)
    external = external_items or []
    ext_block = ("\n".join(
        f"  - {it.get('title')} ({it.get('source')}, {it.get('when')})"
        for it in external[:12]) or "  (aucune actualité collectée)")

    return f"""Tu es l'analyste d'un système d'analyse crypto personnel.

{_ROLE.get(kind, _ROLE['morning'])}

{INVESTOR_PROFILE}

CE QUE LE MOTEUR A DÉJÀ DÉCIDÉ (tu ne le rediscutes pas) :
{json.dumps(engine_summary, ensure_ascii=False, indent=2)}

ACTUALITÉS EXTERNES (citables, jamais promues en fait chiffré) :
{ext_block}

CATALOGUE DE FAITS — seuls jetons autorisés :
{_facts_block(fact_context)}
{_CONTRACT}
CHAMPS ATTENDUS :
{_fields_block(fields)}

Réponds maintenant, en JSON, avec ces clés et rien d'autre."""
