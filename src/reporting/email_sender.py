"""Envoi d'email via SMTP Gmail (app password).

Utilise STARTTLS sur smtp.gmail.com:587. Les identifiants viennent des
variables d'environnement ``GMAIL_USER`` / ``GMAIL_APP_PASSWORD`` /
``RECIPIENT_EMAIL``.
"""

from __future__ import annotations

import os
import smtplib
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 587


def send_email(
    subject: str,
    html_body: str,
    inline_images: Optional[dict[str, bytes]] = None,
) -> bool:
    """Envoie un email HTML, avec images inline optionnelles (CID).

    Args:
        subject: objet de l'email.
        html_body: corps HTML complet.
        inline_images: dict ``{cid: png_bytes}``. Chaque image est attachée en
            ``Content-ID: <cid>`` et référençable via ``<img src="cid:cid">``.
            v20 (audit C1) : Gmail SUPPRIME les images data-URI/SVG inline — les
            graphiques doivent donc passer par des pièces jointes CID pour être
            visibles.

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
        # Media query mobile (additive) : sous 600px, élargit le conteneur,
        # agrandit les polices et empile les grilles. À ≥600px : aucun effet,
        # le rendu desktop reste rigoureusement identique.
        mobile_style = (
            "<style>"
            "@media only screen and (max-width:600px){"
            ".email-container{width:100%!important;max-width:100%!important;"
            "padding:16px!important;border-radius:0!important;}"
            "body{padding:0!important;}"
            ".email-container,.email-container p,.email-container div,"
            ".email-container span,.email-container td{font-size:13px!important;}"
            ".email-container h1{font-size:20px!important;}"
            ".email-container h2{font-size:16px!important;}"
            ".email-container [style*=\"display:grid\"],"
            ".email-container [style*=\"display: grid\"],"
            ".email-container [style*=\"display:flex\"],"
            ".email-container [style*=\"display: flex\"]{display:block!important;}"
            ".email-container [style*=\"grid-template-columns\"]>*{"
            "width:100%!important;display:block!important;margin-bottom:6px!important;}"
            "}"
            "</style>"
        )
        wrapped = (
            "<!DOCTYPE html><html><head>"
            '<meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>" + subject + "</title>"
            + mobile_style +
            "</head><body style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:#faf9f5;margin:0;padding:24px;color:#1a1a18;\">"
            '<div class="email-container" style="max-width:680px;margin:0 auto;background:#ffffff;border-radius:16px;padding:32px;">'
            + html_body +
            "</div></body></html>"
        )
    else:
        wrapped = html_body

    # Corps : texte + HTML dans une partie « alternative ». S'il y a des images
    # inline, on l'enveloppe dans une partie « related » (HTML + images CID).
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText("Ton client mail ne supporte pas le HTML.", "plain", "utf-8"))
    alt.attach(MIMEText(wrapped, "html", "utf-8"))

    if inline_images:
        msg = MIMEMultipart("related")
        msg.attach(alt)
        for cid, png in inline_images.items():
            if not png:
                continue
            img = MIMEImage(png, _subtype="png")
            img.add_header("Content-ID", f"<{cid}>")
            img.add_header("Content-Disposition", "inline", filename=f"{cid}.png")
            msg.attach(img)
    else:
        msg = alt
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = recipient

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
