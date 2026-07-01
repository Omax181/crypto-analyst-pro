"""Runner principal du bot Telegram (Chantier G).

Deux modes d'éveil, choisis automatiquement par ``main()`` :

  • MODE RELAIS (v18.1, défaut quand ``RELAY_PULL_URL`` est défini) — « réveil
    par message » : un Worker Cloudflare reçoit le webhook Telegram, empile le
    message dans une file KV et déclenche ce run, qui vide la file via le relais,
    répond, puis se termine. Pas de polling périodique → zéro minute gaspillée.

  • MODE POLLING (historique, sans relais) — appelé par cron : charge l'offset,
    récupère les messages via getUpdates, les traite, répond, persiste l'offset.

Dans les deux cas : routage (commande d'état / lecture / assistant Gemini),
réponse Telegram, mémoire conversationnelle. Le process se termine ensuite (pas
de serveur permanent) ; les modifs de state sont committées par le workflow.

Usage :
    python -m src.telegram_bot.bot
"""

from __future__ import annotations

import os

from src.state import report_memory as mem
from src.telegram_bot import assistant, commands, portfolio_edit, telegram_api
from src.telegram_bot.context_loader import load_full_context
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _route_message(text: str, context: dict, history: list) -> tuple[str, bool]:
    """Route un message vers le bon handler.

    Returns:
        Tuple ``(reponse, state_modifie)``.
    """
    # 0) Édition du portefeuille (/buy /sell /set ou langage naturel) — modifie
    #    config/portfolio.yaml, protégée par mot de passe. Placée en tête pour
    #    capter /buy /sell /set avant le routage générique des commandes.
    if portfolio_edit.is_edit_intent(text):
        return portfolio_edit.handle_edit(text)

    # 1) Commande d'état (modifie le state).
    if commands.is_state_command(text):
        return commands.handle_state_command(text)

    # 2) Commande de lecture (réponse directe, sans IA).
    if commands.is_command(text):
        read = commands.handle_read_command(text)
        if read is not None:
            return read, False
        # /ask « question » → on enlève le préfixe et on délègue à l'IA.
        cmd, args = commands.parse_command(text)
        if cmd == "/ask":
            question = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""
            if not question.strip():
                return ("Pose ta question après /ask, ex. "
                        "`/ask est-ce le bon moment pour renforcer ETH ?`"), False
            return assistant.answer(question, context, history), False
        # /recherche « question » → force la recherche web (Google grounding).
        if cmd in ("/recherche", "/research"):
            question = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""
            if not question.strip():
                return ("Pose ta question après /recherche, ex. "
                        "`/recherche dernières news ETF Ethereum`"), False
            return assistant.answer(question, context, history, use_search=True), False
        # Commande inconnue → aide.
        return commands.handle_read_command("/aide") or "Commande inconnue.", False

    # 3) Langage naturel → assistant Gemini.
    return assistant.answer(text, context, history), False


def _process_messages(messages: list[dict]) -> None:
    """Traite une liste de messages d'Omar : route, répond, mémorise.

    Partagé par le mode polling (run_once) et le mode relais (run_from_relay)
    pour garantir un comportement identique. Un message qui échoue n'interrompt
    jamais le batch.
    """
    # Contexte chargé une fois pour le batch (les rapports ne changent pas en
    # cours de run). L'historique est rechargé après chaque tour pour refléter
    # les messages précédents du même batch.
    context = load_full_context()

    for msg in messages:
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        history = mem.load_telegram_history(limit=12)
        try:
            reply, _state_modified = _route_message(text, context, history)
        except Exception as exc:  # noqa: BLE001 — un message ne doit pas tout casser
            logger.exception("Erreur de traitement du message : %s", exc)
            reply = ("Une erreur est survenue en traitant ta demande. "
                     "Réessaie ou tape /aide.")
        # Mémoire conversationnelle : on enregistre le tour (user + assistant).
        mem.append_telegram_turn("user", text)
        mem.append_telegram_turn("assistant", reply)
        # v21 — rendu HTML (gras, puces) : le Markdown legacy cassait l'affichage.
        telegram_api.send_message(reply, parse_mode="HTML")


def run_once() -> int:
    """Traite les messages en attente une fois, puis se termine (mode polling).

    Mode HISTORIQUE (cron getUpdates), conservé pour rétro-compatibilité quand
    aucun relais n'est configuré.

    Returns:
        Code de sortie (0 = OK même si aucun message ; 2 = non configuré).
    """
    if not telegram_api.bot_configured():
        logger.warning("Bot Telegram non configuré (TELEGRAM_BOT_TOKEN / "
                       "TELEGRAM_CHAT_ID absents). Rien à faire.")
        return 2

    allowed_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    offset = mem.load_telegram_offset()
    # offset persisté = dernier id traité + 1 ; 0 au premier run (tout récupérer).
    updates = telegram_api.get_updates(offset=offset or None, timeout=0)
    if not updates:
        logger.info("Aucun nouveau message Telegram.")
        return 0

    messages, max_update_id = telegram_api.extract_text_messages(updates, allowed_chat)

    # On avance TOUJOURS l'offset (même les messages ignorés/non autorisés), pour
    # ne pas boucler sur les mêmes updates au prochain run.
    if max_update_id is not None:
        mem.save_telegram_offset(max_update_id + 1)

    if not messages:
        logger.info("Updates reçus mais aucun message exploitable d'Omar.")
        return 0

    _process_messages(messages)
    logger.info("Bot Telegram : %d message(s) traité(s).", len(messages))
    return 0


def run_from_relay() -> int:
    """Traite les messages drainés depuis le relais Cloudflare (mode réveil).

    Mode v18.1 « réveil par message » : un Worker Cloudflare reçoit le webhook
    Telegram et déclenche ce run, qui vide la file KV du relais. Pas de polling,
    pas de minutes gaspillées : le run traite ce qui est en file puis se termine.

    Returns:
        Code de sortie (0 = OK même si aucun message ; 2 = non configuré).
    """
    if not telegram_api.bot_configured():
        logger.warning("Bot Telegram non configuré (TELEGRAM_BOT_TOKEN / "
                       "TELEGRAM_CHAT_ID absents). Rien à faire.")
        return 2

    allowed_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    updates = telegram_api.pull_relay_updates()
    if not updates:
        # File vide = run redondant (un run précédent a déjà tout drainé) ou
        # déclenchement manuel sans message. On sort immédiatement (aucun coût).
        logger.info("Relais : file vide, rien à traiter.")
        return 0

    messages, _max_update_id = telegram_api.extract_text_messages(updates, allowed_chat)
    if not messages:
        logger.info("Relais : updates reçus mais aucun message exploitable.")
        return 0

    _process_messages(messages)
    logger.info("Bot Telegram (relais) : %d message(s) traité(s).", len(messages))
    return 0


def main() -> int:
    try:
        # Si un relais est configuré, on draine sa file (mode réveil-par-message).
        # Sinon on garde le polling getUpdates historique (aucune régression).
        if telegram_api.relay_configured():
            return run_from_relay()
        return run_once()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Échec fatal du bot Telegram : %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
