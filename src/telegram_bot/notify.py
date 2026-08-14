"""Notification Telegram — SPEC V31 §11 (I59, I60).

Le message est une PROJECTION du payload déjà rendu : mêmes chiffres, mêmes
chaînes, même vocabulaire que le mail. Il ne relit aucune source, ne recalcule
rien et n'invente aucun format — toute chaîne numérique vient déjà de
``core.formatter`` via ``reporting.render``.

C'est ce qui rend impossible la divergence mail / Telegram de la v30, où deux
chemins de formatage indépendants produisaient « $64,489 » d'un côté et
« 64 489 $ » de l'autre.
"""

from __future__ import annotations

import html
from typing import Any, Optional

from src.telegram_bot import telegram_api
from src.utils.logger import get_logger

logger = get_logger(__name__)

MAX_LEN = 3500

_HEAD = {
    "morning": "Rapport du matin",
    "evening": "Point du soir",
    "weekly": "Bilan de la semaine",
}


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def _line(parts: list[Optional[str]], sep: str = " · ") -> str:
    return sep.join(p for p in parts if p)


def build_message(payload: dict[str, Any], kind: str) -> str:
    """Construit le message. Fonction PURE — testable sans réseau."""
    out: list[str] = [f"<b>{_esc(_HEAD.get(kind, 'Rapport'))}</b> · "
                      f"{_esc(payload.get('date_label'))}"]

    banner = payload.get("banner")
    if banner:
        out.append(f"\n⚠ {_esc(banner)}")

    if kind == "morning":
        top = payload.get("top_action")
        if top:
            out.append(
                f"\n<b>Le geste :</b> {_esc(top['direction'])} "
                f"{_esc(top['asset'])} — {_esc(top['notional'])}")
            out.append(_line([
                f"entrée {_esc(top['entry'])}",
                f"cible {_esc(top['target'])}",
                f"invalidation {_esc(top['stop'])}"]))
            out.append(f"espérance nette {_esc(top['net_pnl'])} · "
                       f"avantage exigé {_esc(top['delta_required'])}")
        else:
            out.append(f"\n<b>Aucun geste.</b> {_esc(payload.get('nothing_to_do'))}")

        rejections = payload.get("rejections") or []
        if rejections:
            out.append("\n<b>Écartés</b>")
            for r in rejections[:5]:
                out.append(f"• {_esc(r['asset'])} — {_esc(r['reason'])}")

    elif kind == "evening":
        intraday = payload.get("intraday") or []
        if intraday:
            out.append("\n<b>Franchi en séance</b>")
            for w in intraday[:5]:
                out.append(f"• {_esc(w['asset'])} — {_esc(w['kind'])} "
                           f"{_esc(w['level'])} (cours {_esc(w['spot'])})")
        else:
            out.append("\nAucun franchissement observé en séance.")
        out.append("Le carnet n'est pas modifié le soir : les changements "
                   "d'état s'évaluent sur la clôture.")

    else:
        metrics = payload.get("metrics") or []
        published = [m for m in metrics if m.get("published")]
        if published:
            out.append("\n<b>Mesures</b>")
            for m in published[:4]:
                out.append(f"• {_esc(m['question'])} {_esc(m.get('value'))} "
                           f"({_esc(m['window'])}, n = {_esc(m['n'])})")

    transitions = payload.get("transitions") or []
    if transitions:
        out.append("\n<b>Changements d'état</b>")
        for t in transitions[:6]:
            out.append(f"• {_esc(t['asset'])} — {_esc(t['to'])} "
                       f"({_esc(t['cause'])})")

    book = payload.get("book") or {}
    active = book.get("active") or []
    if active:
        out.append(f"\n<b>Carnet</b> · {_esc(book.get('active_count'))} en cours")
        for r in active[:6]:
            out.append(f"• {_esc(r['asset'])} {_esc(r['current'])} "
                       f"({_esc(r['delta'])}) — cible {_esc(r['target'])}, "
                       f"invalidation {_esc(r['stop'])}")

    out.append("\n/carnet /ptf /sources /aide")
    text = "\n".join(out)
    return text[:MAX_LEN]


def push(payload: dict[str, Any], kind: str) -> bool:
    """Envoie la notification. Ne lève jamais : un échec ne compromet pas le run."""
    try:
        message = build_message(payload, kind)
    except Exception as exc:                                    # noqa: BLE001
        logger.warning("Notification Telegram non construite : %s", exc)
        return False
    try:
        return bool(telegram_api.send_message(message))
    except Exception as exc:                                    # noqa: BLE001
        logger.warning("Notification Telegram non envoyée : %s", exc)
        return False
