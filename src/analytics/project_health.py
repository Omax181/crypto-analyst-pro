"""Verdict de santé projet : détecte les signaux 'sors-en' et les warnings.

Combine dev activity (GitHub), liquidité (volume comme proxy) et tendance de
volume. Les critères web (site down, silence founder) ne sont pas vérifiables
sans scraping dédié : ils restent dans la config comme référence et peuvent
être enrichis par la synthèse Gemini/news.
"""

from __future__ import annotations

from typing import Any

from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_TH = load_config("thresholds")
_CRIT = _TH["project_health"]["critical"]
_WARN = _TH["project_health"]["warning"]


def project_health(
    *,
    symbol: str,
    dev_activity: dict[str, Any],
    market: dict[str, Any],
) -> dict[str, Any]:
    """Évalue la santé d'un projet.

    Args:
        symbol: ticker.
        dev_activity: sortie de ``github_dev.get_dev_activity``.
        market: sortie marché CoinGecko pour ce symbole.

    Returns:
        Dict ``{verdict, critical_flags, warnings}`` où ``verdict`` ∈
        {``"ok"``, ``"warning"``, ``"exit"``}.
    """
    critical_flags: list[str] = []
    warnings: list[str] = []

    # --- Critères CRITIQUES (dev) ---
    if dev_activity.get("available"):
        commits = dev_activity.get("commits_30d", 0) or 0
        days_ago = dev_activity.get("last_commit_days_ago")
        if commits == 0 and (days_ago is None or days_ago >= _CRIT["dev_zero_commits_days"]):
            critical_flags.append(
                f"Aucun commit récent (>{_CRIT['dev_zero_commits_days']}j)"
            )

    # --- Liquidité (proxy : volume 24h) ---
    volume = market.get("volume_24h")
    if volume is not None and volume < _CRIT["min_total_liquidity_usd"]:
        critical_flags.append(
            f"Volume 24h très faible (<${_CRIT['min_total_liquidity_usd']:,.0f})"
        )

    # --- Critères WARNING ---
    if dev_activity.get("available"):
        commits = dev_activity.get("commits_30d", 0) or 0
        if 0 < commits < 5:
            warnings.append("Activité dev faible (<5 commits/30j)")

    change_from_ath = market.get("change_from_ath_pct")
    if change_from_ath is not None and change_from_ath < -90:
        warnings.append(f"À {change_from_ath:.0f}% de l'ATH")

    verdict = "ok"
    if critical_flags:
        verdict = "exit"
    elif warnings:
        verdict = "warning"

    return {
        "verdict": verdict,
        "critical_flags": critical_flags,
        "warnings": warnings,
    }
