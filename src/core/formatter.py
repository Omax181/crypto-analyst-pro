"""Formatage numérique — AUTORITÉ UNIQUE (SPEC V31 §1.3, I27 / I59 / I60).

AUCUN autre module du projet n'a le droit de convertir un nombre en chaîne
destinée à un humain : mail, graphique matplotlib, message Telegram, sujet
d'email, log utilisateur. Toute représentation textuelle d'un nombre passe par
ce module, et par lui seul.

Convention FRANÇAISE unique, sans exception :
  - séparateur décimal     : virgule
  - séparateur de milliers : espace fine insécable U+202F
  - devise                 : suffixe « <U+202F>$ »
  - signe négatif          : moins typographique U+2212
  - valeur absente         : tiret cadratin U+2014 (JAMAIS 0, JAMAIS vide)

Le test I60 balaie la SORTIE RENDUE (HTML, texte Telegram, annotations de
graphique, sujet) : un point décimal ou une virgule de milliers dans un nombre
y fait échouer la suite.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Optional

NNBSP = " "     # espace fine insécable — séparateur de milliers
MINUS = "−"     # moins typographique — nombres négatifs
ABSENT = "—"    # tiret cadratin — valeur absente

_MOIS_FR = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
            "août", "septembre", "octobre", "novembre", "décembre")
_JOURS_FR = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi",
             "dimanche")


def _coerce(value: Any) -> Optional[float]:
    """Convertit en float fini, ou ``None``. Ne devine jamais."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v if math.isfinite(v) else None
    return None


def _fr(anglo: str) -> str:
    """« 64,489.00 » -> « 64<NNBSP>489,00 ». Brique commune, jamais exportée."""
    return anglo.replace(",", NNBSP).replace(".", ",")


def _signed(s: str, negative: bool, *, explicit_plus: bool) -> str:
    if negative:
        return MINUS + s
    return ("+" + s) if explicit_plus else s


def num(value: Any, nd: int = 2, *, sign: bool = False) -> str:
    """Nombre générique à ``nd`` décimales fixes."""
    v = _coerce(value)
    if v is None:
        return ABSENT
    neg = v < 0
    s = _fr(f"{abs(v):,.{nd}f}")
    return _signed(s, neg, explicit_plus=sign)


def integer(value: Any, *, sign: bool = False) -> str:
    """Entier avec séparateur de milliers (« 534<NNBSP>028 »)."""
    v = _coerce(value)
    if v is None:
        return ABSENT
    neg = v < 0
    s = _fr(f"{abs(v):,.0f}")
    return _signed(s, neg, explicit_plus=sign)


def ratio(value: Any, nd: int = 2) -> str:
    """Ratio sans unité (« 1,25 », « 0,58 »). Utilisé pour R:R, put/call, HHI."""
    return num(value, nd)


def price(value: Any) -> str:
    """Prix adaptatif suffixé « $ ».

    >= 1000 : 0 décimale · >= 1 : 2 · >= 0,01 : 4 · < 0,01 : 4 chiffres
    significatifs sans notation scientifique. Valeur nulle/négative -> ABSENT
    (un prix n'est jamais <= 0).
    """
    v = _coerce(value)
    if v is None or v <= 0:
        return ABSENT
    if v >= 1000:
        body = _fr(f"{v:,.0f}")
    elif v >= 1:
        body = _fr(f"{v:,.2f}")
    elif v >= 0.01:
        body = _fr(f"{v:.4f}")
    else:
        exp = math.floor(math.log10(v))
        decimals = min(-exp + 3, 18)
        body = _fr(f"{v:.{decimals}f}".rstrip("0").rstrip("."))
    return body + NNBSP + "$"


def usd(value: Any, *, nd: int = 2, sign: bool = False) -> str:
    """Montant signé suffixé « $ » (peut être négatif, contrairement à price)."""
    v = _coerce(value)
    if v is None:
        return ABSENT
    if v == 0:
        return "0" + NNBSP + "$"
    neg = v < 0
    a = abs(v)
    if a >= 1:
        body = _fr(f"{a:,.{nd}f}")
    elif a >= 0.01:
        body = _fr(f"{a:,.4f}")
    else:
        exp = math.floor(math.log10(a))
        decimals = min(-exp + 3, 18)
        body = _fr(f"{a:,.{decimals}f}".rstrip("0").rstrip("."))
    return _signed(body, neg, explicit_plus=sign) + NNBSP + "$"


