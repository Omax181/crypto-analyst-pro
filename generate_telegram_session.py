"""Génère un TELEGRAM_SESSION_STRING (à exécuter UNE FOIS en local).

Prérequis :
  1. Créer une app sur https://my.telegram.org/auth -> récupérer api_id + api_hash.
  2. pip install telethon
  3. python generate_telegram_session.py

Le script demande api_id / api_hash, envoie un code sur ton Telegram, puis
affiche la session string à copier dans le secret GitHub TELEGRAM_SESSION_STRING.

⚠️ La session string donne accès à ton compte Telegram : ne la partage jamais
et ne la commite pas. Stocke-la uniquement dans les secrets GitHub.
"""

from __future__ import annotations


def main() -> None:
    try:
        from telethon.sessions import StringSession
        from telethon.sync import TelegramClient
    except ImportError:
        print("Telethon n'est pas installé. Lance : pip install telethon")
        return

    print("=== Génération de la session Telegram ===")
    api_id = input("api_id (nombre, depuis my.telegram.org) : ").strip()
    api_hash = input("api_hash : ").strip()
    if not api_id.isdigit() or not api_hash:
        print("api_id doit être un nombre et api_hash non vide. Abandon.")
        return

    with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        session = client.session.save()
        print("\n================= SESSION STRING =================")
        print(session)
        print("=================================================")
        print(
            "\nCopie cette chaîne dans le secret GitHub TELEGRAM_SESSION_STRING.\n"
            "Ajoute aussi TELEGRAM_API_ID et TELEGRAM_API_HASH."
        )


if __name__ == "__main__":
    main()
