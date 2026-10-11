"""Plan de trade DÉTERMINISTE par actif (v27 · TH1/TH2/ES1/ES2/ES3/RE1/RE2/RE3).

Transforme les niveaux calculés (``key_levels``) en un PLAN falsifiable :
    • invalidation chiffrée (TH1) — le prix qui TUE la thèse, avec sa base ;
    • cible 30 j (ES1) en FOURCHETTE (ES2) — prochaine résistance ± ATR ;
    • cible cycle (ES1/ES2) — chemin vers l'ATH réel (fib 0.618 → ATH) ;
    • R:R (RE2) — (cible − prix) / (prix − invalidation) ;
    • EV prospectif 30 j (ES3) — p(hausse) × upside − p(baisse) × downside,
      p dérivée de signaux objectifs (RSI, tendance, funding, tilt marché),
      bornée [0.30, 0.70] : une ESTIMATION indicative, jamais une certitude ;
    • bull / base / bear par actif (TH2) avec probabilités sommant à 100 ;
    • zone d'accumulation + DCA 3 tranches (RE3) ;
    • sizing suggéré en % du PTF et $ (RE1) — plafonné par la concentration,
      SANS jamais considérer le cash comme une contrainte (Omar peut injecter).

Tout est Python : le LLM commente ces chiffres, il ne les invente plus.
Chaque champ est None-tolérant (dégradation gracieuse si une donnée manque).
"""

from __future__ import annotations

from typing import Any, Optional

from src.analytics.key_levels import compute_key_levels
from src.utils import numfmt as _numfmt
from src.utils.numfmt import arrondi_niveau as _arr
from src.utils.logger import get_logger

logger = get_logger(__name__)

# v32 (5.20) — NEUTRE d'un funding perpétuel : le taux par défaut appliqué par
# les plateformes quand la prime est dans le corridor (0,01 % par période de
# 8 h), soit 0,01 × 3 × 365 = 10,95 %/an. C'est le point zéro du signal :
# en dessous, les shorts payent ; au-dessus, les longs payent.
_FUNDING_NEUTRE = 10.95


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN out


def _fmt_usd(v: Optional[float]) -> Optional[str]:
    """« 61 949 $ » (espace fine insécable) / « 0.0850 $ » — compact FR."""
    if v is None:
        return None
    a = abs(v)
    if a >= 1000:
        return f"{v:,.0f}".replace(",", " ") + " $"
    # v30.1 (ré-audit #67) — décimale en VIRGULE : les labels de plan
    # (invalidation, cibles, DCA) affichaient « 270.00 $ » (US) au milieu
    # d'une prose unifiée FR.
    if a >= 1:
        return f"{v:,.2f}".replace(".", ",") + " $"
    if a >= 0.01:
        return f"{v:.4f}".replace(".", ",") + " $"
    # v32 (5.6) — 6 décimales FIXES perdaient encore les chiffres d'un actif à
    # 1,2e-5 $ ; l'autorité unique donne 4 chiffres SIGNIFICATIFS.
    return _numfmt.fr_num(v, thin=False) + " $"


def _pct_fr(v: float, nd: int = 1) -> str:
    return f"{'+' if v >= 0 else '−'}{abs(round(v, nd))}%".replace(".", ",")


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ── p(hausse) 30 j — signaux objectifs, bornée, indicative ────────────────

