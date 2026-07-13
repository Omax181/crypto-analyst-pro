"""Édition du portefeuille depuis le bot Telegram (v21).

Permet à Omar de mettre à jour ses positions par message :
  • commandes : « /buy ETH 0.1 1600 <mdp> », « /sell TAO 0.5 <mdp> »,
    « /set BTC 0.015 <mdp> » ;
  • langage naturel : « j'ai acheté 0,1 ETH à 1600 <mdp> », « vendu 0.5 TAO <mdp> ».

Garde-fous :
  • MOT DE PASSE requis pour ÉCRIRE (anti-fausse-manip / anti-accès au tel). Sans
    mot de passe → simple APERÇU (avant → après), aucune écriture.
  • Moteur d'écriture DÉTERMINISTE (portfolio_editor) : pas d'IA, conserve
    commentaires/tiers/notes, refuse une vente > solde, recalcule le PRU à l'achat.
  • Une question (contenant « ? ») n'est JAMAIS interprétée comme un ordre.
  • Seuls les actifs DÉJÀ présents au portefeuille sont éditables (ajout d'un
    nouvel actif = manuel, car tier + coingecko_id nécessaires au prix live).

La vraie barrière d'accès reste le TELEGRAM_CHAT_ID (le bot ne parle qu'à Omar) ;
le mot de passe est une sécurité supplémentaire.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

from src.utils import portfolio_editor
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Mot de passe d'édition = secret GitHub PORTFOLIO_EDIT_PASSWORD, OBLIGATOIRE.
# v29 (audit sécurité) : l'ancien défaut en dur (le pseudo GitHub d'Omar —
# devinable et versionné dans le repo) est SUPPRIMÉ. Secret absent ou vide →
# l'écriture est désactivée et le bot explique quoi configurer ; les APERÇUS
# (sans mot de passe) restent disponibles.
EDIT_PASSWORD = (os.environ.get("PORTFOLIO_EDIT_PASSWORD") or "").strip()

_NUM_RE = re.compile(r"^[-+]?\d+(?:[.,]\d+)?$")
_PRICE_MARKERS = {"à", "a", "@", "at", "prix", "=>", "->", "="}
# Verbes volontairement RESTREINTS : on évite « renforce / allège / sors / mets »
# (vocabulaire d'analyse) qui détourneraient des questions en ordres. Les
# commandes /buy /sell /set restent la voie principale, sans ambiguïté.
_BUY_WORDS = {"/buy", "buy", "acheté", "achete", "achète", "achat", "acheter",
              "ajoute", "ajouter"}
_SELL_WORDS = {"/sell", "sell", "vendu", "vente", "vendre", "vends"}
_SET_WORDS = {"/set", "set", "fixe", "fixer", "positionne", "positionner"}


def _clean(tok: str) -> str:
    return tok.lower().strip(".,;:!()")


def _portfolio_keys() -> set[str]:
    """Symboles éditables = clés du portefeuille (en majuscules)."""
    try:
        from src.utils.portfolio_loader import load_portfolio
        return {str(k).upper() for k in (load_portfolio().get("portfolio") or {})}
    except Exception as exc:  # noqa: BLE001
        logger.info("Portefeuille illisible pour l'édition : %s", exc)
        return set()


def parse_edit(text: str) -> Optional[dict[str, Any]]:
    """Analyse un message ; renvoie {action, asset, qty, price, has_password} ou None.

    None si ce n'est pas une instruction d'édition exploitable (pas de verbe,
    pas d'actif connu, pas de quantité, ou c'est une question).
    """
    if not text or "?" in text:
        return None
    tokens = text.split()
    low = [_clean(t) for t in tokens]

    action = None
    for t in low:
        if t in _BUY_WORDS:
            action = "buy"; break
        if t in _SELL_WORDS:
            action = "sell"; break
        if t in _SET_WORDS:
            action = "set"; break
    if not action:
        return None

    has_password = bool(EDIT_PASSWORD) and EDIT_PASSWORD in tokens

    keys = _portfolio_keys()
    asset = None
    for t in tokens:
        cand = t.upper().strip(".,;:!()")
        if cand in keys:
            asset = cand
            break
    if not asset:
        return None

    # Nombres (hors mot de passe). Détection prix via marqueur (« à », « @ »…).
    numeric = [(i, float(t.replace(",", ".")))
               for i, t in enumerate(tokens)
               if t != EDIT_PASSWORD and _NUM_RE.match(t.replace(",", "."))]
    if not numeric:
        return None

    price = None
    marker_idx = next((i for i, t in enumerate(low) if t in _PRICE_MARKERS), None)
    if marker_idx is not None:
        price = next((v for j, v in numeric if j > marker_idx), None)
    if price is not None:
        qty = next((v for _, v in numeric if v != price), numeric[0][1])
    else:
        qty = numeric[0][1]
        price = numeric[1][1] if len(numeric) > 1 else None

    if qty is None or qty <= 0:
        return None
    return {"action": action, "asset": asset, "qty": qty,
            "price": price, "has_password": has_password}


def is_edit_intent(text: str) -> bool:
    """True si le message est une instruction d'édition de portefeuille."""
    return parse_edit(text) is not None


