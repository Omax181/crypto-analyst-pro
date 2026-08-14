"""Chien de garde — SPEC V31 §10 (R10-1).

Vérifie que le dernier run RÉUSSI n'est pas trop ancien, et alerte si c'est le
cas. Il n'envoie AUCUN rapport : produire un mail depuis le chien de garde
reviendrait à ajouter un quatrième run, avec sa propre collecte et son propre
risque de contredire les trois autres.

Sans paramètre ``watchdog`` dans ``config/params.yaml``, la fonction est
DÉSACTIVÉE et le dit — jamais de seuil inventé (SPEC §9.3).

Usage :  python -m scripts.watchdog
"""

from __future__ import annotations

import sys

from src.core import runlog
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _alert(text: str) -> bool:
    try:
        from src.telegram_bot import telegram_api
        if not telegram_api.bot_configured():
            logger.warning("Alerte non transmise : bot Telegram non configuré.")
            return False
        return bool(telegram_api.send_message(text))
    except Exception as exc:                                    # noqa: BLE001
        logger.warning("Alerte non transmise : %s", exc)
        return False


def main() -> int:
    verdict = runlog.watchdog_verdict()
    if not verdict.get("enabled"):
        logger.info("Chien de garde désactivé : %s", verdict.get("reason"))
        return 0
    if not verdict.get("alert"):
        logger.info("Dernier run à %s h de silence (limite %s h) — rien à "
                    "signaler.", verdict.get("silence_hours"),
                    verdict.get("limit_hours"))
        return 0

    silence = verdict.get("silence_hours")
    limit = verdict.get("limit_hours")
    reason = verdict.get("reason")
    if reason:
        message = f"⚠ Chien de garde : {reason}."
    else:
        message = (f"⚠ Chien de garde : aucun run réussi depuis {silence} h "
                   f"(limite {limit} h). Dernier run connu : "
                   f"{verdict.get('last_run')}.")
    logger.error(message)
    _alert(message)
    # Sortie non nulle : l'alerte est visible dans l'onglet Actions, même si le
    # canal de notification est indisponible.
    return 1


if __name__ == "__main__":
    sys.exit(main())
