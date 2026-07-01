"""Client bas niveau de l'API Telegram Bot (Chantier G).

Utilise directement l'API HTTP Telegram Bot (ce que python-telegram-bot encapsule)
en mode POLLING, idéal pour un cron GitHub Actions de 5 min sans serveur ni
boucle d'événements permanente : à chaque run on récupère les messages en
attente via getUpdates, on les traite, on répond, et le process se termine.

L'offset (dernier update_id traité + 1) est persisté dans le state pour ne JAMAIS
retraiter un message déjà vu d'un run à l'autre.

Sécurité : seul le chat_id d'Omar (TELEGRAM_CHAT_ID) est servi ; tout autre
expéditeur est ignoré silencieusement (exigence de l'audit).
"""

from __future__ import annotations

import os
from typing import Any, Optional

from src.data_sources.http import get_json, post_json
from src.telegram_bot.formatting import strip_html, to_telegram_html
from src.utils.logger import get_logger

logger = get_logger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/{method}"
# Telegram tronque les messages à 4096 caractères : on découpe au besoin.
_TELEGRAM_MAX_CHARS = 4096


def _token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def bot_configured() -> bool:
    """Le bot est-il configuré (token + chat_id présents) ?"""
    return bool(_token()) and bool(os.environ.get("TELEGRAM_CHAT_ID", "").strip())


def _url(method: str) -> str:
    return _API_BASE.format(token=_token(), method=method)


def get_updates(offset: Optional[int] = None, timeout: int = 0,
                limit: int = 20) -> list[dict[str, Any]]:
    """Récupère les nouveaux messages via getUpdates (long-poll court).

    Args:
        offset: update_id minimal à renvoyer (= dernier traité + 1).
        timeout: timeout de long-poll côté Telegram (0 = instantané, adapté cron).
        limit: nombre maximum d'updates récupérés par appel (1-100). Borne le
            travail par run pour rester sous le timeout du workflow.

    Returns:
        Liste d'updates Telegram (vide en cas d'échec ou d'absence de message).
    """
    if not _token():
        return []
    params: dict[str, Any] = {
        "timeout": timeout,
        "allowed_updates": '["message"]',
        "limit": max(1, min(limit, 100)),
    }
    if offset is not None:
        params["offset"] = offset
    # Le timeout HTTP doit dépasser le long-poll Telegram pour éviter une coupure.
    raw = get_json(_url("getUpdates"), params=params, timeout=max(timeout + 10, 15))
    if not isinstance(raw, dict) or not raw.get("ok"):
        if isinstance(raw, dict):
            logger.warning("getUpdates KO : %s", raw.get("description"))
        return []
    result = raw.get("result")
    return result if isinstance(result, list) else []


def relay_configured() -> bool:
    """Un relais Cloudflare est-il configuré (mode réveil-par-message) ?

    Si ``RELAY_PULL_URL`` est présent, le bot ne fait plus de polling getUpdates
    (incompatible avec le webhook) : il draine la file d'attente du relais. Sinon
    il garde le comportement historique (getUpdates), donc aucune régression.
    """
    return bool(os.environ.get("RELAY_PULL_URL", "").strip())


def pull_relay_updates() -> list[dict[str, Any]]:
    """Draine la file d'attente des updates depuis le relais Cloudflare.

    Le Worker reçoit le webhook Telegram, empile chaque update dans une file KV,
    puis déclenche ce run. On vide ici TOUTE la file en un appel : aucun message
    n'est perdu même si plusieurs sont arrivés pendant qu'un run tournait déjà
    (la concurrency GitHub sérialise, la file KV conserve). Dégradation gracieuse
    totale : toute erreur (relais down, secret invalide) → liste vide → le run se
    termine proprement sans rien traiter.

    Returns:
        Liste d'updates Telegram (même forme que getUpdates), vide si rien/échec.
    """
    url = os.environ.get("RELAY_PULL_URL", "").strip()
    if not url:
        return []
    secret = os.environ.get("RELAY_SECRET", "").strip()
    headers = {"Authorization": f"Bearer {secret}"} if secret else None
    raw = get_json(url, headers=headers, timeout=20)
    # Le Worker renvoie soit la liste directement, soit {"updates": [...]}.
    if isinstance(raw, dict):
        raw = raw.get("updates")
    return raw if isinstance(raw, list) else []


