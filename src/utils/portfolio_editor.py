"""Moteur d'édition CHIRURGICALE de config/portfolio.yaml (v21).

Édite le YAML par regex sur les lignes pour CONSERVER commentaires, tiers, notes
et mise en page. Fonctions PURES : prennent le texte, renvoient le nouveau texte
+ un résumé, et LÈVENT ``PortfolioEditError`` en cas de problème (jamais de print
ni de sys.exit). Utilisé par le CLI (scripts/update_portfolio.py) ET par le bot
Telegram (src/telegram_bot/portfolio_edit.py) → une seule implémentation.

PRU (coût moyen pondéré) recalculé automatiquement à l'achat :
    new_pru = (old_qty*old_pru + qty_achetée*prix) / (old_qty + qty_achetée)
La vente ne change pas le PRU (méthode du coût moyen).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

PORTFOLIO_PATH = Path(__file__).resolve().parents[2] / "config" / "portfolio.yaml"


class PortfolioEditError(ValueError):
    """Erreur d'édition du portefeuille (actif introuvable, vente excessive…)."""


# --------------------------------------------------------------------------- #
# Helpers bas niveau (regex sur le texte YAML)
# --------------------------------------------------------------------------- #
def _find_asset_block(text: str, asset: str) -> Optional[tuple[int, int]]:
    """Retourne (start, end) du bloc d'un actif (indentation 2 espaces)."""
    pattern = re.compile(rf"^  {re.escape(asset)}:\s*$", re.MULTILINE)
    m = pattern.search(text)
    if not m:
        return None
    rest = text[m.end():]
    m2 = re.compile(r"^  \S", re.MULTILINE).search(rest)
    end = m.end() + m2.start() if m2 else len(text)
    return m.start(), end


def _update_field(block: str, field: str, value: str) -> str:
    """Met à jour (ou insère) un champ YAML à 4 espaces dans un bloc d'actif."""
    pattern = re.compile(rf"^(    {field}:\s*).*$", re.MULTILINE)
    if pattern.search(block):
        return pattern.sub(rf"\g<1>{value}", block)
    lines = block.split("\n")
    lines.insert(1, f"    {field}: {value}")
    return "\n".join(lines)


def _get_field(block: str, field: str) -> Optional[str]:
    """Lit la valeur brute d'un champ (None si absent)."""
    m = re.search(rf"^    {field}:\s*(.+)$", block, re.MULTILINE)
    return m.group(1).strip() if m else None


def _format_qty(qty: float) -> str:
    """Entier si rond, sinon décimal sans zéros inutiles."""
    if qty == int(qty) and abs(qty) < 1e12:
        return str(int(qty))
    return f"{qty:.10f}".rstrip("0").rstrip(".")


def _format_price(p: float) -> str:
    """Prix lisible : assez de décimales pour les micro-caps, sans zéros inutiles."""
    s = f"{p:.10f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _to_float(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Opérations (texte -> texte)
# --------------------------------------------------------------------------- #
def apply_quantity_change(
    text: str, asset: str, action: str, qty: float,
    price: Optional[float] = None,
) -> tuple[str, dict[str, Any]]:
    """Applique buy / sell / set sur un actif EXISTANT.

    Args:
        text: contenu YAML actuel.
        asset: symbole (clé du portefeuille), insensible à la casse.
        action: 'buy' | 'sell' | 'set'.
        qty: quantité (> 0).
        price: prix unitaire USD (recalcule le PRU à l'achat ; pose le PRU sur
            'set' si fourni ; ignoré sur 'sell').

    Returns:
        (nouveau_texte, résumé) où résumé = {asset, action, old_qty, new_qty,
        old_pru, new_pru, price}.

    Raises:
        PortfolioEditError: actif introuvable, action inconnue, qty<=0, ou vente
            supérieure au solde.
    """
    asset = asset.upper()
    if action not in ("buy", "sell", "set"):
        raise PortfolioEditError(f"Action inconnue : {action}")
    if qty <= 0:
        raise PortfolioEditError("La quantité doit être strictement positive.")

    bounds = _find_asset_block(text, asset)
    if bounds is None:
        raise PortfolioEditError(
            f"Actif « {asset} » introuvable dans le portefeuille. "
            "Pour un nouvel actif, ajoute-le manuellement (tier + coingecko_id requis).")

    start, end = bounds
    block = text[start:end]
    old_qty = _to_float(_get_field(block, "quantity")) or 0.0
    old_pru = _to_float(_get_field(block, "pru"))

    new_pru = old_pru
    if action == "buy":
        new_qty = old_qty + qty
        if price is not None:
            if old_pru is None and old_qty > 0:
                # Audit final v26 — PRU du stock existant INCONNU : l'ancien
                # calcul le traitait comme GRATUIT (base_cost=0), produisant un
                # PRU minuscule → faux gains latents affichés. Le seul coût
                # CONNU est celui de cet achat : PRU = prix de l'achat
                # (conservateur, jamais un gain fabriqué).
                new_pru = price
            else:
                base_cost = (old_qty * old_pru) if old_pru is not None else 0.0
                new_pru = (base_cost + qty * price) / new_qty if new_qty else price
    elif action == "sell":
        if qty > old_qty + 1e-12:
            raise PortfolioEditError(
                f"Vente impossible : tu détiens {_format_qty(old_qty)} {asset}, "
                f"vente de {_format_qty(qty)} demandée.")
        new_qty = max(0.0, old_qty - qty)
        # PRU inchangé (méthode coût moyen).
    else:  # set
        new_qty = qty
        if price is not None:
            new_pru = price

    new_block = _update_field(block, "quantity", _format_qty(new_qty))
    if new_pru is not None and new_pru != old_pru:
        new_block = _update_field(new_block, "pru", _format_price(new_pru))
    # Rafraîchit la baseline value_usd si on connaît un prix (sinon on la laisse).
    if price is not None:
        new_block = _update_field(
            new_block, "value_usd", _format_price(round(new_qty * price, 2)))

    new_text = text[:start] + new_block + text[end:]
    return new_text, {
        "asset": asset, "action": action,
        "old_qty": old_qty, "new_qty": new_qty,
        "old_pru": old_pru, "new_pru": new_pru, "price": price,
    }


# --------------------------------------------------------------------------- #
# I/O fichier (le bot et le CLI passent par là)
# --------------------------------------------------------------------------- #
def read_portfolio_text(path: Path = PORTFOLIO_PATH) -> str:
    return path.read_text(encoding="utf-8")


def write_portfolio_text(text: str, path: Path = PORTFOLIO_PATH) -> None:
    path.write_text(text, encoding="utf-8")
