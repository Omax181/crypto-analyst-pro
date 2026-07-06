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

# U+FFFD « REPLACEMENT CHARACTER » (échappé → source ASCII, sans dépendance
# d'encodage du fichier).
_REPLACEMENT = "�"


def strip_surrogates(text: str, replacement: str = _REPLACEMENT) -> str:
    """Rend ``text`` sûr pour un encodage UTF-8 strict.

    1. Tente de RECOMBINER les paires valides (high+low adjacents) en leur
       caractère astral d'origine — récupère l'emoji au lieu de le détruire.
    2. Remplace tout surrogate resté ORPHELIN par ``replacement`` (U+FFFD par
       défaut, le « caractère de remplacement » Unicode).

    Non-``str`` ou chaîne vide → renvoyé tel quel. Idempotent.
    """
    if not text or not isinstance(text, str):
        return text
    # 1. Recombinaison des paires (le cas prod : un emoji splitté en 2 moitiés).
    #    surrogatepass laisse passer les surrogates à l'encodage UTF-16 ; le
    #    décodage UTF-16 standard réassemble les paires valides. Un orphelin
    #    isolé fait lever UnicodeError → on garde le texte et on strippe en 2.
    if _SURROGATE_RE.search(text):
        try:
            text = text.encode("utf-16", "surrogatepass").decode("utf-16")
        except UnicodeError:
            pass
    # 2. Strip des orphelins restants (impairs, non recombinables).
    if _SURROGATE_RE.search(text):
        text = _SURROGATE_RE.sub(replacement, text)
    return text
