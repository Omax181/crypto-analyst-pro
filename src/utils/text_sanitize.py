"""Neutralisation des SURROGATES Unicode isolés (robustesse UTF-8).

Un ``str`` Python peut contenir des code points U+D800–U+DFFF (surrogates)
lorsqu'une source — typiquement la sortie d'un LLM — émet un caractère astral
TRONQUÉ (moitié d'emoji). Ces code points sont INVALIDES en UTF-8 : toute
écriture fichier (``fh.write`` du state) ou tout envoi mail (encodage MIME
UTF-8) planterait alors avec ``UnicodeEncodeError: surrogates not allowed`` —
observé en prod le 06/07 (weekly Gemini → crash AVANT l'envoi du mail).

On applique la neutralisation À LA SOURCE (sortie Gemini) pour que ni le state,
ni le mail, ni Telegram ne voient jamais un surrogate, avec un filet ultime au
point d'écriture du state.
"""

from __future__ import annotations

import re

# En mémoire, un str Python est une suite de code points : TOUT surrogate y est
# « isolé » (jamais apparié comme en UTF-16). On les cible donc tous.
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")
# Paire VALIDE adjacente : high (D800-DBFF) suivi de low (DC00-DFFF).
_PAIR_RE = re.compile(r"[\ud800-\udbff][\udc00-\udfff]")

# U+FFFD « REPLACEMENT CHARACTER » (échappé → source ASCII, sans dépendance
# d'encodage du fichier).
_REPLACEMENT = "�"


def _join_pair(m: "re.Match[str]") -> str:
    """Recombine une paire high+low en son caractère astral (arithmétique
    UTF-16 exacte, vérifiée sur les bornes U+10000 et U+10FFFF)."""
    hi, lo = m.group(0)
    return chr(0x10000 + ((ord(hi) - 0xD800) << 10) + (ord(lo) - 0xDC00))


def strip_surrogates(text: str, replacement: str = _REPLACEMENT) -> str:
    """Rend ``text`` sûr pour un encodage UTF-8 strict.

    1. RECOMBINE chaque paire valide (high+low adjacents) en son caractère
       astral d'origine — récupère l'emoji au lieu de le détruire. Par PAIRE
       (regex), pas par aller-retour UTF-16 global : un orphelin ailleurs dans
       le texte ne condamne plus les paires récupérables (l'ancien roundtrip
       échouait en bloc sur un texte mixte paire+orphelin).
    2. Remplace tout surrogate resté ORPHELIN par ``replacement`` (U+FFFD par
       défaut, le « caractère de remplacement » Unicode).

    Non-``str`` ou chaîne vide → renvoyé tel quel. Idempotent.
    """
    if not text or not isinstance(text, str):
        return text
    if not _SURROGATE_RE.search(text):
        return text
    text = _PAIR_RE.sub(_join_pair, text)
    if _SURROGATE_RE.search(text):
        text = _SURROGATE_RE.sub(replacement, text)
    return text


def strip_surrogates_deep(obj: object) -> object:
    """Applique ``strip_surrogates`` RÉCURSIVEMENT (dict / list / tuple / str).

    Indispensable pour la sortie JSON d'un LLM : un surrogate peut y être
    ÉCHAPPÉ en ASCII pur (``\\ud83c`` sur 6 caractères) — invisible pour la
    sanitisation du texte brut — puis DÉCODÉ par ``json.loads`` en véritable
    surrogate isolé dans les VALEURS du dict (crash weekly du 06/07, run #28 :
    le payload passait la sanitisation source mais empoisonnait le mail).
    Les clés de dict sont nettoyées aussi. Types non-conteneurs → inchangés.
    """
    if isinstance(obj, str):
        return strip_surrogates(obj)
    if isinstance(obj, dict):
        return {
            (strip_surrogates(k) if isinstance(k, str) else k):
                strip_surrogates_deep(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [strip_surrogates_deep(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(strip_surrogates_deep(v) for v in obj)
    return obj
