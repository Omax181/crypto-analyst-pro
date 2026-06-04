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