def _prob_up_30d(
    readout: dict[str, Any],
    funding_annualized_pct: Any = None,
    market_net_tilt: Any = None,
) -> float:
    """Probabilité indicative de hausse à 30 j depuis les signaux disponibles.

    Chaque signal contribue un tilt ∈ [−1, 1] ; la moyenne pondérée est
    convertie en probabilité bornée [0.30, 0.70] — on n'affirme jamais une
    quasi-certitude depuis 4 indicateurs techniques.
    """
    tilts: list[tuple[float, float]] = []  # (tilt, poids)
    rsi = _num((readout or {}).get("rsi"))
    if rsi is not None:
        # Contrarian doux : survendu → tilt positif, suracheté → négatif.
        tilts.append((_clamp((50.0 - rsi) / 30.0, -1, 1), 1.0))
    trend = _num((readout or {}).get("trend_7d_pct"))
    if trend is not None:
        # Momentum : la tendance 7j se prolonge plus souvent qu'elle ne s'inverse.
        tilts.append((_clamp(trend / 10.0, -1, 1), 0.8))
    ma200 = _num((readout or {}).get("ma200_rel_pct"))
    if ma200 is not None:
        tilts.append((_clamp(ma200 / 25.0, -1, 1), 0.6))
    fund = _num(funding_annualized_pct)
    if fund is not None:
        # ── v32 (5.20) — LE POINT ZÉRO EST LE NEUTRE DU MARCHÉ, PAS 0 %/AN.
        # Arbitrage d'Omar (25/08/2026).
        #
        # Funding très négatif = shorts en excès → carburant contrarian
        # haussier ; funding très positif = surchauffe des longs. Mais le signal
        # était centré sur 0 %/an, alors que le NEUTRE d'un perpétuel est le
        # taux par défaut des plateformes : 0,01 % par période, soit
        # 0,01 × 3 × 365 = **+10,95 %/an**. Un actif au funding parfaitement
        # normal recevait donc un tilt de −0,438 (poids 0,6) — MESURÉ : −2,0
        # points de probabilité de hausse par rapport à l'absence totale de
        # donnée. Ce biais baissier s'appliquait à presque tous les actifs, se
        # propageait à l'« Espérance 30 j » publiée sur chaque fiche, et
        # provenait d'une valeur qui ne dit rien.
        tilts.append((_clamp(-(fund - _FUNDING_NEUTRE) / 25.0, -1, 1), 0.6))
    tilt_mkt = _num(market_net_tilt)
    if tilt_mkt is not None:
        tilts.append((_clamp(tilt_mkt, -1, 1), 0.8))
    if not tilts:
        return 0.5
    num = sum(t * w for t, w in tilts)
    den = sum(w for _, w in tilts)
    return round(_clamp(0.5 + 0.2 * (num / den), 0.30, 0.70), 2)


# ── sizing suggéré (RE1) — % PTF plafonné par la concentration ────────────

def suggest_sizing(
    *,
    action_type: str,
    weight_pct: Any = None,
    ptf_value_usd: Any = None,
    is_core: bool = False,
    position_value_usd: Any = None,
) -> Optional[dict[str, Any]]:
    """Geste chiffré suggéré : % du PTF + $ + poids avant→après.

    Le CASH N'EST JAMAIS une contrainte (Omar peut injecter des fonds
    externes) : le sizing s'exprime en % du PTF et en $, sans conditionner à
    une vente. Garde-fou concentration : pas de renfort proposé au-delà de
    20% du PTF sur un même actif (12% pour un satellite).
    """
    def _p(x: float, nd: int = 1) -> str:
        """Pourcentage FR : 12,4 (virgule décimale) — v30 (#5/#67)."""
        return f"{x:.{nd}f}".replace(".", ",")

    w = _num(weight_pct)
    ptf = _num(ptf_value_usd)
    act = (action_type or "").lower()
    if act in ("bullish", "renforcer", "buy", "accumuler"):
        cap = 20.0 if is_core else 12.0
        _cap_lbl = f"plafond {cap:.0f}% {'cœur' if is_core else 'satellite'}"
        if w is not None and w >= cap:
            return {
                "add_pct_ptf": 0.0,
                "note": (f"déjà {_p(w)}% du PTF ({_cap_lbl}) — "
                         "renfort non suggéré, concentration"),
            }
        add = (2.0 if is_core else 1.0) if (w is None or w < cap - 3) else 0.5
        # v30 (#82) — sous ~50 $ de renfort, un DCA 3 tranches est absurde
        # (tranches de 5 $, frais > gain d'exécution) : signal au consommateur.
        # (posé plus bas dans out["single_shot"] quand add_usd < 50.)
        # v30 (#6) — le renfort ne DÉPASSE jamais le plafond : le 14/07,
        # « porte 12% → 12,4% » franchissait le plafond que le même moteur
        # opposait à BTC/ETH. Clamp au plafond ; reliquat < 0,25% = plafonné.
        if w is not None:
            add = min(add, round(cap - w, 1))
            if add < 0.25:
                return {
                    "add_pct_ptf": 0.0,
                    "note": (f"déjà {_p(w)}% du PTF ({_cap_lbl}) — "
                             "renfort non suggéré, concentration"),
                }
        out: dict[str, Any] = {"add_pct_ptf": add}
        if ptf:
            out["add_usd"] = round(ptf * add / 100.0, 0)
            if out["add_usd"] < 50:
                out["single_shot"] = True
        if w is not None:
            out["weight_before_pct"] = round(w, 1)
            out["weight_after_pct"] = round(w + add, 1)
            _usd = f" (≈ {_fmt_usd(out.get('add_usd'))})" if out.get("add_usd") else ""
            # v30 (#5) — MÊME précision de part et d'autre de la flèche
            # (fini « +1,0% … porte 2% → 3,4% » où 2 était un 2,4 tronqué).
            out["note"] = (f"+{_p(add)}% du PTF{_usd} · porte "
                           f"{_p(w)}% → {_p(w + add)}% du PTF")
        return out
    if act in ("bearish", "alléger", "alleger", "sell", "sortir"):
        pv = _num(position_value_usd)
        trim_pct = 50.0 if not is_core else 25.0
        out = {"trim_pct_position": trim_pct}
        if pv:
            out["trim_usd"] = round(pv * trim_pct / 100.0, 0)
            out["note"] = (f"−{trim_pct:.0f}% de la position "
                           f"(≈ {_fmt_usd(out['trim_usd'])})")
        return out
    return None


