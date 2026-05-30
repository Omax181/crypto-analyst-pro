"""Envoi d'email via SMTP Gmail (app password).

Utilise STARTTLS sur smtp.gmail.com:587. Les identifiants viennent des
variables d'environnement ``GMAIL_USER`` / ``GMAIL_APP_PASSWORD`` /
``RECIPIENT_EMAIL``.
"""

from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.utils.logger import get_logger

logger = get_logger(__name__)

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 587


def send_email(subject: str, html_body: str) -> bool:
    """Envoie un email HTML.

    Args:
        subject: objet de l'email.
        html_body: corps HTML complet.

    Returns:
        ``True`` si l'envoi a réussi, ``False`` sinon (ne lève pas).
    """
    user = os.environ.get("GMAIL_USER", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    recipient = os.environ.get("RECIPIENT_EMAIL", "").strip() or user

    if not user or not password:
        logger.error("GMAIL_USER / GMAIL_APP_PASSWORD manquants : email non envoyé.")
        return False

    # Wrapper HTML complet pour maximum de compat (Outlook desktop est strict).
    # Si le corps contient déjà <html, on ne wrappe pas.
    if "<html" not in html_body.lower()[:200]:
        wrapped = (
            "<!DOCTYPE html><html><head>"
            '<meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>" + subject + "</title>"
            "</head><body style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:#faf9f5;margin:0;padding:24px;color:#1a1a18;\">"
            '<div style="max-width:680px;margin:0 auto;background:#ffffff;border-radius:16px;padding:32px;">'
            + html_body +
            "</div></body></html>"
        )
    else:
        wrapped = html_body

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = recipient
    # Fallback texte minimal pour les clients sans HTML.
    msg.attach(MIMEText("Ton client mail ne supporte pas le HTML.", "plain", "utf-8"))
    msg.attach(MIMEText(wrapped, "html", "utf-8"))

    try:
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(user, [recipient], msg.as_string())
        logger.info("Email envoyé à %s : %s", recipient, subject)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.exception("Échec d'envoi email : %s", exc)
        return False
