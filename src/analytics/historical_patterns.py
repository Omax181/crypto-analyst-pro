"""Base de cas historiques : patterns macro->crypto avec statistiques.

Cette base sert de RÉFÉRENCE FACTUELLE passée à Gemini pour qu'il fasse des
liens historiques chiffrés (ex. réaction de BTC après une publication CPI).

⚠️ Ces statistiques sont des ordres de grandeur indicatifs basés sur des
tendances observées 2022-2025. Elles ne constituent pas une garantie. Gemini
doit toujours nommer les conditions d'invalidation. À actualiser périodiquement.
"""

from __future__ import annotations

from typing import Any

HISTORICAL_PATTERNS: list[dict[str, Any]] = [
    {
        "trigger": "CPI US au-dessus du consensus",
        "asset": "BTC",
        "window": "48h",
        "observation": "correction à court terme dans une majorité de cas",
        "stat": "BTC a corrigé dans les 48h dans ~7 cas sur 11 depuis 2022",
        "invalidation": "si le marché anticipait déjà un chiffre élevé (déjà pricé)",
    },
    {
        "trigger": "FOMC : ton dovish / pause de hausse",
        "asset": "crypto risk-on (BTC/ETH/alts AI)",
        "window": "1 semaine",
        "observation": "rebond du risk-on",
        "stat": "réaction haussière dans ~6 cas sur 9 depuis 2022",
        "invalidation": "si accompagné d'une révision haussière des projections de taux",
    },
    {
        "trigger": "Hausse marquée du DXY (>1% en 5j)",
        "asset": "altcoins",
        "window": "1-2 semaines",
        "observation": "pression baissière sur les alts (corrélation inverse)",
        "stat": "sous-performance des alts vs BTC dans la majorité des épisodes",
        "invalidation": "narrative crypto-spécifique forte qui découple un secteur",
    },
    {
        "trigger": "Fear & Greed < 25 (peur extrême)",
        "asset": "BTC",
        "window": "30-90j",
        "observation": "zones historiquement favorables à l'accumulation long terme",
        "stat": "les creux de peur extrême ont souvent précédé des rebonds pluri-semaines",
        "invalidation": "détérioration macro structurelle (récession confirmée, crise crédit)",
    },
    {
        "trigger": "Fear & Greed > 75 (avidité extrême)",
        "asset": "BTC/alts",
        "window": "court terme",
        "observation": "risque accru de correction / prise de profits",
        "stat": "les pics d'avidité ont souvent coïncidé avec des sommets locaux",
        "invalidation": "afflux structurel (ETF, adoption) qui soutient durablement",
    },
    {
        "trigger": "Yield curve (10Y-2Y) repasse positive après inversion",
        "asset": "risk-on global",
        "window": "plusieurs mois",
        "observation": "signal macro historiquement précurseur de volatilité",
        "stat": "la dé-inversion a souvent précédé des phases de stress des marchés",
        "invalidation": "soft landing confirmé par l'emploi et la consommation",
    },
    {
        "trigger": "Rotation narrative AI (TAO/RNDR/FET) sur volumes croissants",
        "asset": "paniers AI",
        "window": "jours-semaines",
        "observation": "surperformance temporaire du secteur AI vs marché",
        "stat": "les rotations sectorielles crypto durent typiquement de quelques jours à semaines",
        "invalidation": "essoufflement du volume ou rotation vers un autre secteur",
    },
]


