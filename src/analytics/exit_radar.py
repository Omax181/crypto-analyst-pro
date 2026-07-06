"""OB1 — RADAR DE SORTIE / ALLÈGEMENT.

Moteur DÉTERMINISTE consolidant « quelles positions envisager d'alléger
AUJOURD'HUI », aligné sur la stratégie réelle d'Omar :
  • prise de profit par PALIERS : +80 % / ×2 (+100 %) / ×3 (+200 %) vs PRU ;
  • offload des SATELLITES (« jetables ») dans les pumps (vendre la force) ;
  • contrôle de la CONCENTRATION (satellite surpondéré = risque).

Le CŒUR (BTC/ETH/TAO/LINK, gardé des années) n'est signalé que sur extension
EXTRÊME (+300 %) et seulement pour une PETITE tranche — jamais offloadé comme un
satellite. Les satellites reçoivent des signaux bien plus réactifs.

Best-effort : toute donnée manquante → le déclencheur concerné est ignoré, jamais
d'exception. Le LLM COMMENTE ces signaux déterministes, il ne les invente pas.
Cadrage : il s'agit de PRISE DE PROFIT / gestion du risque (aligné sur les
paliers d'Omar), pas d'une contrainte de cash (RE1 intact) ni d'un ordre — c'est
« à considérer », Omar tranche.
"""

from __future__ import annotations

from typing import Any, Optional

# Cœur structurel d'Omar (source : investor_profile). Détecté même si le champ
# ``tier`` du portefeuille est absent → protège toujours ces 4 positions.
_CORE_SYMBOLS = {"BTC", "ETH", "TAO", "LINK"}

# Audit v26 final — un stablecoin (ajoutable via /buy) n'est ni un palier de
# profit ni un risque de concentration volatile : JAMAIS de signal dessus
# (« USDC surpondéré → réduis le risque » serait un non-sens, c'est du cash).
_STABLE_SYMBOLS = {"USDC", "USDT", "DAI", "FDUSD", "TUSD", "PYUSD", "USDE"}

# Seuils (points de %). Tunables sans toucher à la logique.
_LADDER_X3 = 200.0        # ×3 vs PRU
_LADDER_X2 = 100.0        # ×2 vs PRU
_LADDER_80 = 80.0         # premier palier de prise de profit
_CORE_EXTREME = 300.0     # cœur : seuil d'extension extrême (petite tranche)
_PUMP_7D = 40.0           # pump satellite sur 7 j → fenêtre d'offload
_PUMP_24H = 20.0          # accélération 24 h (renforce le signal de pump)
_CONC_SATELLITE = 12.0    # satellite surpondéré (% du PTF)


def _is_core(symbol: str, tier: Optional[str]) -> bool:
    return str(tier or "").lower() == "core" or symbol.upper() in _CORE_SYMBOLS


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def compute_exit_signals(positions: list[dict[str, Any]]) -> dict[str, Any]:
    """Signaux d'allègement déterministes, un au plus par position (le plus urgent).

    Args:
        positions: liste de dicts au format de
            ``live_data.get_live_portfolio_snapshot()['positions']`` :
            ``{symbol, pnl_pct, weight_pct, tier, change_24h, change_7d}``.

    Returns:
        ``{available, signals: [{symbol, tier, urgency (1-3), reason, action,
        pnl_pct, weight_pct, change_7d}], count, summary}``.
    """
    signals: list[dict[str, Any]] = []
    for p in positions or []:
        sym = str(p.get("symbol") or "").upper()
        if not sym or sym in _STABLE_SYMBOLS:
            continue
        pnl = _num(p.get("pnl_pct"))
        weight = _num(p.get("weight_pct"))
        chg7 = _num(p.get("change_7d"))
        chg24 = _num(p.get("change_24h"))
        core = _is_core(sym, p.get("tier"))
        best: Optional[tuple[int, str, str]] = None  # (urgency, reason, action)

        # 1) PALIERS DE PRISE DE PROFIT (priorité maximale).
        if pnl is not None:
            if core and pnl >= _CORE_EXTREME:
                best = (2, f"cœur très étendu (+{pnl:.0f}% vs PRU)",
                        "allège une PETITE tranche, garde le socle long terme")
            elif not core and pnl >= _LADDER_X3:
                best = (3, f"×3 atteint (+{pnl:.0f}% vs PRU)",
                        "allège une grosse tranche (prise de profit)")
            elif not core and pnl >= _LADDER_X2:
                best = (3, f"×2 atteint (+{pnl:.0f}% vs PRU)",
                        "allège une tranche")
            elif not core and pnl >= _LADDER_80:
                best = (2, f"palier +80 % atteint (+{pnl:.0f}% vs PRU)",
                        "commence à alléger une première tranche")

        # 2) PUMP MOMENTUM sur satellite (ta stratégie : vendre la force).
        if (best is None and not core and chg7 is not None and chg7 >= _PUMP_7D
                and (pnl is None or pnl > 0)):
            extra = (f", +{chg24:.0f}% sur 24h"
                     if chg24 is not None and chg24 >= _PUMP_24H else "")
            best = (2, f"pump de +{chg7:.0f}% sur 7j{extra}",
                    "fenêtre pour offloader une partie sur la force")

        # 3) SUR-CONCENTRATION (satellite surpondéré).
        if (best is None and not core and weight is not None
                and weight >= _CONC_SATELLITE):
            best = (1, f"surpondéré ({weight:.0f}% du PTF)",
                    "réduis le risque de concentration")

        if best is not None:
            urgency, reason, action = best
            signals.append({
                "symbol": sym,
                "tier": "core" if core else "satellite",
                "urgency": urgency,
                "reason": reason,
                "action": action,
                "pnl_pct": pnl,
                "weight_pct": weight,
                "change_7d": chg7,
            })

    signals.sort(key=lambda s: (s["urgency"], s.get("pnl_pct") or 0.0), reverse=True)
    if not signals:
        return {"available": False, "signals": [], "count": 0}
    parts = [f"{s['symbol']} ({s['reason']})" for s in signals[:4]]
    return {
        "available": True,
        "signals": signals,
        "count": len(signals),
        "summary": (f"{len(signals)} position(s) à considérer pour allègement : "
                    + ", ".join(parts)),
    }