def send_message(
    text: str,
    chat_id: Optional[str] = None,
    *,
    parse_mode: Optional[str] = "Markdown",
    disable_preview: bool = True,
) -> bool:
    """Envoie un message Telegram (découpé si > 4096 caractères).

    Args:
        text: contenu du message.
        chat_id: destinataire (par défaut TELEGRAM_CHAT_ID, celui d'Omar).
        parse_mode: 'Markdown' (défaut) ou None pour du texte brut.
        disable_preview: désactive les aperçus de liens.

    Returns:
        True si au moins un envoi a réussi.
    """
    if not _token():
        logger.info("send_message ignoré : TELEGRAM_BOT_TOKEN absent.")
        return False
    target = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not target:
        logger.info("send_message ignoré : aucun chat_id.")
        return False

    # On découpe le texte BRUT (≤4096) puis on formate chaque morceau : ainsi les
    # balises HTML ne sont jamais coupées en deux entre deux messages.
    chunks = _split_message(text)
    ok_any = False
    for raw_chunk in chunks:
        chunk = to_telegram_html(raw_chunk) if parse_mode == "HTML" else raw_chunk
        body: dict[str, Any] = {
            "chat_id": target,
            "text": chunk,
            "disable_web_page_preview": disable_preview,
        }
        if parse_mode:
            body["parse_mode"] = parse_mode
        resp = post_json(_url("sendMessage"), json_body=body)
        if isinstance(resp, dict) and resp.get("ok"):
            ok_any = True
            continue
        # Échec de formatage (HTML/Markdown invalide) → repli TEXTE PROPRE : on
        # retire les balises HTML (pas de <b> affiché en clair) et on renvoie sans
        # parse_mode. Garantit qu'Omar reçoit toujours un message lisible.
        if parse_mode:
            plain = strip_html(chunk) if parse_mode == "HTML" else raw_chunk
            body["text"] = plain
            body.pop("parse_mode", None)
            resp2 = post_json(_url("sendMessage"), json_body=body)
            if isinstance(resp2, dict) and resp2.get("ok"):
                ok_any = True
                continue
        if isinstance(resp, dict):
            logger.warning("sendMessage KO : %s", resp.get("description"))
    return ok_any


def _split_message(text: str) -> list[str]:
    """Découpe un texte en morceaux ≤ 4096 caractères, en respectant les lignes."""
    if len(text) <= _TELEGRAM_MAX_CHARS:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        # Ligne seule trop longue : on la coupe durement.
        if len(line) > _TELEGRAM_MAX_CHARS:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(line), _TELEGRAM_MAX_CHARS):
                chunks.append(line[i:i + _TELEGRAM_MAX_CHARS])
            continue
        if len(current) + len(line) + 1 > _TELEGRAM_MAX_CHARS:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def extract_text_messages(
    updates: list[dict[str, Any]],
    allowed_chat_id: str,
) -> tuple[list[dict[str, Any]], Optional[int]]:
    """Filtre les updates : ne garde que les messages texte du chat autorisé.

    Args:
        updates: updates bruts de getUpdates.
        allowed_chat_id: seul chat_id servi (celui d'Omar).

    Returns:
        Tuple ``(messages, max_update_id)`` où messages est une liste
        ``[{text, chat_id, message_id, date}]`` et max_update_id sert à avancer
        l'offset (même les messages ignorés font avancer l'offset).
    """
    messages: list[dict[str, Any]] = []
    max_update_id: Optional[int] = None
    for up in updates:
        uid = up.get("update_id")
        if isinstance(uid, int):
            max_update_id = uid if max_update_id is None else max(max_update_id, uid)
        msg = up.get("message") or {}
        text = msg.get("text")
        chat = (msg.get("chat") or {}).get("id")
        if not text or chat is None:
            continue
        # Sécurité : on ne sert QUE le chat_id d'Omar.
        if str(chat) != str(allowed_chat_id):
            logger.info("Message ignoré d'un chat non autorisé (%s).", chat)
            continue
        messages.append({
            "text": text,
            "chat_id": str(chat),
            "message_id": msg.get("message_id"),
            "date": msg.get("date"),
        })
    return messages, max_update_id