def relevant_patterns(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Filtre les patterns pertinents selon le contexte du jour.

    Args:
        context: dict pouvant contenir ``fear_greed`` (int), ``has_cpi`` (bool),
            ``has_fomc`` (bool), ``dxy_up`` (bool).

    Returns:
        Liste des patterns jugés pertinents (sinon retourne tout pour que
        Gemini choisisse).
    """
    fng = context.get("fear_greed")
    selected: list[dict[str, Any]] = []
    for p in HISTORICAL_PATTERNS:
        trig = p["trigger"].lower()
        if context.get("has_cpi") and "cpi" in trig:
            selected.append(p)
        elif context.get("has_fomc") and "fomc" in trig:
            selected.append(p)
        elif context.get("dxy_up") and "dxy" in trig:
            selected.append(p)
        elif fng is not None and "fear & greed < 25" in trig and fng < 25:
            selected.append(p)
        elif fng is not None and "fear & greed > 75" in trig and fng > 75:
            selected.append(p)
    return selected or HISTORICAL_PATTERNS


# --- A11 : analyse historique chartiste RÉELLE (calculée sur OHLC) -----------

def compute_setup_stats(
    closes: list[float],
    change_24h: float | None = None,
    forward_days: int = 7,
) -> dict[str, Any]:
    """Statistiques chartistes RÉELLES d'un actif à partir de ses clôtures.

    Au lieu d'affirmations vagues, on quantifie le comportement passé du
    « même type de configuration » : on repère dans l'historique les jours où
    l'actif était au moins aussi survendu qu'aujourd'hui (écart sous sa moyenne
    mobile 20j comparable, ou repli journalier comparable), puis on mesure le
    rendement moyen sur ``forward_days`` jours et la proportion de cas positifs.

    Args:
        closes: clôtures quotidiennes croissantes (la dernière = aujourd'hui).
        change_24h: variation 24h actuelle (%) pour calibrer « repli comparable ».
        forward_days: horizon de mesure du rendement forward (jours).

    Returns:
        Dict ``{available, occurrences, avg_forward_pct, win_rate_pct,
        forward_days, lookback_days, summary}``. ``available=False`` si
        l'historique est trop court (< ~35 points).
    """
    if not closes or len(closes) < 35:
        return {"available": False}

    n = len(closes)
    ma_window = 20

    # Écart actuel sous la moyenne mobile 20j (mesure de survente positionnelle).
    def _ma_gap(idx: int) -> float | None:
        if idx < ma_window:
            return None
        window = closes[idx - ma_window:idx]
        ma = sum(window) / ma_window
        if ma <= 0:
            return None
        return (closes[idx] - ma) / ma * 100.0

    current_gap = _ma_gap(n - 1)
    # Seuil de « configuration comparable » : aussi bas (ou plus) que maintenant.
    # Si l'écart actuel n'est pas calculable, on retombe sur le repli 24h.
    drop_threshold = None
    if change_24h is not None:
        drop_threshold = min(float(change_24h), -2.0)  # au moins -2%

    occurrences = 0
    forward_returns: list[float] = []
    # On s'arrête forward_days avant la fin pour avoir un rendement forward complet.
    for i in range(ma_window, n - forward_days):
        gap = _ma_gap(i)
        is_similar = False
        if current_gap is not None and gap is not None and current_gap < 0:
            # configuration au moins aussi survendue que maintenant
            is_similar = gap <= current_gap
        elif drop_threshold is not None and i >= 1 and closes[i - 1] > 0:
            day_ret = (closes[i] - closes[i - 1]) / closes[i - 1] * 100.0
            is_similar = day_ret <= drop_threshold
        if not is_similar:
            continue
        entry = closes[i]
        future = closes[i + forward_days]
        if entry > 0:
            occurrences += 1
            forward_returns.append((future - entry) / entry * 100.0)

    if occurrences < 3 or not forward_returns:
        return {
            "available": False,
            "lookback_days": n,
            "reason": "pas assez d'occurrences comparables sur l'historique",
        }

    avg_fwd = sum(forward_returns) / len(forward_returns)
    wins = sum(1 for r in forward_returns if r > 0)
    win_rate = wins / len(forward_returns) * 100.0
    summary = (
        f"Sur ~{n}j d'historique, une configuration aussi survendue s'est "
        f"présentée {occurrences} fois ; rendement moyen {avg_fwd:+.1f}% sur "
        f"{forward_days}j, positif dans {win_rate:.0f}% des cas."
    )
    return {
        "available": True,
        "occurrences": occurrences,
        "avg_forward_pct": round(avg_fwd, 1),
        "win_rate_pct": round(win_rate, 0),
        "forward_days": forward_days,
        "lookback_days": n,
        "summary": summary,
    }