def _fq(v: Optional[float]) -> str:
    return portfolio_editor._format_qty(v) if v is not None else "—"


def _fp(v: Optional[float]) -> str:
    return f"${portfolio_editor._format_price(v)}" if v is not None else "—"


def handle_edit(text: str) -> tuple[str, bool]:
    """Traite une instruction d'édition. Returns (réponse, state_modifié)."""
    intent = parse_edit(text)
    if not intent:
        return ("Je n'ai pas compris l'opération. Exemples :\n"
                "• `/buy ETH 0.1 1600 <mot de passe>`\n"
                "• `/sell TAO 0.5 <mot de passe>`\n"
                "• « j'ai acheté 0,1 ETH à 1600 <mot de passe> »", False)

    asset, action, qty, price = (intent["asset"], intent["action"],
                                 intent["qty"], intent["price"])
    try:
        current = portfolio_editor.read_portfolio_text()
        new_text, summ = portfolio_editor.apply_quantity_change(
            current, asset, action, qty, price)
    except portfolio_editor.PortfolioEditError as exc:
        return (f"❌ {exc}", False)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Édition portefeuille échouée : %s", exc)
        return ("❌ Erreur interne pendant l'édition du portefeuille.", False)

    verb = {"buy": "Achat", "sell": "Vente", "set": "Set"}[action]
    head = f"{verb} {_fq(qty)} {asset}" + (f" @ {_fp(price)}" if price is not None else "")
    change = f"Quantité : {_fq(summ['old_qty'])} → {_fq(summ['new_qty'])}"
    if action == "buy" and price is None:
        pru_line = "PRU : inchangé ⚠ (prix d'achat non fourni)"
    elif summ.get("new_pru") is not None and summ.get("new_pru") != summ.get("old_pru"):
        pru_line = f"PRU : {_fp(summ.get('old_pru'))} → {_fp(summ.get('new_pru'))}"
    else:
        pru_line = f"PRU : {_fp(summ.get('old_pru'))} (inchangé)"
    body = f"*{head}*\n{change}\n{pru_line}"

    if not EDIT_PASSWORD:
        # v29 (audit sécurité) — pas de secret configuré = écriture désactivée.
        return (f"🔒 Aperçu (NON appliqué) :\n{body}\n\n"
                "⚠ L'édition est désactivée : configure le secret GitHub "
                "`PORTFOLIO_EDIT_PASSWORD` (Settings → Secrets → Actions) "
                "pour pouvoir confirmer les modifications.", False)

    if not intent["has_password"]:
        return (f"🔒 Aperçu (NON appliqué) :\n{body}\n\n"
                "Renvoie la même instruction *avec ton mot de passe* pour confirmer.",
                False)

    try:
        portfolio_editor.write_portfolio_text(new_text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Écriture portefeuille échouée : %s", exc)
        return ("❌ Échec de l'écriture du portefeuille.", False)

    # Mémoire durable (déterministe) : trace la décision pour la continuité.
    try:
        from src.state import report_memory as mem
        memo = (f"{verb} {_fq(qty)} {asset}"
                + (f" @ {_fp(price)}" if price is not None else "")
                + f" — qté {_fq(summ['old_qty'])}→{_fq(summ['new_qty'])}")
        if summ.get("new_pru") is not None and summ.get("new_pru") != summ.get("old_pru"):
            memo += f", PRU {_fp(summ.get('old_pru'))}→{_fp(summ.get('new_pru'))}"
        mem.append_bot_memory("decision", memo)
    except Exception as exc:  # noqa: BLE001 — la mémoire ne doit jamais bloquer l'édition
        logger.info("Mémoire décision non enregistrée : %s", exc)

    return (f"✅ Portefeuille mis à jour :\n{body}", True)