# ── plan complet par actif ────────────────────────────────────────────────

def compute_asset_plan(
    symbol: str,
    closes: list[float],
    volumes: Optional[list[float]] = None,
    *,
    price: Optional[float] = None,
    ath: Any = None,
    ath_suspect: bool = False,
    funding_annualized_pct: Any = None,
    market_net_tilt: Any = None,
    key_levels_result: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Construit le plan déterministe d'un actif (voir docstring module).

    Args:
        symbol: ticker.
        closes: clôtures chronologiques (≥ 30).
        volumes: volumes alignés (optionnel).
        price: prix spot live (défaut : dernière clôture).
        ath: ATH réel CoinGecko (None → pas de cible cycle).
        ath_suspect: True = ATH de listing illiquide → cible cycle omise.
        funding_annualized_pct: funding annualisé Binance (contrarian).
        market_net_tilt: biais directionnel marché (scaffold), ∈ [−1, 1].
        key_levels_result: résultat ``compute_key_levels`` déjà calculé
            (évite un recalcul) ; sinon calculé ici.

    Returns:
        ``{available, symbol, price, invalidation, target_30d, target_cycle,
        rr_30d, prob_up_30d, ev_30d_pct, scenarios, accumulation_zone, dca,
        plan_line}`` — ``available=False`` si données insuffisantes.
    """
    kl = key_levels_result or compute_key_levels(
        symbol, closes, volumes, price=price)
    if not kl or not kl.get("available"):
        return {"available": False, "symbol": symbol,
                "reason": (kl or {}).get("reason") or "niveaux indisponibles"}
    px = _num(kl.get("price"))
    if not px or px <= 0:
        return {"available": False, "symbol": symbol, "reason": "prix invalide"}

    sups = kl.get("supports") or []
    ress = kl.get("resistances") or []
    readout = kl.get("readout") or {}
    atr = _num(readout.get("atr_abs")) or px * 0.03  # repli : 3% du prix

    # ── RED TEAM (RT-4) — L'INVALIDATION DOIT RESTER SOUS LE REPÈRE
    # D'ACCUMULATION DU MÊME PLAN.
    #
    # ``s_ref`` est le support de travail : le premier support détecté, ou
    # « prix − 1 ATR » quand aucun ne l'est. Il sert à la zone d'accumulation
    # et au 2ᵉ palier DCA. Les replis de l'invalidation étaient, eux, ancrés
    # sur un −8 % FIXE : deux ancrages indépendants, donc un ordre non garanti.
    # Reproduit par fuzzing sur des prix ordinaires : « Invalidation 0,1843 $ ·
    # Zone d'accu 0,1803–0,1903 $ · DCA 50 % à 0,1803 $ » — le plan faisait
    # acheter la moitié de la position 2 % SOUS son propre stop. Il suffit
    # qu'aucun support ne soit détecté (l'actif fait un plus-bas) et que l'ATR
    # dépasse 8 % du prix. On calcule donc ``s_ref`` d'abord, et tout repli
    # d'invalidation passe sous lui d'au moins une demi-amplitude.
    _s1 = _num(sups[0].get("level")) if sups else None
    s_ref = _s1 if (_s1 and _s1 < px) else px - atr

    def _repli_invalidation() -> float:
        """−8 %, ou une demi-amplitude sous le repère d'accumulation : le plus bas."""
        return min(px * 0.92, s_ref - 0.5 * atr)

    # ── TH1 — INVALIDATION : le prix qui tue la thèse. 2e support (le 1er
    # peut être bruité par l'intraday) ; repli : 1er support − 1 ATR ; repli
    # ultime : −8% (jamais de plan sans invalidation).
    if len(sups) >= 2:
        inv_level = _num(sups[1].get("level"))
        inv_basis = sups[1].get("basis")
    elif sups:
        inv_level = (_num(sups[0].get("level")) or px * 0.95) - atr
        inv_basis = f"{sups[0].get('basis')} − 1 ATR"
    else:
        inv_level = _repli_invalidation()
        inv_basis = ("repli −8% (aucun support détecté)"
                     if inv_level >= px * 0.92 - 1e-12
                     else "1,5 ATR sous le prix (aucun support détecté)")
    if inv_level is None or inv_level >= px or inv_level <= 0:
        # ``inv_level <= 0`` : micro-prix avec ATR > support (« s0 − 1 ATR »
        # négatif) — un plan n'a jamais d'invalidation à 0 ou négative.
        inv_level = _repli_invalidation()
        inv_basis = ("repli −8% (support incohérent)"
                     if inv_level >= px * 0.92 - 1e-12
                     else "1,5 ATR sous le prix (support incohérent)")
    if inv_level <= 0:
        # Volatilité extrême : la demi-amplitude passe sous zéro. On garde une
        # invalidation strictement positive plutôt qu'un prix impossible.
        inv_level, inv_basis = px * 0.5, "repli −50% (volatilité extrême)"
    # ── RED TEAM (RT-4, filet) — LE REPÈRE D'ACCUMULATION RESTE AU-DESSUS DU
    # STOP, quoi qu'il arrive. Le repli « −50 % (volatilité extrême) » ci-dessus
    # remonte l'invalidation ; quand l'ATR dépasse la moitié du prix, le repère
    # « prix − 1 ATR » repasse en dessous. On le replace alors à mi-chemin, et
    # on le DIT dans la base du palier : un palier d'accumulation sous son
    # propre stop n'est pas un palier, c'est une contradiction publiée.
    _base_s_ref = None
    if s_ref <= inv_level:
        s_ref = (inv_level + px) / 2.0
        _base_s_ref = "mi-chemin prix / invalidation (volatilité extrême)"

    invalidation = {
        "level": _arr(inv_level),
        "level_label": _fmt_usd(inv_level),
        "basis": inv_basis,
        "dist_pct": round((inv_level - px) / px * 100, 1),
    }

    # ── ES1/ES2 — CIBLE 30 j : première résistance à ≥ +3% (sinon la
    # suivante), en FOURCHETTE ± 1 ATR (honnête, pas de fausse précision).
    tgt = None
    for r in ress:
        lv = _num(r.get("level"))
        if lv and (lv - px) / px * 100 >= 3.0:
            tgt = (lv, r.get("basis"))
            break
    if tgt is None and ress:
        lv = _num(ress[-1].get("level"))
        if lv:
            tgt = (lv, ress[-1].get("basis"))
    if tgt is None:
        tgt = (px + 2 * atr, "extension +2 ATR (aucune résistance détectée)")
    target_30d = {
        "level": _arr(tgt[0]),
        "level_label": _fmt_usd(tgt[0]),
        "basis": tgt[1],
        "low": _arr(max(tgt[0] - atr, px)),
        "high": _arr(tgt[0] + atr),
        "low_label": _fmt_usd(max(tgt[0] - atr, px)),
        "high_label": _fmt_usd(tgt[0] + atr),
        "upside_pct": round((tgt[0] - px) / px * 100, 1),
    }

    # ── ES1/ES2 — CIBLE CYCLE : chemin fib 0.618 → ATH réel (jamais
    # au-delà) ; omise si ATH suspect (listing illiquide) ou déjà proche.
    target_cycle = None
    ath_v = _num(ath)
    if ath_v and ath_v > px * 1.10 and not ath_suspect:
        low_c = px + (ath_v - px) * 0.618
        target_cycle = {
            "low": _arr(low_c),
            "high": _arr(ath_v),
            "low_label": _fmt_usd(low_c),
            "high_label": _fmt_usd(ath_v),
            "upside_pct": round((ath_v - px) / px * 100, 0),
            "kind": ("cycle" if (ath_v - px) / px * 100 >= 250 else "6-12m"),
            "basis": "fib 0.618 → ATH réel",
        }

    # ── RE2 — R:R sur le plan 30 j.
    risk = px - inv_level
    reward = target_30d["level"] - px
    rr_30d = round(reward / risk, 1) if risk > 0 and reward > 0 else None

    # ── ES3 — EV prospectif 30 j (indicatif).
    p_up = _prob_up_30d(readout, funding_annualized_pct, market_net_tilt)
    upside_pct = (target_30d["level"] - px) / px * 100
    downside_pct = (inv_level - px) / px * 100  # négatif
    ev = round(p_up * upside_pct + (1 - p_up) * downside_pct, 1)

    # ── TH2 — BULL / BASE / BEAR par actif, probabilités sommant à 100.
    # Base comprimée quand le tilt est net ; bull/bear répartis selon p_up.
    tilt_strength = abs(p_up - 0.5) * 2  # 0..0.4 (p_up borné [0.30, 0.70])
    p_base = int(round(55 - 20 * tilt_strength))
    p_bull = int(round((100 - p_base) * p_up))
    p_bear = 100 - p_base - p_bull
    scenarios = {
        "bull": {
            "probability_pct": p_bull,
            "level": target_30d["high"],
            "level_label": _fmt_usd(target_30d["high"]),
            "condition": (f"cassure de {target_30d['level_label']} "
                          f"({target_30d['basis']}) en clôture"),
        },
        "base": {
            "probability_pct": p_base,
            "low": _arr(_s1) if _s1 else invalidation["level"],
            "high": target_30d["level"],
            "range_label": (f"{_fmt_usd(_s1 if _s1 else invalidation['level'])}"
                            f" – {target_30d['level_label']}"),
            "condition": "consolidation entre support et résistance",
        },
        "bear": {
            "probability_pct": p_bear,
            # RED TEAM (RT-7) — un PRIX négatif n'existe pas. « invalidation
            # − 1 ATR » passait sous zéro quand l'ATR dépassait l'invalidation
            # (volatilité extrême) : le mail publiait « −42,21 $ » comme
            # objectif baissier. Plancher à la moitié de l'invalidation.
            "level": _arr(max(inv_level - atr, inv_level * 0.5)),
            "level_label": _fmt_usd(max(inv_level - atr, inv_level * 0.5)),
            "condition": (f"cassure de {invalidation['level_label']} "
                          f"({invalidation['basis']}) en clôture"),
        },
    }

    # ── RE3 — ZONE D'ACCUMULATION + DCA 3 tranches (contrarian, profil Omar).
    s1 = s_ref
    accumulation_zone = {
        "low": _arr(min(inv_level + 0.25 * atr, s1)),
        "high": _arr(min(px, s1 + 0.5 * atr)),
    }
    accumulation_zone["low_label"] = _fmt_usd(accumulation_zone["low"])
    accumulation_zone["high_label"] = _fmt_usd(accumulation_zone["high"])
    # ── v32 (5.10) — LA 3ᵉ TRANCHE DOIT AVOIR DE LA PLACE, ou disparaître.
    # Elle était placée à inv + 0,25 ATR, soit un QUART de l'amplitude
    # journalière au-dessus du stop du MÊME plan. Mesuré sur les mails réels :
    # INJ 24/08 tranche 3 a 5,30 $ pour un stop a 5,25 $ ; TAO 21/08 213,70 $
    # pour un stop a 212,45 $ ; RENDER 21/08 1,26 $ pour un stop a 1,25 $. Un
    # ordre exécuté là se fait sortir par le bruit ordinaire de la séance : la
    # tranche d'accumulation et le stop du même plan ne peuvent pas coexister
    # à cette distance. Trois paliers factices valent moins que deux vrais :
    # si l'espace sous le support ne loge pas un palier à ≥ 0,5 ATR du stop et
    # nettement sous le précédent, on n'en publie que deux, et on le dit.
    dca = [
        {"price": _arr(px), "price_label": _fmt_usd(px),
         "weight_pct": 40, "basis": "prix actuel"},
        {"price": _arr(s1), "price_label": _fmt_usd(s1),
         "weight_pct": 30, "basis": (_base_s_ref
                                     or (sups[0].get("basis") if sups
                                         else "prix − 1 ATR"))},
    ]
    dca_note = None
    # 0,5 ATR = une demi-amplitude journalière : en-deçà, le palier et le stop
    # sont le même niveau à la volatilité près, et l'ordre se fait sortir le
    # jour de son exécution. C'est le minimum, pas un confort.
    _t3 = inv_level + 0.5 * atr
    if _t3 < s1 - 0.15 * atr:
        dca.append({"price": _arr(_t3), "price_label": _fmt_usd(_t3),
                    "weight_pct": 30,
                    "basis": "0,5 ATR au-dessus de l'invalidation"})
    else:
        # Pas de place : on redistribue le poids sur les deux paliers reels.
        dca[0]["weight_pct"], dca[1]["weight_pct"] = 50, 50
        _repere = ("sous le support" if sups
                   else "sous le prix − 1 ATR (aucun support détecté)")
        dca_note = (f"2 paliers seulement : {_repere}, il ne reste pas "
                    "une demi-amplitude avant l'invalidation — un 3e palier serait sorti "
                    "par le bruit du jour.")

    # ── ligne FR compacte (rendu mail + Telegram).
    parts = [
        f"Invalidation {invalidation['level_label']} "
        f"({invalidation['basis']} · {_pct_fr(invalidation['dist_pct'])})",
        f"Cible 30j {target_30d['level_label']} "
        f"[{target_30d['low_label']}–{target_30d['high_label']}]",
    ]
    if rr_30d is not None:
        parts.append(f"R:R {str(rr_30d).replace('.', ',')}")
    parts.append(f"EV 30j {_pct_fr(ev)} (p↑ {int(p_up * 100)}%)")
    parts.append(f"Zone d'accu {accumulation_zone['low_label']}"
                 f"–{accumulation_zone['high_label']}")
    plan_line = " · ".join(parts)

    return {
        "available": True,
        "symbol": symbol,
        "price": _arr(px),
        "price_label": _fmt_usd(px),
        "invalidation": invalidation,
        "target_30d": target_30d,
        "target_cycle": target_cycle,
        "rr_30d": rr_30d,
        "prob_up_30d": p_up,
        "ev_30d_pct": ev,
        # v32 (5.9) - L'ESPÉRANCE DIT SUR QUOI ELLE PORTE. Le mail affichait
        # côte à côte une espérance à DEUX issues (cible vs invalidation,
        # pondérées par p_up) et un arbre à TROIS scénarios (bull/base/bear à
        # des niveaux différents) : deux modèles du même futur, impossibles à
        # réconcilier. Vérifié sur les mails réels — INJ 24/08 : 0,50 ×
        # (+3,91 %) + 0,50 × (−2,23 %) = +0,8 %, exactement la valeur publiée,
        # alors que les scénarios affichés en donnaient +1,2 %. Les deux
        # chiffres étaient justes ; rien ne disait qu'ils ne mesuraient pas la
        # même chose. On nomme donc la base de calcul.
        "ev_note": ("cible 30 j vs invalidation, pondérées par p↑ — "
                    "distincte de l'arbre bull/base/bear ci-dessus ; "
                    "estimation indicative, pas une certitude"),
        "scenarios": scenarios,
        "accumulation_zone": accumulation_zone,
        "dca": dca,
        "dca_note": dca_note,
        "plan_line": plan_line,
    }
