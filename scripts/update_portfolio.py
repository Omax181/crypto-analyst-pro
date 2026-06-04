#!/usr/bin/env python3
"""Met à jour config/portfolio.yaml après un achat / vente / ajustement.

Usage :
    python scripts/update_portfolio.py --asset BTC --action buy  --quantity 0.01
    python scripts/update_portfolio.py --asset SOL --action sell --quantity 5
    python scripts/update_portfolio.py --asset BTC --action set  --quantity 0.015225
    python scripts/update_portfolio.py --asset PEPE --action add  --quantity 1000000 \
        --tier 4 --coingecko-id pepe --notes "meme coin"

Actions :
    buy   → ajoute la quantité à l'existant
    sell  → soustrait la quantité (erreur si insuffisant)
    set   → remplace la quantité par la valeur donnée
    add   → crée un nouvel actif (--tier et --coingecko-id requis)

Le fichier YAML est modifié « chirurgicalement » (regex sur les lignes) pour
CONSERVER les commentaires, la mise en page et les notes existantes.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO_PATH = ROOT / "config" / "portfolio.yaml"
SOURCES_PATH = ROOT / "config" / "sources.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_yaml_raw(path: Path) -> str:
    """Charge le fichier YAML brut (texte)."""
    return path.read_text(encoding="utf-8")


def _save_yaml_raw(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _find_asset_block(text: str, asset: str) -> tuple[int, int] | None:
    """Retourne (start, end) du bloc d'un actif dans le YAML.

    Le bloc va de la ligne ``  ASSET:`` jusqu'à la prochaine ligne de même
    indentation (c.-à-d. le prochain actif, commentaire de section, ou EOF).
    """
    # Le pattern matche un actif à 2 espaces d'indentation (enfant de portfolio:)
    pattern = re.compile(rf"^  {re.escape(asset)}:\s*$", re.MULTILINE)
    m = pattern.search(text)
    if not m:
        return None
    start = m.start()
    # Cherche la fin du bloc : la prochaine ligne qui commence à indentation ≤ 2
    # (prochain actif "  XXX:", commentaire de section "  # ===", ou EOF).
    rest = text[m.end():]
    end_pattern = re.compile(r"^  \S", re.MULTILINE)
    m2 = end_pattern.search(rest)
    end = m.end() + m2.start() if m2 else len(text)
    return start, end


def _update_field(block: str, field: str, value: str) -> str:
    """Met à jour un champ YAML dans un bloc d'actif (ex. quantity: 1.5)."""
    pattern = re.compile(rf"^(    {field}:\s*).*$", re.MULTILINE)
    if pattern.search(block):
        return pattern.sub(rf"\g<1>{value}", block)
    # Le champ n'existe pas dans le bloc → on l'ajoute après la première ligne.
    lines = block.split("\n")
    lines.insert(1, f"    {field}: {value}")
    return "\n".join(lines)


def _get_field(block: str, field: str) -> str | None:
    """Lit la valeur brute d'un champ YAML dans un bloc."""
    m = re.search(rf"^    {field}:\s*(.+)$", block, re.MULTILINE)
    return m.group(1).strip() if m else None


def _format_qty(qty: float) -> str:
    """Formate une quantité : entier si rond, sinon décimal sans zéros inutiles."""
    if qty == int(qty) and qty < 1e12:
        return str(int(qty))
    # Jusqu'à 10 décimales utiles, on retire les zéros de fin.
    return f"{qty:.10f}".rstrip("0").rstrip(".")


def _tier_comment(tier: int) -> str:
    return {
        1: "  # === TIER 1 (analyse deep) ===",
        2: "  # === TIER 2 (analyse condensee) ===",
        3: "  # === TIER 3 (silence sauf mouvement >10%) ===",
        4: "  # === TIER 4 (poussieres) ===",
    }.get(tier, "")


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def action_buy_sell_set(text: str, asset: str, action: str, qty: float) -> str:
    bounds = _find_asset_block(text, asset)
    if bounds is None:
        print(f"❌ Actif '{asset}' introuvable dans portfolio.yaml.")
        print("   Pour ajouter un nouvel actif, utilisez --action add.")
        sys.exit(1)

    start, end = bounds
    block = text[start:end]
    current_str = _get_field(block, "quantity")
    current = float(current_str) if current_str else 0.0

    if action == "buy":
        new_qty = current + qty
        verb = f"acheté +{_format_qty(qty)}"
    elif action == "sell":
        if qty > current + 1e-12:
            print(f"❌ Quantité insuffisante : vous avez {_format_qty(current)} {asset},"
                  f" tentative de vente de {_format_qty(qty)}.")
            sys.exit(1)
        new_qty = max(0.0, current - qty)
        verb = f"vendu -{_format_qty(qty)}"
    else:  # set
        new_qty = qty
        verb = f"positionné à {_format_qty(qty)}"

    new_block = _update_field(block, "quantity", _format_qty(new_qty))
    result = text[:start] + new_block + text[end:]

    print(f"✅ {asset} : {verb}")
    print(f"   {_format_qty(current)} → {_format_qty(new_qty)}")
    return result


