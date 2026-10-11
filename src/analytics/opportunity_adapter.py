"""Adaptateur ``eligible_theses`` → candidats normalisés du moteur v33.

Le moteur (`src.analytics.opportunity`) est volontairement pur : il ne connaît
ni CoinGecko, ni la structure du payload du matin, ni le portefeuille. Ce
module fait la traduction, et **rien d'autre**.

RÈGLE UNIQUE, NON NÉGOCIABLE
============================
Une donnée absente reste ``None``. Aucun repli, aucune valeur « raisonnable ».

CONTRAT PRODUCTEUR → CONSOMMATEUR (audit zero-trust, 01/10)
===========================================================
Trois clés lues ici n'étaient produites par PERSONNE : ``valuation.metrics.
mc_tvl`` (le producteur écrit ``mc_tvl_ratio``), ``dev_activity.commits_90d``
(le producteur écrit ``commits_30d`` et ``last_commit_days_ago``) et
``data_freshness.age_days`` (jamais calculé). Les tests fabriquaient ces clés :
ils étaient verts sur un chemin mort en production. Chaque clé lue ici est
désormais celle que le producteur écrit, et ``tests/test_v33_contrat.py``
fait passer la sortie RÉELLE des producteurs dans cet adaptateur.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from statistics import pstdev
from typing import Any, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and math.isfinite(f) else None


def _dig(node: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def complete_days(dated: Any, today: Optional[str] = None) -> dict[str, float]:
    """Clôtures datées de JOURNÉES COMPLÈTES uniquement (date < aujourd'hui UTC).

    La série CoinGecko quotidienne se termine par le cours « maintenant »,
    rangé sous la date du jour : une journée partielle. La garder ajoutait un
    rendement de quelques heures au milieu de rendements journaliers.
    """
    if not isinstance(dated, dict):
        return {}
    today = today or _today_utc()
    out: dict[str, float] = {}
    for d, v in dated.items():
        f = _num(v)
        if isinstance(d, str) and len(d) >= 10 and d[:10] < today and f and f > 0:
            out[d[:10]] = f
    return out


def dated_log_returns(dated: dict[str, float]) -> dict[str, float]:
    """Log-rendements quotidiens indexés par la date de FIN, jours consécutifs
    uniquement (un trou de données ne fabrique pas un rendement sur 2 jours)."""
    out: dict[str, float] = {}
    jours = sorted(dated)
    for prev, cur in zip(jours, jours[1:]):
        try:
            d0 = datetime.strptime(prev, "%Y-%m-%d")
            d1 = datetime.strptime(cur, "%Y-%m-%d")
        except ValueError:
            continue
        if (d1 - d0).days != 1:
            continue
        out[cur] = math.log(dated[cur] / dated[prev])
    return out


def daily_volatility_pct(closes: Any) -> Optional[float]:
    """Écart-type des log-rendements quotidiens, en %.

    Accepte une série datée ``{date: clôture}`` (chemin de production,
    journées complètes) ou une liste chronologique (rétro-compatibilité).
    Au moins 20 rendements : en dessous, l'écart-type d'un actif crypto est un
    bruit, et on renvoie ``None`` (que le moteur pénalise).
    """
    if isinstance(closes, dict):
        rets = list(dated_log_returns(complete_days(closes)).values())
    else:
        rets = []
        for prev, cur in zip(closes or [], (closes or [])[1:]):
            p, c = _num(prev), _num(cur)
            if p is None or c is None or p <= 0 or c <= 0:
                continue
            rets.append(math.log(c / p))
    if len(rets) < 20:
        return None
    return round(pstdev(rets) * 100.0, 4)


def _dev_active(entry: dict[str, Any]) -> Optional[bool]:
    """Activité de développement MESURÉE (producteur : ``github_dev``).

    Le producteur écrit ``commits_30d`` et ``last_commit_days_ago``. Un actif
    est « inactif » seulement si son DERNIER commit date de plus de 90 jours —
    ``commits_30d == 0`` seul ne prouve pas 90 jours de silence.
    """
    dev = entry.get("dev_activity")
    if not isinstance(dev, dict) or not dev.get("available"):
        return None
    last = _num(dev.get("last_commit_days_ago"))
    if last is not None:
        return last <= 90
    c30 = _num(dev.get("commits_30d"))
    if c30 is not None and c30 > 0:
        return True
    return None


def build_candidate(
    entry: dict[str, Any],
    *,
    ptf_value_usd: Optional[float] = None,
    closes: Any = None,
    volume_24h: Optional[float] = None,
    portfolio_returns: Optional[dict[str, float]] = None,
    mvrv_history: Optional[dict[str, Any]] = None,
    mc_tvl_peers: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Traduit UNE entrée en candidat normalisé, provenance tracée par champ."""
    sym = str(entry.get("asset") or "?").upper()
    sources: dict[str, str] = {}

    val, ptf = _num(entry.get("value_usd")), _num(ptf_value_usd)
    weight = (val / ptf * 100.0) if (val is not None and ptf and ptf > 0) else None
    sources["weight_pct"] = ("value_usd / portefeuille" if weight is not None
                             else "ABSENT")

    vol = daily_volatility_pct(closes)
    sources["daily_vol_pct"] = ("écart-type des log-rendements (journées "
                                "complètes)" if vol is not None else "ABSENT")

    beta = beta_vs_portfolio(closes, portfolio_returns or {})
    sources["beta_portfolio"] = ("régression datée sur le portefeuille ex-soi"
                                 if beta is not None else "ABSENT")

    mcap, vol24 = _num(entry.get("market_cap")), _num(volume_24h)
    liq = (vol24 / mcap) if (vol24 is not None and mcap and mcap > 0) else None
    sources["volume_to_mcap"] = ("volume 24 h / capitalisation (CoinGecko)"
                                 if liq is not None else "ABSENT")
    sources["volume_24h_usd"] = ("volume 24 h (CoinGecko)"
                                 if vol24 is not None and vol24 > 0 else "ABSENT")

    dil = _num(_dig(entry, "valuation", "metrics", "dilution_remaining_pct"))
    sources["dilution_remaining_pct"] = ("valuation.metrics (max_supply)"
                                         if dil is not None else "ABSENT")

    cov_pct = _num(_dig(entry, "thesis_scoring", "completeness", "pct"))
    cov = (cov_pct / 100.0) if cov_pct is not None else None
    sources["coverage"] = ("thesis_scoring.completeness"
                           if cov is not None else "ABSENT")

    oca = entry.get("onchain_advanced")
    oca = oca if isinstance(oca, dict) else {}
    mvrv = _num(oca.get("mvrv"))
    sources["mvrv"] = ("onchain_advanced (Coin Metrics)" if mvrv is not None
                       else "ABSENT — couvert pour BTC/ETH seulement")
    mvrv_stale = bool(oca.get("stale")) if mvrv is not None else None

    mc_tvl = _num(_dig(entry, "valuation", "metrics", "mc_tvl_ratio"))
    sources["mc_tvl"] = ("valuation.metrics.mc_tvl_ratio (CoinGecko / "
                         "DeFiLlama)" if mc_tvl is not None else "ABSENT")

    dev_active = _dev_active(entry)
    sources["dev_active"] = ("dev_activity (GitHub)" if dev_active is not None
                             else "ABSENT")

    return {
        "asset": sym,
        "weight_pct": weight,
        "value_usd": val,
        "price": _num(entry.get("price")),
        "daily_vol_pct": vol,
        "beta_portfolio": beta,
        "volume_to_mcap": liq,
        "volume_24h_usd": vol24 if (vol24 is not None and vol24 > 0) else None,
        "dilution_remaining_pct": dil,
        "coverage": cov,
        "mvrv": mvrv,
        "mvrv_stale": mvrv_stale,
        "mvrv_history": mvrv_history if mvrv is not None else None,
        "mc_tvl": mc_tvl,
        "mc_tvl_peers": mc_tvl_peers if mc_tvl is not None else None,
        "dev_active": dev_active,
        "sources": sources,
    }


def build_candidates(
    eligible: list[dict[str, Any]],
    *,
    ptf_value_usd: Optional[float] = None,
    closes_by_asset: Optional[dict[str, Any]] = None,
    volume_by_asset: Optional[dict[str, float]] = None,
    mvrv_history: Optional[dict[str, Any]] = None,
    peers_by_asset: Optional[dict[str, dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Traduit tout l'univers. Best-effort : une entrée illisible est ignorée."""
    out: list[dict[str, Any]] = []
    closes_by_asset = closes_by_asset or {}
    volume_by_asset = volume_by_asset or {}
    mvrv_history = mvrv_history or {}
    peers_by_asset = peers_by_asset or {}
    poids: dict[str, float] = {}
    for e in (eligible or []):
        if isinstance(e, dict) and e.get("asset"):
            v = _num(e.get("value_usd"))
            if v is not None and v > 0:
                poids[str(e["asset"]).upper()] = v
    rets_by_asset = {str(a).upper(): dated_log_returns(complete_days(c))
                     for a, c in closes_by_asset.items() if isinstance(c, dict)}
    for entry in (eligible or []):
        if not isinstance(entry, dict) or not entry.get("asset"):
            continue
        sym = str(entry["asset"]).upper()
        try:
            # ── EX-SOI : β mesuré contre le portefeuille PRIVÉ de l'actif (un
            # actif pesant 83 % obtenait β = 1,124 contre 0,891 ex-soi).
            ref = portfolio_return_series(
                {a: r for a, r in rets_by_asset.items() if a != sym},
                {a: w for a, w in poids.items() if a != sym})
            out.append(build_candidate(
                entry, ptf_value_usd=ptf_value_usd,
                closes=closes_by_asset.get(sym),
                volume_24h=volume_by_asset.get(sym),
                portfolio_returns=ref,
                mvrv_history=mvrv_history.get(sym),
                mc_tvl_peers=peers_by_asset.get(sym)))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Candidat %s ignoré : %s", sym, exc)
    return out


def coverage_report(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Combien d'actifs disposent réellement de chaque grandeur.

    Rendu dans le mail : c'est ce qui permet de LIRE que le moteur ne peut pas
    trancher sur la majorité des lignes, au lieu de le déduire d'une absence de
    recommandation.
    """
    champs = ("daily_vol_pct", "beta_portfolio", "volume_to_mcap", "volume_24h_usd",
              "dilution_remaining_pct", "coverage", "mvrv", "mc_tvl",
              "dev_active")
    total = len(candidates)
    return {
        "total": total,
        "par_champ": {
            f: sum(1 for c in candidates if c.get(f) is not None)
            for f in champs
        },
        "avec_ancrage_valorisation": sum(
            1 for c in candidates
            if c.get("mvrv") is not None or c.get("mc_tvl") is not None),
    }


# ── application de la décision déterministe aux thèses ────────────────────

_FIRM = ("RENFORC", "ALLÉG", "ALLEG", "ACCUMUL", "BUY", "SELL", "MAINTENIR")

# Le modèle signale une contradiction majeure via l'un de ces champs. Seule
# une contradiction explicitement marquée MAJEURE porte le veto : une réserve
# ordinaire est une nuance de rédaction, pas un motif de retrait.
_CHAMPS_CONTRADICTION = ("contradictions", "contradictions_majeures",
                         "blocking_contradictions")

# Champs CHIFFRÉS qu'une thèse rédigée par le modèle de langage peut porter.
# Sur une posture ferme, AUCUN ne survit : « le LLM ne peut pas modifier un
# chiffre déterministe, choisir le sizing, fabriquer une probabilité » (Omar).
# Audit zero-trust (01/10) : la confiance du modèle s'affichait encore
# « RENFORCER (C.41%) » à côté d'une décision du moteur, conditionnait sa
# fiche détaillée et sa persistance au suivi (seuil 55).
_CHAMPS_CHIFFRES_LLM = ("confidence", "targets", "rr_value", "rr_favorable",
                        "size_note", "real_confidence_pct", "probability_pct",
                        "ev_30d_pct", "expected_value",
                        # prix recopiés, ancien score V30 (« score 9 · seuil
                        # 2 »), statistiques chartistes recopiées par le modèle
                        "price_line", "signals_summary", "historical_pattern")


_CHAMPS_RESERVES = ("engine_view", "v33_decision", "v33_trigger", "v33_reason",
                    "forecast_view", "_v33_gated", "v33_sans_narration",
                    "demoted_by_python", "demotion_reason", "_expand",
                    "asset_plan", "plan_line", "current_price", "gate_note",
                    "_gated", "conviction_delta", "sector_rank", "ct_warning")


def _contradictions_majeures(t: dict[str, Any]) -> list[str]:
    """Contradictions MAJEURES signalées par le modèle sur cette thèse.

    Le veto ne se déclenche pas sur du texte libre : il faut une entrée
    structurée et explicitement qualifiée de majeure. Omar l'autorise : « le
    LLM peut retirer une recommandation en supprimant une preuve narrative,
    mais ne peut jamais en créer une que le moteur refuse ».
    """
    out: list[str] = []
    for champ in _CHAMPS_CONTRADICTION:
        val = t.get(champ)
        if isinstance(val, dict):
            val = [val]
        if not isinstance(val, list):
            continue
        for item in val:
            if isinstance(item, dict):
                niveau = str(item.get("severity") or item.get("niveau")
                             or "").lower()
                texte = str(item.get("text") or item.get("texte")
                            or item.get("label") or "").strip()
                if niveau in ("majeure", "major", "bloquante", "blocking") and texte:
                    out.append(texte)
    return out


def _is_firm(action: Any) -> bool:
    a = str(action or "").upper()
    return any(k in a for k in _FIRM)


def verrouiller_postures_fermes(theses: Any) -> list[str]:
    """Aucune posture ferme sans porteur déterministe (fail-closed).

    RENFORCER n'existe que décidé par le moteur (``engine_view``), ALLÉGER que
    déclenché par le radar de sortie (``v33_trigger``). Toute autre posture
    ferme — reste d'une application interrompue, posture du modèle — devient
    SURVEILLER, sans ses chiffres. Mutation in-place.
    """
    fixes: list[str] = []
    for t in theses if isinstance(theses, list) else []:
        if not isinstance(t, dict) or not _is_firm(t.get("action")):
            continue
        act = str(t.get("action") or "").upper()
        if t.get("engine_view") and "RENFORC" in act:
            continue
        if t.get("v33_trigger") and ("ALLÉG" in act or "ALLEG" in act):
            continue
        t["action"] = "SURVEILLER"
        t["_v33_gated"] = t.get("_v33_gated") or "sans_porteur"
        t.setdefault("gate_note", "posture ferme sans décision du moteur ni "
                                  "règle de prise de profit — retirée")
        for k in _CHAMPS_CHIFFRES_LLM:
            t.pop(k, None)
        t["action_plan"] = {}
        fixes.append(f"{t.get('asset')} : {act} sans porteur → SURVEILLER")
    return fixes


def _purge_chiffres_llm(t: dict[str, Any]) -> None:
    """Retire de la thèse tout nombre décisionnel produit par le modèle."""
    for k in _CHAMPS_CHIFFRES_LLM:
        t.pop(k, None)
    t["action_plan"] = {}


def _forecast_view(c: Optional[dict[str, Any]]) -> dict[str, Any]:
    from src.analytics.forecast import distribution, forecast_display
    c = c or {}
    view = {"price": _num(c.get("price")),
            "forecast_30d": distribution(c.get("price"), c.get("daily_vol_pct"), 30),
            "forecast_365d": distribution(c.get("price"), c.get("daily_vol_pct"), 365)}
    view["display"] = forecast_display(view)
    return view


def _imposer_decision(t: dict[str, Any], dec: dict[str, Any],
                      cand: Optional[dict[str, Any]]) -> None:
    """Écrit la décision du moteur dans la thèse — seuls ses chiffres restent."""
    from src.analytics.forecast import engine_view
    view = engine_view(dec, cand)
    _purge_chiffres_llm(t)
    t["action"] = dec.get("action") or "RENFORCER"
    t["action_type"] = "bullish"
    t["thesis_type"] = ("tactical" if dec.get("channel") == "tactique"
                        else "conviction")
    t["engine_view"] = view
    t["v33_decision"] = dec
    t["v33_reason"] = dec.get("reason")
    if view.get("price") is not None:
        t["current_price"] = view["price"]
    if view.get("size_pct") is not None:
        band = view.get("size_band") or [view["size_pct"], view["size_pct"]]
        t["action_plan"] = {
            "entry": view.get("price"),
            "position_size_pct": view["size_pct"],
            "position_size_usd": view.get("size_usd"),
            "sizing_note": (
                f"bande {view.get('size_band_label')} "
                f"{'cœur' if view.get('size_tier') == 'core' else 'satellite'} "
                f"{band[0]:g}–{band[1]:g} %".replace(".", ",")),
        }


def apply_decisions_to_theses(
    payload: dict[str, Any], data: dict[str, Any]
) -> list[str]:
    """Le moteur est SOUVERAIN sur l'action et sur les chiffres. Mutation in-place.

    * une thèse ferme dont l'actif n'a PAS de décision du moteur redevient
      ``SURVEILLER`` (non rendue), avec le motif exact du refus ;
    * une décision du moteur impose ``RENFORCER``, SA taille et SES chiffres
      (potentiel, requis, fourchettes) — la thèse du modèle n'en garde que la
      prose ;
    * un ALLÉGER n'existe que si le radar de sortie (règles de prise de profit
      d'Omar) l'a déclenché ; ses chiffres sont ceux du radar et de Python ;
    * un MAINTENIR écrit par le modèle n'est pas une posture du moteur : il
      redevient ``SURVEILLER`` ;
    * la confiance du modèle disparaît des postures fermes : elle n'est plus
      ni affichée, ni décisionnelle, ni persistée.

    Idempotent : un second appel ne change rien.

    Returns:
        Journal des ajustements (pour le log et l'audit).
    """
    fixes: list[str] = []
    theses = payload.get("thesis_of_the_day")
    if not isinstance(theses, list):
        theses = []
        payload["thesis_of_the_day"] = theses
    # Champs RÉSERVÉS au moteur et à Python : s'ils arrivent dans une thèse du
    # modèle, ils sont retirés avant toute décision (un modèle qui écrirait
    # lui-même un « engine_view » se ferait passer pour le moteur).
    for t in theses:
        if isinstance(t, dict):
            for k in _CHAMPS_RESERVES:
                t.pop(k, None)

    opp = data.get("opportunity") if isinstance(data.get("opportunity"), dict) else {}
    if not opp.get("available"):
        for t in theses:
            if isinstance(t, dict) and _is_firm(t.get("action")):
                t["action"] = "SURVEILLER"
                t["_v33_gated"] = "moteur_indisponible"
                t["gate_note"] = ("moteur d'opportunité indisponible — aucune "
                                  "recommandation ferme ne peut être établie")
                fixes.append(f"{t.get('asset')} : ferme → SURVEILLER (moteur KO)")
        return fixes

    by_asset = {str(d.get("asset") or "").upper(): d
                for d in (opp.get("decisions") or []) if isinstance(d, dict)}
    firm_by_asset = {str(d.get("asset") or "").upper(): d
                     for d in (opp.get("firm") or []) if isinstance(d, dict)}
    reduce_by_asset = {str(r.get("asset") or "").upper(): r
                       for r in (opp.get("reduce_firm") or [])
                       if isinstance(r, dict)}
    cand_by_asset = {str(c.get("asset") or "").upper(): c
                     for c in (opp.get("candidates") or []) if isinstance(c, dict)}

    _meta_px = {str(e.get("asset") or "").upper(): e.get("price")
                for e in (data.get("eligible_theses") or []) if isinstance(e, dict)}

    _meta_manque = {str(e.get("asset") or "").upper():
                    (((e.get("thesis_scoring") or {}).get("completeness") or {})
                     .get("missing") or [])
                    for e in (data.get("eligible_theses") or []) if isinstance(e, dict)}

    def _candidat(sym: str) -> dict[str, Any]:
        c = dict(cand_by_asset.get(sym) or {})
        if _num(c.get("price")) is None and _num(_meta_px.get(sym)) is not None:
            c["price"] = _num(_meta_px.get(sym))
        return c

    def _completer_vue(t: dict[str, Any], sym: str) -> None:
        if isinstance(t.get("engine_view"), dict):
            t["engine_view"]["coverage_missing"] = list(_meta_manque.get(sym) or [])

    for t in theses:
        if not isinstance(t, dict):
            continue
        sym = str(t.get("asset") or "").upper()
        act = str(t.get("action") or "").upper()

        if "ALLÉG" in act or "ALLEG" in act or "SELL" in act:
            red = reduce_by_asset.get(sym)
            if red:
                _purge_chiffres_llm(t)
                t["action"] = "ALLÉGER"
                t["action_type"] = "bearish"
                t["v33_decision"] = red
                t["v33_reason"] = red.get("reason")
                t["v33_trigger"] = red.get("trigger")
                t["forecast_view"] = _forecast_view(cand_by_asset.get(sym))
            else:
                t["action"] = "SURVEILLER"
                t["_v33_gated"] = "aucun_declencheur"
                t["gate_note"] = (
                    "aucune règle de prise de profit déclenchée — alléger exige "
                    "un motif nommé (radar de sortie), pas un récit")
                fixes.append(f"{sym} : ALLÉGER → SURVEILLER (aucun déclencheur)")
            continue

        dec = firm_by_asset.get(sym)
        if dec:
            _contra = _contradictions_majeures(t)
            if _contra:
                t["action"] = "SURVEILLER"
                t["_v33_gated"] = "contradiction"
                t["v33_decision"] = dec
                t["gate_note"] = (
                    "décision arithmétique valide, mais contradiction majeure "
                    "signalée à l'analyse : " + " · ".join(_contra))
                fixes.append(f"{sym} : veto qualitatif — {_contra[0][:60]}")
                continue
            if not t.get("engine_view"):
                fixes.append(f"{sym} : décision du moteur appliquée")
            _imposer_decision(t, dec, _candidat(sym))
            _completer_vue(t, sym)
            continue

        if _is_firm(act):
            refus = by_asset.get(sym)
            t["action"] = "SURVEILLER"
            t["_v33_gated"] = (refus or {}).get("condition_failed") or "hors_univers"
            t["gate_note"] = ((refus or {}).get("reason")
                              or "actif hors de l'univers évalué par le moteur")
            t["v33_decision"] = refus
            for k in _CHAMPS_CHIFFRES_LLM:
                t.pop(k, None)
            fixes.append(
                f"{sym} : {act or '?'} → SURVEILLER "
                f"({(refus or {}).get('condition_failed') or 'hors univers'})")

    # ── AUTONOMIE : une décision du moteur atteint le mail même si le modèle
    # de langage n'a rien écrit sur cet actif (sinon le silence du modèle
    # suffirait à effacer une recommandation).
    presents = {str(t.get("asset") or "").upper() for t in theses
                if isinstance(t, dict)}
    meta = {str(e.get("asset") or "").upper(): e
            for e in (data.get("eligible_theses") or [])
            if isinstance(e, dict)}
    for sym, dec in firm_by_asset.items():
        if not sym or sym in presents:
            continue
        e = meta.get(sym) or {}
        _nom = e.get("name")
        t = {"asset": sym, "name": (_nom if _nom and str(_nom).upper() != sym else None),
             "tier_label": e.get("tier_label") or "",
             "reasoning_signals": [], "v33_sans_narration": True}
        _imposer_decision(t, dec, _candidat(sym))
        _completer_vue(t, sym)
        theses.append(t)
        presents.add(sym)
        fixes.append(f"{sym} : thèse injectée (décision du moteur, "
                     "aucune narration du modèle)")

    if fixes:
        logger.info("Moteur v33 souverain : %d ajustement(s) — %s",
                    len(fixes), " | ".join(fixes))
    return fixes


# ── bêta contre le PORTEFEUILLE — information de concentration ────────────

def portfolio_return_series(
    rets_by_asset: dict[str, dict[str, float]],
    weights_by_asset: dict[str, float],
) -> dict[str, float]:
    """Rendements quotidiens du PORTEFEUILLE, alignés PAR DATE.

    Audit zero-trust (01/10) : les séries étaient alignées « par la fin ». Or
    certaines contenaient le point « maintenant » et d'autres non : décalées
    d'un jour, elles donnaient β(BTC) = −0,05 et β(QNT) = −0,66 le 30/09. On
    aligne désormais sur les DATES communes ; un historique trop court pour la
    fenêtre commune est retiré de la référence plutôt que de la tronquer pour
    tous.
    """
    series = {a: r for a, r in (rets_by_asset or {}).items()
              if r and (_num(weights_by_asset.get(a)) or 0.0) > 0}
    if not series:
        return {}
    longueurs = sorted(len(r) for r in series.values())
    mediane = longueurs[len(longueurs) // 2]
    series = {a: r for a, r in series.items() if len(r) >= min(mediane, 60)}
    if not series:
        return {}
    communes = set.intersection(*(set(r) for r in series.values()))
    if len(communes) < 20:
        return {}
    total = sum(_num(weights_by_asset.get(a)) or 0.0 for a in series)
    if total <= 0:
        return {}
    return {d: sum((_num(weights_by_asset.get(a)) or 0.0) / total * r[d]
                   for a, r in series.items())
            for d in sorted(communes)}


def beta_vs_portfolio(
    closes: Any, portfolio_returns: dict[str, float]
) -> Optional[float]:
    """β de l'actif contre le portefeuille ex-soi — MESURÉ sur dates communes.

    Information de concentration (redondance avec ce qui est déjà détenu),
    publiée à côté de la décision. Ni plafond, ni prix du risque : « pas de
    hard cap d'exposition » (Omar, 01/10).

    Returns:
        β, ou ``None`` sous 20 dates communes.
    """
    if not isinstance(closes, dict) or not portfolio_returns:
        return None
    r_a = dated_log_returns(complete_days(closes))
    dates = sorted(set(r_a) & set(portfolio_returns))
    n = len(dates)
    if n < 20:
        return None
    a = [r_a[d] for d in dates]
    p = [portfolio_returns[d] for d in dates]
    mp = sum(p) / n
    var = sum((x - mp) ** 2 for x in p) / n
    if var <= 0:
        return None
    ma = sum(a) / n
    cov = sum((x - ma) * (y - mp) for x, y in zip(a, p)) / n
    beta = cov / var
    return round(beta, 4) if math.isfinite(beta) else None
