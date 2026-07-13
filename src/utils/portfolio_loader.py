"""Chargement des configs YAML et mise à jour du portfolio.

Expose :
- ``load_portfolio()`` / ``load_config(name)`` : lecture des YAML de ``config/``.
- Une CLI ``--update`` (bonus) qui parse une instruction en langage naturel
  via Gemini et réécrit ``portfolio.yaml``.

Usage CLI :
    python -m src.utils.portfolio_loader --update "vendu 100 RNDR, acheté 50 USDC"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

from src.utils.logger import get_logger

logger = get_logger(__name__)

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def _config_path(name: str) -> Path:
    """Retourne le chemin d'un fichier de config par son nom de base."""
    if not name.endswith((".yaml", ".yml")):
        name += ".yaml"
    return CONFIG_DIR / name


def load_config(name: str) -> dict[str, Any]:
    """Charge un fichier YAML de ``config/`` en dictionnaire.

    Args:
        name: nom du fichier (avec ou sans extension), ex. ``"sources"``.

    Returns:
        Le contenu parsé.

    Raises:
        FileNotFoundError: si le fichier n'existe pas.
    """
    path = _config_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Config introuvable : {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_portfolio() -> dict[str, Any]:
    """Charge ``portfolio.yaml`` et valide la structure minimale.

    Returns:
        Dict avec clés ``meta`` et ``portfolio``.

    Raises:
        ValueError: si la structure est invalide.
    """
    data = load_config("portfolio")
    if "portfolio" not in data or not isinstance(data["portfolio"], dict):
        raise ValueError("portfolio.yaml : clé 'portfolio' manquante ou invalide.")
    for symbol, info in data["portfolio"].items():
        if "tier" not in info:
            raise ValueError(f"{symbol} : champ 'tier' manquant.")
        if info["tier"] not in (1, 2, 3, 4):
            raise ValueError(f"{symbol} : tier invalide ({info['tier']}).")
    data.setdefault("meta", {})
    return data


def total_value_usd(portfolio: dict[str, Any]) -> float:
    """Somme des ``value_usd`` du portfolio."""
    return float(
        sum(float(v.get("value_usd", 0.0)) for v in portfolio.get("portfolio", {}).values())
    )


# --------------------------------------------------------------------------- #
# Bonus : mise à jour du portfolio en langage naturel via Gemini
# --------------------------------------------------------------------------- #
def update_portfolio_from_text(instruction: str) -> None:
    """Met à jour ``portfolio.yaml`` à partir d'une instruction naturelle.

    Utilise Gemini pour interpréter l'instruction (ex. "vendu 100 RNDR,
    acheté 50 USDC") et réécrit le YAML. Affiche un diff résumé.

    Args:
        instruction: instruction en langage naturel (FR ou EN).
    """
    # Import tardif : évite de charger Gemini quand on ne fait que lire la config.
    from src.ai_brain.gemini_client import GeminiClient

    data = load_portfolio()
    current_yaml = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)

    prompt = (
        "Tu es un assistant qui met à jour un portfolio crypto au format YAML.\n"
        "Voici le YAML actuel :\n\n"
        f"{current_yaml}\n\n"
        f"Instruction de l'utilisateur : \"{instruction}\"\n\n"
        "Applique l'instruction (vente, achat, ajustement de quantité). "
        "Recalcule 'value_usd' proportionnellement si seule la quantité change "
        "(garde le même prix unitaire implicite). Pour un nouvel actif, choisis "
        "un tier cohérent avec sa value_usd (>50:1, 10-50:2, 1-10:3, <1:4) et un "
        "id CoinGecko plausible en commentaire si tu en connais un.\n"
        "Réponds UNIQUEMENT avec le YAML complet mis à jour, sans backticks, "
        "sans aucun texte avant ou après."
    )

    client = GeminiClient()
    response = client.generate(prompt)
    new_yaml = response.strip()
    if new_yaml.startswith("```"):
        new_yaml = new_yaml.strip("`")
        new_yaml = new_yaml.split("\n", 1)[1] if "\n" in new_yaml else new_yaml

    # Validation : on parse avant d'écrire.
    try:
        parsed = yaml.safe_load(new_yaml)
        assert isinstance(parsed, dict) and "portfolio" in parsed
    except (yaml.YAMLError, AssertionError) as exc:
        logger.error("Gemini a renvoyé un YAML invalide, abandon. (%s)", exc)
        sys.exit(1)

    path = _config_path("portfolio")
    path.write_text(new_yaml + "\n", encoding="utf-8")
    logger.info("portfolio.yaml mis à jour. Pense à `git add config/portfolio.yaml && git commit`.")
    print("✅ portfolio.yaml mis à jour. Vérifie le diff avant de commit/push.")


def _main() -> None:
    parser = argparse.ArgumentParser(description="Outils portfolio.")
    parser.add_argument(
        "--update",
        metavar="INSTRUCTION",
        help="Instruction naturelle, ex. \"vendu 100 RNDR, acheté 50 USDC\"",
    )
    parser.add_argument(
        "--show", action="store_true", help="Affiche un résumé du portfolio."
    )
    args = parser.parse_args()

    if args.update:
        update_portfolio_from_text(args.update)
    elif args.show:
        data = load_portfolio()
        total = total_value_usd(data)
        print(f"Portfolio : {len(data['portfolio'])} actifs · total ~${total:,.2f}")
        for sym, info in sorted(
            data["portfolio"].items(),
            key=lambda kv: kv[1].get("value_usd", 0),
            reverse=True,
        ):
            print(f"  T{info['tier']}  {sym:<8} ${info.get('value_usd', 0):>10,.2f}")
    else:
        parser.print_help()


if __name__ == "__main__":
    _main()