def action_add(text: str, asset: str, qty: float, tier: int,
               coingecko_id: str | None, notes: str | None) -> str:
    if _find_asset_block(text, asset) is not None:
        print(f"❌ '{asset}' existe déjà. Utilisez --action buy/sell/set.")
        sys.exit(1)

    # Construit le nouveau bloc YAML.
    lines = [f"  {asset}:"]
    lines.append(f"    quantity: {_format_qty(qty)}")
    lines.append(f"    value_usd: 0")
    lines.append(f"    tier: {tier}")
    if notes:
        lines.append(f'    notes: "{notes}"')
    new_block = "\n".join(lines) + "\n"

    # Insertion : juste avant le commentaire du tier suivant, ou en fin de fichier.
    tier_comment = _tier_comment(tier)
    next_tier_comment = _tier_comment(tier + 1)

    if next_tier_comment and next_tier_comment in text:
        # On insère juste avant le commentaire du tier suivant.
        idx = text.index(next_tier_comment)
        result = text[:idx] + new_block + text[idx:]
    elif tier_comment and tier_comment in text:
        # Pas de tier suivant → on ajoute en fin de fichier (avant le dernier \n).
        result = text.rstrip("\n") + "\n" + new_block
    else:
        result = text.rstrip("\n") + "\n" + new_block

    # Ajoute le coingecko_id dans sources.yaml si fourni.
    if coingecko_id:
        _add_coingecko_id(asset, coingecko_id)

    print(f"✅ Nouvel actif {asset} ajouté (tier {tier}, qté {_format_qty(qty)}).")
    if coingecko_id:
        print(f"   CoinGecko ID '{coingecko_id}' ajouté dans sources.yaml.")
    else:
        print(f"   ⚠️  Pensez à ajouter le coingecko_id dans config/sources.yaml"
              f" (sinon le prix ne sera jamais récupéré).")
    return result


def _add_coingecko_id(asset: str, cg_id: str) -> None:
    """Ajoute une entrée dans la section coingecko_ids de sources.yaml."""
    src = _load_yaml_raw(SOURCES_PATH)
    # Vérifie que l'asset n'est pas déjà dans la section.
    if re.search(rf"^\s+{re.escape(asset)}:", src, re.MULTILINE):
        return
    # On insère après la dernière entrée de coingecko_ids (avant la ligne vide
    # ou le prochain bloc de section).
    pattern = re.compile(
        r"(coingecko_ids:\n(?:  \w+:.*\n)*)", re.MULTILINE
    )
    m = pattern.search(src)
    if m:
        insertion = f"  {asset}: {cg_id}\n"
        new_src = src[:m.end()] + insertion + src[m.end():]
        _save_yaml_raw(SOURCES_PATH, new_src)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Met à jour le portfolio après un trade."
    )
    parser.add_argument("--asset", required=True,
                        help="Symbole (ex. BTC, SOL, PEPE)")
    parser.add_argument("--action", required=True,
                        choices=["buy", "sell", "set", "add"],
                        help="buy=ajouter, sell=retirer, set=fixer, add=nouvel actif")
    parser.add_argument("--quantity", required=True, type=float,
                        help="Quantité (positif)")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3, 4], default=3,
                        help="Tier pour un nouvel actif (défaut: 3)")
    parser.add_argument("--coingecko-id",
                        help="ID CoinGecko pour un nouvel actif (ex. solana)")
    parser.add_argument("--notes",
                        help="Note libre (ex. 'meme coin')")

    args = parser.parse_args()
    asset = args.asset.upper()
    qty = args.quantity

    if qty <= 0:
        print("❌ La quantité doit être strictement positive.")
        sys.exit(1)

    text = _load_yaml_raw(PORTFOLIO_PATH)

    if args.action == "add":
        if not args.coingecko_id:
            print("❌ --coingecko-id est requis pour ajouter un nouvel actif.")
            print("   Trouvez-le sur https://www.coingecko.com (URL slug).")
            sys.exit(1)
        text = action_add(text, asset, qty, args.tier, args.coingecko_id, args.notes)
    else:
        text = action_buy_sell_set(text, asset, args.action, qty)

    _save_yaml_raw(PORTFOLIO_PATH, text)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"   Fichier portfolio.yaml mis à jour ({now}).")


if __name__ == "__main__":
    main()
