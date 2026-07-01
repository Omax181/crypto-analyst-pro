"""Formatage des messages du bot pour Telegram (v21).

Le LLM (et les commandes) produisent du Markdown « GitHub » (``**gras**``, puces
``*``/``-``, titres ``#``, ``code``). Le parse_mode « Markdown » LEGACY de
Telegram attend ``*gras*`` (simple) et casse sur le moindre caractère spécial →
le message retombait en texte brut, d'où les ``**`` affichés littéralement.

On convertit donc en **HTML Telegram** (balises ``<b> <i> <code>`` ; seuls
``& < >`` à échapper) — bien plus robuste. Si l'envoi HTML échoue malgré tout,
``telegram_api`` retombe sur ``strip_html`` (texte propre sans balises).
"""

from __future__ import annotations

import html
import re

_CODE_RE = re.compile(r"`([^`\n]+)`")
_HEADER_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*$", re.MULTILINE)
_BOLD2_RE = re.compile(r"\*\*([^\n*]+?)\*\*")            # **gras**
_BOLD1_RE = re.compile(r"(?<![*\w])\*([^\s*][^\n*]*?)\*(?!\w)")  # *gras*
_ITALIC_RE = re.compile(r"(?<![_\w])_([^\s_][^\n_]*?)_(?!\w)")   # _italique_
_BULLET_RE = re.compile(r"^([ \t]*)[\*\-•][ \t]+", re.MULTILINE)
_TAG_RE = re.compile(r"<[^>]+>")


def to_telegram_html(text: str) -> str:
    """Convertit un texte Markdown-ish en HTML compatible Telegram.

    Ordre important : échappement HTML d'abord, puis code, titres, gras, italique,
    puis puces (pour ne pas confondre une puce ``* `` avec du gras).
    """
    if not text:
        return ""
    out = html.escape(text, quote=False)                 # & < >
    out = _CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    out = _HEADER_RE.sub(lambda m: f"<b>{m.group(1)}</b>", out)
    out = _BOLD2_RE.sub(lambda m: f"<b>{m.group(1)}</b>", out)
    out = _BOLD1_RE.sub(lambda m: f"<b>{m.group(1)}</b>", out)
    out = _ITALIC_RE.sub(lambda m: f"<i>{m.group(1)}</i>", out)
    out = _BULLET_RE.sub(lambda m: f"{m.group(1)}• ", out)
    return out


def strip_html(text: str) -> str:
    """Retire les balises HTML et dé-échappe les entités (repli texte brut)."""
    if not text:
        return ""
    return html.unescape(_TAG_RE.sub("", text))
