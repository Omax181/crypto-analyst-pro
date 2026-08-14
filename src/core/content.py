"""Contrat de contenu — SPEC V31 §5 (I25 à I29, I36 à I39).

Trois classes, une seule par champ :

  DERIVED   Python. Chiffres et calculs. Le LLM ne l'écrit jamais.
  AUTHORED  LLM. Jugement, hiérarchie, formulation. AUCUN numéral brut :
            les nombres sont référencés par jeton ``[[fact:id]]``.
  EXTERNAL  LLM lecteur d'une source non maîtrisée. Citation attribuée,
            jamais promue en fait, jamais dans un calcul.

VALIDATION PAR REJET, jamais par réparation. Une réparation prétend connaître
l'intention de l'auteur ; un rejet ne prétend rien. C'est ce renversement qui
rend impossible le défaut ``_dedupe_segments`` de la v30 (une garde qui
corrompait les décimales tout en journalisant « segment dupliqué retiré »).

Hiérarchie de repli, dans l'ordre : omission -> génération déterministe ->
déclaration d'absence. Un repli n'imite JAMAIS le champ écarté.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional

from markupsafe import Markup, escape

from src.core.facts import FactStore


class FieldClass(str, Enum):
    DERIVED = "DERIVED"
    AUTHORED = "AUTHORED"
    EXTERNAL = "EXTERNAL"


class FallbackLevel(str, Enum):
    OMISSION = "omission"
    DETERMINISTIC = "deterministic"
    DECLARATION = "declaration"


# Jeton délibérément distinct de la syntaxe Jinja ``{{ }}`` : aucune confusion
# possible, aucune ré-évaluation accidentelle au rendu.
TOKEN_RE = re.compile(r"\[\[fact:([A-Za-z0-9_.\-]+)\]\]")

# Un champ AUTHORED ne contient AUCUN chiffre hors jeton. Règle stricte et
# vérifiable par machine : les comptes sont dérivables, donc DERIVED.
_DIGIT_RE = re.compile(r"\d")


@dataclass(frozen=True)
class FieldSpec:
    path: str
    field_class: FieldClass
    fallback: FallbackLevel
    fallback_text: Optional[str] = None
    deterministic: Optional[Callable[[Any], Optional[str]]] = None

    def __post_init__(self) -> None:
        if (self.fallback is FallbackLevel.DECLARATION
                and not self.fallback_text):
            raise ValueError(f"{self.path} : repli DECLARATION sans texte (I29)")


# ── déclarations d'absence — écrites à la main, une par TYPE de champ ──────
# Elles disent ce qui manque. Elles n'imitent jamais le contenu écarté.
DECL_MACRO = "Lecture macro non disponible pour ce rapport."
DECL_IMPLICATION = "Conclusion actionnable non disponible pour ce rapport."
DECL_CRITIQUE = "Auto-critique non disponible pour ce rapport."
DECL_COUNTER = "Contre-thèse non disponible pour cette recommandation."
DECL_NEWS_IMPACT = "Lien avec le portefeuille non établi pour cette information."
DECL_GENERIC = "Commentaire non disponible pour ce rapport."


SCHEMA: dict[str, FieldSpec] = {}


def _reg(path: str, klass: FieldClass, fallback: FallbackLevel,
         text: Optional[str] = None) -> None:
    SCHEMA[path] = FieldSpec(path, klass, fallback, text)


# Champs éditoriaux irréductibles -> déclaration d'absence.
_reg("macro_reading", FieldClass.AUTHORED, FallbackLevel.DECLARATION, DECL_MACRO)
_reg("macro_implication", FieldClass.AUTHORED, FallbackLevel.DECLARATION,
     DECL_IMPLICATION)
_reg("self_critique", FieldClass.AUTHORED, FallbackLevel.DECLARATION,
     DECL_CRITIQUE)
_reg("counter_thesis", FieldClass.AUTHORED, FallbackLevel.DECLARATION,
     DECL_COUNTER)
_reg("news_impact", FieldClass.EXTERNAL, FallbackLevel.DECLARATION,
     DECL_NEWS_IMPACT)
_reg("weekly_lesson", FieldClass.AUTHORED, FallbackLevel.DECLARATION,
     DECL_GENERIC)
_reg("evening_reading", FieldClass.AUTHORED, FallbackLevel.DECLARATION,
     DECL_MACRO)

# Champs dont l'absence est acceptable : la section se rend sans eux.
for _p in ("sector_comment", "asset_comment", "tracking_note",
           "watch_comment", "onchain_reading"):
    _reg(_p, FieldClass.AUTHORED, FallbackLevel.OMISSION)

# Champs dérivables : le gabarit reconstruit une énumération déterministe.
for _p in ("observation", "reasoning_signals", "combined_reading"):
    _reg(_p, FieldClass.AUTHORED, FallbackLevel.DETERMINISTIC)


@dataclass
class Rejection:
    path: str
    rule: str
    detail: str


def validate_authored(text: Any, referenceable: set[str]) -> list[str]:
    """Violations d'un champ AUTHORED. Liste vide = conforme."""
    if text is None:
        return []
    if not isinstance(text, str):
        return ["type non textuel"]
    tokens = TOKEN_RE.findall(text)
    stripped = TOKEN_RE.sub("", text)
    problems: list[str] = []
    if _DIGIT_RE.search(stripped):
        problems.append("numéral brut hors jeton")
    for tok in tokens:
        if tok not in referenceable:
            problems.append(f"jeton inconnu ou périmé : {tok}")
    return problems