def compact_usd(value: Any) -> str:
    """Montant compact : « 2,3<NNBSP>Mds$ » · « 142<NNBSP>M$ » · « 890<NNBSP>k$ »."""
    v = _coerce(value)
    if v is None:
        return ABSENT
    neg = v < 0
    a = abs(v)
    if a >= 1e12:
        body, unit = f"{a / 1e12:.1f}".rstrip("0").rstrip("."), "Bn$"
    elif a >= 1e9:
        body, unit = f"{a / 1e9:.1f}".rstrip("0").rstrip("."), "Mds$"
    elif a >= 1e6:
        body, unit = f"{a / 1e6:.1f}".rstrip("0").rstrip("."), "M$"
    elif a >= 1e3:
        body, unit = f"{a / 1e3:.0f}", "k$"
    else:
        return usd(value)
    return _signed(body.replace(".", ","), neg, explicit_plus=False) + NNBSP + unit


def pct(value: Any, nd: int = 1, *, sign: bool = True) -> str:
    """Pourcentage (« +3,4% », « −12,2% », « 0,0% »).

    ``value`` est exprimée EN POINTS DE POURCENTAGE (3.4 -> « +3,4% »).
    """
    v = _coerce(value)
    if v is None:
        return ABSENT
    if round(v, nd) == 0:
        v = 0.0  # évite « −0,0% »
    neg = v < 0
    body = _fr(f"{abs(v):,.{nd}f}")
    return _signed(body, neg, explicit_plus=sign) + "%"


def fraction_as_pct(value: Any, nd: int = 1, *, sign: bool = False) -> str:
    """Fraction -> pourcentage (0.05 -> « 5,0% »). Pour delta_claimable, p*, p0."""
    v = _coerce(value)
    if v is None:
        return ABSENT
    return pct(v * 100.0, nd, sign=sign)


def sigma(value: Any, nd: int = 2) -> str:
    """Distance exprimée en écarts-types d'horizon (« 0,09 σ »)."""
    v = _coerce(value)
    if v is None:
        return ABSENT
    return num(v, nd) + NNBSP + "σ"


def days(value: Any) -> str:
    """Durée en jours (« 27 j »)."""
    v = _coerce(value)
    if v is None:
        return ABSENT
    return f"{int(v)}{NNBSP}j"


def date_fr(value: Any) -> str:
    """Date française courte (« 8 août »). Accepte date/datetime/ISO."""
    d = _as_date(value)
    if d is None:
        return ABSENT
    return f"{d.day} {_MOIS_FR[d.month - 1]}"


def date_full_fr(value: Any) -> str:
    """Date française complète (« vendredi 8 août 2026 »)."""
    d = _as_date(value)
    if d is None:
        return ABSENT
    return (f"{_JOURS_FR[d.weekday()]} {d.day} {_MOIS_FR[d.month - 1]} "
            f"{d.year}")


def date_short(value: Any) -> str:
    """Date numérique courte (« 08/08 »)."""
    d = _as_date(value)
    if d is None:
        return ABSENT
    return f"{d.day:02d}/{d.month:02d}"


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


# ── détection d'infraction — support des tests I60 ─────────────────────────

import re as _re  # noqa: E402  (import bas volontaire : usage interne au test)

# Un nombre est FAUTIF s'il contient un point décimal entre deux chiffres, ou
# une virgule utilisée comme séparateur de milliers (« 7,758 » = 3 chiffres
# après la virgule sans autre virgule autour).
_BAD_DOT = _re.compile(r"\d\.\d")
_BAD_THOUSAND_COMMA = _re.compile(r"(?<!\d)\d{1,3},\d{3}(?!\d)")


def find_format_violations(text: str) -> list[str]:
    """Renvoie les motifs numériques non conformes trouvés dans une SORTIE.

    Support de l'invariant I60. Utilisé par les tests sur le HTML rendu, le
    texte Telegram et les annotations de graphique — jamais sur le code source
    (le contrôle sur le source a laissé passer « R:R 1.2 » en v30.1).
    """
    if not isinstance(text, str):
        return []
    out: list[str] = []
    for m in _BAD_DOT.finditer(text):
        out.append(text[max(0, m.start() - 12):m.end() + 12])
    for m in _BAD_THOUSAND_COMMA.finditer(text):
        out.append(text[max(0, m.start() - 12):m.end() + 12])
    return out
