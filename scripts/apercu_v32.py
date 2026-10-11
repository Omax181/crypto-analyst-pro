"""Aperçu V32 — les trois mails et les messages Telegram, sans RIEN envoyer.

À lancer dans le Codespace, après ``bash deploy_v32.sh`` (SANS ``--push``) et
avant le commit : collecte réelle (API), modèle réel (Gemini), rendu réel. Les
prompts V33 ne peuvent être jugés que sur une vraie réponse du modèle : c'est
l'occasion de lire ce qu'Omar recevra AVANT que la V32 parte en production.

Garanties :

* aucun mail ni message Telegram ne part (variables d'envoi retirées de
  l'environnement de CE processus, fonctions d'envoi remplacées) ;
* ``state/`` n'est jamais écrit : il est copié dans un dossier temporaire, et
  c'est cette copie que les trois rapports lisent et écrivent (le soir lit
  donc le matin de l'aperçu, l'hebdo la semaine) — l'empreinte de ``state/``
  est contrôlée à la fin ;
* seul le modèle de langage est appelé pour de vrai (≈ 4 appels Gemini).

Usage ::

    python3 scripts/apercu_v32.py              # matin, soir, hebdo
    python3 scripts/apercu_v32.py matin hebdo  # au choix

Sortie : ``apercu_v32/{matin,soir,hebdo}.html`` et ``.telegram.txt`` (dossier
ignoré par git). Les graphiques sont intégrés au HTML.
"""

from __future__ import annotations

import base64
import hashlib
import os
import pathlib
import shutil
import sys
import tempfile

RACINE = pathlib.Path(__file__).resolve().parents[1]
SORTIE = RACINE / "apercu_v32"
RAPPORTS = {"matin": "run_morning", "soir": "run_evening", "hebdo": "run_weekly"}
# Variables des canaux d'ENVOI : retirées de l'environnement de ce processus
# (et de lui seul) avant tout import du code des rapports.
_ENVOI = ("GMAIL_USER", "GMAIL_APP_PASSWORD", "RECIPIENT_EMAIL",
          "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "RELAY_PULL_URL",
          "RELAY_SECRET")


def empreinte(dossier: pathlib.Path) -> str:
    h = hashlib.md5()
    if dossier.is_dir():
        for p in sorted(dossier.rglob("*")):
            if p.is_file():
                h.update(str(p.relative_to(dossier)).encode())
                h.update(p.read_bytes())
    return h.hexdigest()


def integrer_images(html: str, images: dict) -> str:
    """``cid:nom`` → image intégrée, pour lire le HTML hors messagerie."""
    for nom, octets in (images or {}).items():
        if isinstance(octets, (bytes, bytearray)):
            uri = "data:image/png;base64," + base64.b64encode(octets).decode()
            html = html.replace(f"cid:{nom}", uri)
    return html


def main(argv: list[str]) -> int:
    choix = argv or list(RAPPORTS)
    inconnus = [c for c in choix if c not in RAPPORTS]
    if inconnus:
        print(f"✗ rapport inconnu : {', '.join(inconnus)} "
              f"(au choix : {', '.join(RAPPORTS)})")
        return 2
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        print("✗ GEMINI_API_KEY absente de l'environnement du Codespace.\n"
              "  Ajoute-la comme secret Codespaces du dépôt (Settings → Secrets →\n"
              "  Codespaces), puis rouvre le terminal. Ne la colle jamais dans un chat.")
        return 2
    for k in _ENVOI:
        os.environ.pop(k, None)

    os.chdir(RACINE)
    sys.path.insert(0, str(RACINE))
    etat_reel = RACINE / "state"
    avant = empreinte(etat_reel)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="apercu_v32_"))
    if etat_reel.is_dir():
        shutil.copytree(etat_reel, tmp / "state")

    from src.state import report_memory as mem
    mem._STATE_DIR = tmp / "state"            # toutes les lectures/écritures d'état
    import src.main as M

    capture: dict = {}

    def _mail(sujet, html, inline_images=None, **_kw):
        capture["sujet"], capture["html"] = sujet, html
        capture["images"] = inline_images or {}
        return True

    def _telegram(payload, kind):
        from src.telegram_bot import notify
        capture["telegram"] = notify._build_digest(payload or {}, kind)

    M.send_email = _mail
    M._push_telegram_notification = _telegram

    SORTIE.mkdir(exist_ok=True)
    print("Collecte réelle : compte plusieurs minutes par rapport (les API "
          "gratuites limitent le débit). Rien ne sera envoyé.", flush=True)
    code = 0
    try:
        for nom in choix:
            capture.clear()
            print(f"── {nom} …", flush=True)
            rc = getattr(M, RAPPORTS[nom])()
            html = integrer_images(capture.get("html", ""), capture.get("images"))
            (SORTIE / f"{nom}.html").write_text(html, encoding="utf-8")
            (SORTIE / f"{nom}.telegram.txt").write_text(
                capture.get("telegram", ""), encoding="utf-8")
            print(f"   rc={rc} · « {capture.get('sujet', '—')} » · "
                  f"{len(html) // 1024} Ko → apercu_v32/{nom}.html")
            if rc != 0 or not capture.get("html"):
                code = 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        apres = empreinte(etat_reel)
        if apres != avant:
            print("✗ state/ a changé pendant l'aperçu — à contrôler (git status state)")
            code = 1
        else:
            print("✓ state/ inchangé · aucun mail ni message Telegram envoyé")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