def substitute(text: str, render_map: dict[str, str]) -> Markup:
    """Échappe PUIS substitue (I26, sécurité du gabarit).

    L'échappement précède la substitution : un jeton ne peut pas réintroduire
    de balise, et les valeurs injectées sont elles-mêmes échappées.
    """
    escaped = str(escape(text))

    def _sub(m: re.Match) -> str:
        return str(escape(render_map.get(m.group(1), "")))

    return Markup(TOKEN_RE.sub(_sub, escaped))


def apply_contract(
    payload: dict[str, Any], store: FactStore,
    deterministic: Optional[dict[str, str]] = None,
) -> tuple[dict[str, Any], list[Rejection]]:
    """Valide et rend les champs AUTHORED/EXTERNAL. Rejette, ne répare jamais.

    Returns:
        (payload nettoyé, rejets). Un champ rejeté est REMPLACÉ par son repli
        et n'apparaît jamais partiellement corrigé.
    """
    out = dict(payload)
    rejections: list[Rejection] = []
    referenceable = store.referenceable_ids()
    render_map = store.render_map()
    det = deterministic or {}

    for path, spec in SCHEMA.items():
        if path not in out:
            continue
        value = out[path]
        if spec.field_class is FieldClass.DERIVED:
            continue
        problems = validate_authored(value, referenceable)
        if not problems:
            if isinstance(value, str):
                out[path] = substitute(value, render_map)
            continue
        rejections.append(Rejection(path, "contrat AUTHORED",
                                    " ; ".join(problems)))
        if spec.fallback is FallbackLevel.OMISSION:
            out.pop(path, None)
        elif spec.fallback is FallbackLevel.DETERMINISTIC:
            replacement = det.get(path)
            if replacement:
                out[path] = substitute(replacement, render_map)
            else:
                out.pop(path, None)
        else:
            out[path] = Markup(str(escape(spec.fallback_text)))
    return out, rejections


def rejection_alarm_level(rejections: list[Rejection]) -> Optional[str]:
    """Alarme opérationnelle (SPEC §5.3). Sans effet sur le contenu."""
    n = len(rejections)
    if n >= 3:
        return "banner"     # mentionné dans le bandeau de dégradation
    if n >= 1:
        return "warning"    # log WARNING seulement
    return None
