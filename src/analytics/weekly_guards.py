"""Gardes déterministes du rapport HEBDO (v26).

L'audit du weekly v25 a montré que les règles de prompt seules ne suffisent
pas : le LLM a réinventé la perf hebdo (« +2.32% » vs +3.8% Python), cité un
« vs BTC » divisé par 10, gardé un ordre de SORTIE sur un actif sous reco
RENFORCER active, halluciné un drawdown ATH (JASMY −99.9%) et recyclé des
narratifs périmés (« transition vers ETH 2.0 en cours »). Ce module applique
donc les corrections EN PYTHON, après génération : chaque garde est pure,
best-effort (jamais bloquante) et renvoie la liste des corrections opérées
pour le log.

Convention : tous les helpers acceptent des payloads partiels/None et
retournent silencieusement l'entrée inchangée si la donnée de référence
manque (pas de sur-correction sans source de vérité).
"""

from __future__ import annotations

import re
from typing import Any, Optional

# ── helpers ──────────────────────────────────────────────────────────────

_NUM = r"[+\-−]?\d{1,3}(?:[.,]\d+)?"


def _to_float(token: str) -> Optional[float]:
    """« +2,32 » / « −0.03 » → float (signe − typographique accepté)."""
    try:
        return float(token.replace("−", "-").replace(",", "."))
    except (ValueError, AttributeError):
        return None


def _fmt_pct(v: float) -> str:
    """Format canonique aligné sur les KPI du template (+3.8% / −0.25%)."""
    s = f"{v:+.2f}".rstrip("0").rstrip(".")
    if s in ("+", "-"):  # v == 0 arrondi
        s = "+0"
    return s.replace("-", "−") + "%"


def _bullet_text(b: Any) -> Optional[str]:
    if isinstance(b, str):
        return b
    if isinstance(b, dict) and isinstance(b.get("text"), str):
        return b["text"]
    return None


def _set_bullet_text(b: Any, text: str) -> Any:
    if isinstance(b, dict):
        b = dict(b)
        b["text"] = text
        return b
    return text


# ── A1/B1 — chiffres du bilan verrouillés sur le snapshot Python ─────────

def enforce_summary_figures(
    bullets: Any,
    snapshot: Optional[dict[str, Any]],
    fear_greed_value: Any = None,
    fear_greed_7d_ago: Any = None,
) -> tuple[Any, list[str]]:
    """Force la perf hebdo / vs BTC / F&G du bilan sur les valeurs Python.

    Le LLM ne peut plus afficher « +2.32% sur la semaine » quand le KPI
    déterministe dit +3.8% : tout pourcentage identifié comme perf hebdo du
    PTF (ou « vs BTC », ou valeur F&G) divergeant de la source de vérité est
    RÉÉCRIT avec la valeur exacte. Fenêtre de tolérance 0,15 pt (arrondis).

    v28 (W-A1) — l'ÉVOLUTION F&G est aussi verrouillée sur la série API :
    « F&G X → Y » (les DEUX bornes) et « rebondi de N points » sont réécrits
    depuis ``fear_greed_7d_ago``/``fear_greed_value``. Le 07/07, le hebdo
    citait 24, 23 ET 15 pour « il y a 7 j » selon la section — la série 8 j
    d'alternative.me (15) est la seule source de vérité.

    Returns:
        (bullets corrigés, liste des corrections pour le log).
    """
    fixes: list[str] = []
    if not isinstance(bullets, list) or not isinstance(snapshot, dict):
        return bullets, fixes
    true_pnl = snapshot.get("weekly_pnl_pct")
    true_vsbtc = snapshot.get("vs_btc_7d_pct")

    _pct_re = re.compile(rf"({_NUM})\s?%")
    _ptf_ctx = re.compile(r"(?i)(performance|perf|p&l|portefeuille|ptf|gagné|progress)")
    _week_ctx = re.compile(r"(?i)(semaine|hebdo|7\s?j)")
    _vsbtc_ctx = re.compile(r"(?i)vs\s+btc|face\s+à\s+bitcoin|contre\s+btc")
    # Audit v26 final — « La performance de BTC cette semaine : +2.1% » ne doit
    # JAMAIS être réécrite en perf PTF : si un actif/indice NOMMÉ précède le
    # token de près, le % lui appartient (la branche « vs BTC » reste prioritaire).
    _asset_near = re.compile(
        r"(?i)\b(BTC|ETH|bitcoin|ethereum|solana|S\s?&\s?P|nasdaq|dax|nikkei|"
        r"gold|dominance)\b")
    _fg_prefix = r"(?:F\s?&\s?G|Fear\s*(?:&|and)\s*Greed)"
    # v28 — le lookahead négatif épargne la 1re borne d'une évolution « X → Y »
    # (sinon la garde v26 aurait cassé « F&G 15 → 27 » en « 27 → 27 »).
    _fg_re = re.compile(
        rf"(?i)({_fg_prefix}[^0-9%]{{0,20}})(\d{{1,3}})\b(?!\s*(?:→|->|—>)\s*\d)")
    _fg_evo_re = re.compile(
        rf"(?i)({_fg_prefix}[^0-9%]{{0,20}})(\d{{1,3}})(\s*(?:→|->|—>)\s*)(\d{{1,3}})\b")
    _fg_pts_re = re.compile(
        r"(?i)(rebondi|remonté|gagné|repris|progressé|bondi|grimpé|perdu|chuté|"
        r"cédé|reculé)\s+de\s+(\d{1,3})\s*(points?|pts)")
    _fg_ctx = re.compile(r"(?i)F\s?&\s?G|fear|greed|sentiment|peur|avidité")

    out: list[Any] = []
    for b in bullets:
        text = _bullet_text(b)
        if text is None:
            out.append(b)
            continue
        new = text
        # v28 (W-A1) — évolution « F&G X → Y » : les deux bornes verrouillées.
        if isinstance(fear_greed_value, (int, float)):
            def _fg_evo_fix(m: re.Match) -> str:
                start = _to_float(m.group(2))
                end = _to_float(m.group(4))
                want_start = (int(fear_greed_7d_ago)
                              if isinstance(fear_greed_7d_ago, (int, float))
                              else (int(start) if start is not None else None))
                changed = False
                if end is not None and int(end) != int(fear_greed_value):
                    changed = True
                if (want_start is not None and start is not None
                        and int(start) != want_start):
                    changed = True
                if changed and want_start is not None:
                    fixes.append(
                        f"évolution F&G {m.group(2)}→{m.group(4)} "
                        f"→ {want_start}→{int(fear_greed_value)}")
                    return f"{m.group(1)}{want_start}{m.group(3)}{int(fear_greed_value)}"
                return m.group(0)
            new = _fg_evo_re.sub(_fg_evo_fix, new)
        # F&G : une seule valeur autorisée (celle de data.fear_greed).
        if isinstance(fear_greed_value, (int, float)):
            def _fg_fix(m: re.Match) -> str:
                cited = _to_float(m.group(2))
                if cited is not None and int(cited) != int(fear_greed_value):
                    fixes.append(f"F&G {m.group(2)} → {int(fear_greed_value)}")
                    return f"{m.group(1)}{int(fear_greed_value)}"
                return m.group(0)
            new = _fg_re.sub(_fg_fix, new)
        # v28 (W-A1) — « rebondi de N points » (contexte sentiment) : N et le
        # verbe verrouillés sur delta_7d = value − value_7d_ago de la série.
        if (isinstance(fear_greed_value, (int, float))
                and isinstance(fear_greed_7d_ago, (int, float))
                and _fg_ctx.search(new)):
            _delta_fg = int(fear_greed_value) - int(fear_greed_7d_ago)

            def _fg_pts_fix(m: re.Match) -> str:
                cited = _to_float(m.group(2))
                verb_up = m.group(1).lower() in (
                    "rebondi", "remonté", "gagné", "repris", "progressé",
                    "bondi", "grimpé")
                if cited is None:
                    return m.group(0)
                if int(cited) == abs(_delta_fg) and verb_up == (_delta_fg >= 0):
                    return m.group(0)
                verb = "rebondi" if _delta_fg >= 0 else "reculé"
                fixes.append(
                    f"F&G « {m.group(0)} » → « {verb} de {abs(_delta_fg)} points »")
                return f"{verb} de {abs(_delta_fg)} {m.group(3)}"
            new = _fg_pts_re.sub(_fg_pts_fix, new)
        # Perf hebdo PTF + vs BTC : remplacement token par token, en contexte.
        if isinstance(true_pnl, (int, float)) or isinstance(true_vsbtc, (int, float)):
            result: list[str] = []
            last = 0
            for m in _pct_re.finditer(new):
                val = _to_float(m.group(1))
                before = new[max(0, m.start() - 70):m.start()]
                near = new[max(0, m.start() - 30):m.start()]
                replacement = None
                if val is not None:
                    if (_vsbtc_ctx.search(near)
                            or _vsbtc_ctx.search(new[m.end():m.end() + 25])):
                        if (isinstance(true_vsbtc, (int, float))
                                and abs(val - true_vsbtc) > 0.15):
                            replacement = _fmt_pct(true_vsbtc)
                            fixes.append(f"vs BTC {m.group(0)} → {replacement}")
                    elif (_ptf_ctx.search(before) and _week_ctx.search(new)
                          and isinstance(true_pnl, (int, float))
                          and abs(val - true_pnl) > 0.15
                          # garde-fou : on ne réécrit que des ordres de grandeur
                          # de perf PTF plausibles (pas un « 67% du PTF »).
                          and abs(val) < 25
                          # garde-fou : pas d'actif/indice nommé juste avant.
                          and not _asset_near.search(near)):
                        replacement = _fmt_pct(true_pnl)
                        fixes.append(f"perf hebdo {m.group(0)} → {replacement}")
                result.append(new[last:m.start()])
                result.append(replacement if replacement else m.group(0))
                last = m.end()
            result.append(new[last:])
            new = "".join(result)
        out.append(_set_bullet_text(b, new) if new != text else b)
    return out, fixes


# ── A9 — indices actions : « −16.13 points » → % 7j réel ─────────────────

_EQUITY_ALIASES = {
    "sp500": r"S\s?&\s?P\s?500|S&P",
    "nasdaq": r"Nasdaq",
    "stoxx50": r"Stoxx\s?50|Euro\s?Stoxx",
    "dax": r"DAX",
    "nikkei": r"Nikkei",
    "gold": r"[Oo]r\b|[Gg]old",
}


def fix_equity_points(text: str, markets_week_pct: Optional[dict[str, Any]]) -> tuple[str, list[str]]:
    """Remplace « ±N points » cité pour un indice par sa vraie perf 7j en %.

    Des « points » sans base ne veulent rien dire (−16 pts de S&P ≈ −0,3%) et
    la fenêtre restait ambiguë. Sans % 7j disponible pour l'indice, le token
    est laissé tel quel (le prompt interdit déjà les points, ceci est le
    filet de sécurité).
    """
    fixes: list[str] = []
    if not isinstance(text, str) or not markets_week_pct:
        return text, fixes
    # Audit v26 final — « 25 points de base » (Fed) n'est PAS un niveau d'indice :
    # lookahead négatif pour ne jamais réécrire un taux en perf 7j d'indice.
    pat = re.compile(rf"({_NUM})\s?points?\b(?!\s+de\s+base)")

    def _fix(m: re.Match) -> str:
        before = text[max(0, m.start() - 45):m.start()]
        for key, alias in _EQUITY_ALIASES.items():
            pct = (markets_week_pct or {}).get(key)
            if isinstance(pct, (int, float)) and re.search(alias, before):
                rep = f"{_fmt_pct(pct)} (7j)"
                fixes.append(f"{key} {m.group(0)} → {rep}")
                return rep
        return m.group(0)

    return pat.sub(_fix, text), fixes


def fix_equity_points_in_bullets(
    bullets: Any, markets_week_pct: Optional[dict[str, Any]]
) -> tuple[Any, list[str]]:
    """Applique ``fix_equity_points`` à une liste de puces (str ou {text})."""
    fixes: list[str] = []
    if not isinstance(bullets, list):
        return bullets, fixes
    out = []
    for b in bullets:
        text = _bullet_text(b)
        if text is None:
            out.append(b)
            continue
        new, fx = fix_equity_points(text, markets_week_pct)
        fixes.extend(fx)
        out.append(_set_bullet_text(b, new) if new != text else b)
    return out, fixes


# ── A3 — contradiction reco RENFORCER active vs SORTIE/allègement ────────

_SELL_VERBS = re.compile(
    r"(?i)all[ée]ger?|vend(?:re|s)?|sort(?:ir|s)?|liquid(?:er|e)|r[ée]duire"
)


def active_reinforced_assets(scoring_detail: Any) -> set[str]:
    """Actifs sous reco d'ACHAT active (validée ou en cours) cette semaine."""
    out: set[str] = set()
    for r in (scoring_detail or []):
        if not isinstance(r, dict) or not r.get("asset"):
            continue
        reco = str(r.get("reco") or "").upper()
        status = str(r.get("status") or "")
        if (reco.startswith(("RENFORCER", "BUY", "ACCUMULER"))
                and status in ("in_progress", "validated")):
            out.add(str(r["asset"]).upper())
    return out


def reconcile_recos(payload: dict[str, Any], scoring_detail: Any) -> list[str]:
    """Retire watchlist SORTIE + actions d'allègement sur un actif RENFORCÉ.

    v25 : RSR était « RENFORCER +5.0% · en cours » ET « ▼ SORTIE » en
    watchlist ET « alléger 50% » au plan d'action. Une position sous reco
    d'achat active ne peut pas être à vendre dans le même mail : la reco
    active PRIME (règle v16.1 du prompt, désormais appliquée en Python).
    """
    fixes: list[str] = []
    reinforced = active_reinforced_assets(scoring_detail)
    if not reinforced or not isinstance(payload, dict):
        return fixes

    wl = payload.get("watchlist")
    if isinstance(wl, list):
        kept = []
        for w in wl:
            asset = str((w or {}).get("asset") or "").upper() if isinstance(w, dict) else ""
            direction = str((w or {}).get("direction") or "").lower() if isinstance(w, dict) else ""
            is_exit = direction not in ("entrée", "entree", "achat", "buy", "in", "entry")
            if asset in reinforced and is_exit:
                fixes.append(f"watchlist SORTIE {asset} retirée (reco RENFORCER active)")
                continue
            kept.append(w)
        payload["watchlist"] = kept

    plan = payload.get("weekly_action_plan")
    if isinstance(plan, list):
        kept_p = []
        for a in plan:
            if isinstance(a, dict):
                blob = f"{a.get('action') or ''} {a.get('rationale') or ''}"
                hit = next(
                    (s for s in reinforced
                     if re.search(rf"\b{re.escape(s)}\b", blob)
                     and _SELL_VERBS.search(blob)),
                    None,
                )
                if hit:
                    fixes.append(
                        f"plan d'action « {str(a.get('action'))[:40]}… » retiré "
                        f"({hit} sous reco RENFORCER active)")
                    continue
            kept_p.append(a)
        # Renumérote pour ne pas laisser de trou (le template affiche priority).
        for i, a in enumerate(kept_p):
            if isinstance(a, dict):
                a["priority"] = i + 1
        payload["weekly_action_plan"] = kept_p
    return fixes


# ── A5/B10 — plausibilité des drawdowns « −X% sous ATH » ─────────────────

_ATH_CLAIM = re.compile(r"[−\-–]\s?(\d{1,3}(?:[.,]\d+)?)\s?%\s?(?:sous|vs|de l['’])\s?(?:l['’])?ATH")

# Un drawdown ≥ 99,5% implique un ATH ≥ 200× le prix : c'est presque toujours
# un ATH de listing illiquide (ex. JASMY 4,99 $ sur CoinGecko) → non significatif.
SUSPECT_FROM_ATH_PCT = -99.5


def ath_is_suspect(from_ath_pct: Any) -> bool:
    """True si le drawdown vs ATH n'est pas exploitable (ATH aberrant)."""
    return isinstance(from_ath_pct, (int, float)) and from_ath_pct <= SUSPECT_FROM_ATH_PCT


def sanitize_ath_claims(
    entries: Any, ath_facts: Optional[dict[str, Any]]
) -> list[str]:
    """Réaligne chaque « −X% sous ATH » d'analyse sur le drawdown CoinGecko réel.

    v25 : « JASMY −99.9% sous ATH » (ATH implicite ≈ 4,5 $, aberrant). Deux
    gardes : (1) écart > 3 pts avec ``from_ath_pct`` réel → le chiffre est
    réécrit ; (2) ATH suspect (≥ 99,5% de drawdown = listing illiquide) → la
    mention est remplacée par un constat honnête. Mutation in-place.
    """
    fixes: list[str] = []
    if not isinstance(entries, list) or not isinstance(ath_facts, dict):
        return fixes
    for e in entries:
        if not isinstance(e, dict):
            continue
        asset = str(e.get("asset") or "").upper()
        text = e.get("analysis")
        if not asset or not isinstance(text, str):
            continue
        fact = ath_facts.get(asset) or {}
        real = fact.get("from_ath_pct")

        def _fix(m: re.Match) -> str:
            claim = _to_float(m.group(1))
            if not isinstance(real, (int, float)) or claim is None:
                return m.group(0)
            if ath_is_suspect(real):
                fixes.append(f"{asset} : drawdown ATH suspect ({real}%) → mention neutralisée")
                return "ATH de référence peu significatif (listing illiquide)"
            if abs(claim - abs(real)) > 3.0:
                fixes.append(f"{asset} : −{m.group(1)}% sous ATH → {real}%")
                return f"−{abs(real):.1f}% sous ATH".replace(".", ",")
            return m.group(0)

        new = _ATH_CLAIM.sub(_fix, text)
        if new != text:
            e["analysis"] = new
    return fixes


# ── A10/B10 — narratifs périmés + MVRV cross-actif ───────────────────────

_STALE_NARRATIVES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)transition vers (?:l['’])?(?:ETH|Ethereum)\s?2\.0 en cours"),
     "roadmap post-Merge (scaling L2) en cours"),
    (re.compile(r"(?i)(?:l['’])?(?:ETH|Ethereum)\s?2\.0"), "post-Merge"),
]


def scrub_stale_text(text: str) -> tuple[str, list[str]]:
    """Remplace les narratifs factuellement périmés (le Merge date de 2022)."""
    fixes: list[str] = []
    if not isinstance(text, str):
        return text, fixes
    out = text
    for pat, rep in _STALE_NARRATIVES:
        if pat.search(out):
            fixes.append(f"narratif périmé « {pat.pattern[:30]} » remplacé")
            out = pat.sub(rep, out)
    return out, fixes


def scrub_stale_narratives(payload: dict[str, Any]) -> list[str]:
    """Applique le scrub de narratifs à tous les champs texte visibles du hebdo."""
    fixes: list[str] = []
    if not isinstance(payload, dict):
        return fixes

    def _walk(node: Any) -> Any:
        if isinstance(node, str):
            new, fx = scrub_stale_text(node)
            fixes.extend(fx)
            return new
        if isinstance(node, list):
            return [_walk(x) for x in node]
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        return node

    for key in ("weekly_summary", "positions_review", "long_term_positioning",
                "scenarios", "macro_panorama", "strategy_focus", "my_errors",
                "losses_vs_recos", "exit_plan", "watchlist",
                "weekly_action_plan", "concentration_reading"):
        if key in payload:
            payload[key] = _walk(payload[key])
    return fixes


# Cible le fragment « , MVRV à 1.14 (neutre) » précisément (nombre décimal +
# parenthèse de lecture optionnelle) — sans avaler le reste de la phrase.
_MVRV_FRAGMENT = re.compile(
    r",?\s*(?:le\s)?MVRV\s*(?:à|de|=)?\s*\d+(?:[.,]\d+)?(?:\s*\([^)]*\))?")


def sanitize_cross_asset_mvrv(
    entries: Any, onchain_assets: Optional[dict[str, Any]]
) -> list[str]:
    """Neutralise un MVRV cité pour un actif qui n'en a pas dans les données.

    v25 : la thèse ETH citait « MVRV à 1.14 » — la valeur BTC recopiée. Si
    l'on possède le MVRV réel de l'actif, la valeur est réécrite ; sinon le
    fragment est retiré (donnée non sourcée). BTC n'est jamais touché.
    """
    fixes: list[str] = []
    if not isinstance(entries, list):
        return fixes
    onchain_assets = onchain_assets if isinstance(onchain_assets, dict) else {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        asset = str(e.get("asset") or "").upper()
        text = e.get("analysis")
        if asset in ("", "BTC") or not isinstance(text, str) or "MVRV" not in text:
            continue
        real = (onchain_assets.get(asset) or {}).get("mvrv")
        if isinstance(real, (int, float)):
            new = re.sub(r"(MVRV[^0-9]{0,12})\d+(?:[.,]\d+)?",
                         lambda m: f"{m.group(1)}{real:.2f}".replace(".", ","),
                         text)
            if new != text:
                fixes.append(f"{asset} : MVRV réaligné sur la donnée réelle ({real:.2f})")
                e["analysis"] = new
        else:
            new = _MVRV_FRAGMENT.sub("", text).strip(" ,")
            if new != text:
                fixes.append(f"{asset} : MVRV non sourcé retiré de la thèse")
                e["analysis"] = new
    return fixes


# ── A18 — coût d'opportunité : actif détenu ≠ « absence de position » ────

def fix_held_opportunity_wording(
    text: Any, held_assets: set[str]
) -> tuple[Any, list[str]]:
    """« l'absence de reco/position sur X » → « l'absence de renfort sur X (détenu) ».

    FET était en portefeuille (1,3% PTF) : la hausse ratée est un choix de
    non-renfort, pas une opportunité externe manquée.
    """
    fixes: list[str] = []
    if not isinstance(text, str) or not held_assets:
        return text, fixes
    pat = re.compile(
        r"(?i)l['’]absence de (?:reco(?:mmandation)?|position) sur ([A-Z0-9]{2,10})")

    def _fix(m: re.Match) -> str:
        asset = m.group(1).upper()
        if asset in held_assets:
            fixes.append(f"{asset} : « absence de position/reco » → « pas de renfort (détenu) »")
            return f"l'absence de renfort sur {asset} (position déjà détenue)"
        return m.group(0)

    return pat.sub(_fix, text), fixes


# ── v28 (W-A3) — le MOT directionnel vs BTC doit suivre le SIGNE corrigé ──

_UNDERPERF_RE = re.compile(r"(?i)sous[- ]perform")
_OVERPERF_RE = re.compile(r"(?i)sur[- ]?perform")


def fix_vsbtc_direction_wording(
    bullets: Any, true_vsbtc: Any
) -> tuple[Any, list[str]]:
    """Réaligne « sous-performant / surperformant (le Bitcoin) » sur le signe réel.

    Le 07/07, la garde v26 a corrigé le CHIFFRE (−0,43% → +0,04% vs BTC) mais
    le MOT est resté : « sous-performant légèrement le Bitcoin (+0.04%) » —
    contradiction visible avec la tuile « outperform » calculée du chiffre.
    Ne touche que les puces parlant du PORTEFEUILLE face à BTC (jamais d'un
    actif tiers), et préserve la casse/flexion (participe, indicatif, nom).
    """
    fixes: list[str] = []
    if not isinstance(bullets, list) or not isinstance(true_vsbtc, (int, float)):
        return bullets, fixes
    _ptf_ctx = re.compile(r"(?i)portefeuille|\bPTF\b")
    _btc_ctx = re.compile(r"(?i)\bBTC\b|bitcoin")

    def _swap(text: str) -> str:
        if true_vsbtc >= 0:
            # sous-performant → surperformant (toutes flexions, tiret absorbé).
            return re.sub(r"(?i)sous[- ]perform", "surperform", text)
        return re.sub(r"(?i)sur[- ]?perform", "sous-perform", text)

    out: list[Any] = []
    for b in bullets:
        text = _bullet_text(b)
        if text is None or not (_ptf_ctx.search(text) and _btc_ctx.search(text)):
            out.append(b)
            continue
        wrong = (_UNDERPERF_RE.search(text) if true_vsbtc >= 0
                 else _OVERPERF_RE.search(text))
        if not wrong:
            out.append(b)
            continue
        new = _swap(text)
        fixes.append(
            f"wording vs BTC réaligné sur {'+' if true_vsbtc >= 0 else '−'}"
            f"{abs(true_vsbtc)}% (« {wrong.group(0)}… » corrigé)")
        out.append(_set_bullet_text(b, new))
    return out, fixes


# ── v28 (W-A5) — dédup des segments répétés dans les lignes ⚙ d'analyse ──

def _dedupe_segments(text: str) -> str:
    """« A, A (détail), B » → « A (détail), B » (le segment le PLUS LONG gagne).

    Le 07/07, la neutralisation ATH (garde v26) a produit « ATH de référence
    peu significatif, ATH de référence peu significatif (listing illiquide) » :
    le LLM avait déjà écrit la mention que la garde a réinjectée. On compare
    les segments normalisés (casse/espaces) : doublon exact OU préfixe l'un de
    l'autre → une seule occurrence, la plus informative, à la 1re position.
    """
    parts = [p.strip() for p in text.split(",")]
    if len(parts) < 2:
        return text
    kept: list[str] = []
    for p in parts:
        if not p:
            continue
        p_norm = p.lower()
        dup_idx = None
        for i, k in enumerate(kept):
            k_norm = k.lower()
            if (p_norm == k_norm or p_norm.startswith(k_norm)
                    or k_norm.startswith(p_norm)):
                dup_idx = i
                break
        if dup_idx is None:
            kept.append(p)
        elif len(p) > len(kept[dup_idx]):
            kept[dup_idx] = p  # garde la variante la plus détaillée
    return ", ".join(kept)


def dedupe_analysis_segments(entries: Any) -> list[str]:
    """Applique la dédup de segments aux champs ``analysis`` (mutation in-place)."""
    fixes: list[str] = []
    if not isinstance(entries, list):
        return fixes
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get("analysis"), str):
            continue
        new = _dedupe_segments(e["analysis"])
        if new != e["analysis"]:
            fixes.append(
                f"{str(e.get('asset') or '?').upper()} : segment dupliqué retiré")
            e["analysis"] = new
    return fixes


# ── v28 (W-A5) — « SYM (−99.9% ATH) » dans les TEXTES LIBRES (exit plan…) ──

_ATH_PAREN = re.compile(
    r"\b([A-Z0-9]{2,10})\s*\(\s*[−\-–]?\s*(\d{1,3}(?:[.,]\d+)?)\s*%\s*"
    r"(?:sous\s+|vs\s+)?(?:l['’])?ATH\s*\)")


def sanitize_ath_text(
    text: Any, ath_facts: Optional[dict[str, Any]]
) -> tuple[Any, list[str]]:
    """Réaligne « JASMY (-99.9% ATH) » cité dans un paragraphe libre.

    ``sanitize_ath_claims`` ne couvrait que les fiches positions (« −X% sous
    ATH ») : la section poussières du 07/07 affichait encore le drawdown
    suspect neutralisé ailleurs. Même règles : ATH suspect → mention honnête ;
    écart > 3 pts avec CoinGecko → chiffre réécrit.
    """
    fixes: list[str] = []
    if not isinstance(text, str) or not isinstance(ath_facts, dict):
        return text, fixes

    def _fix(m: re.Match) -> str:
        asset = m.group(1).upper()
        fact = ath_facts.get(asset) or {}
        real = fact.get("from_ath_pct")
        claim = _to_float(m.group(2))
        if not isinstance(real, (int, float)) or claim is None:
            return m.group(0)
        if ath_is_suspect(real):
            fixes.append(f"{asset} : drawdown ATH suspect ({real}%) → neutralisé (texte libre)")
            return f"{asset} (ATH de référence peu significatif)"
        if abs(claim - abs(real)) > 3.0:
            fixes.append(f"{asset} : ({m.group(2)}% ATH) → {real}% (texte libre)")
            _fmt = str(round(real, 1)).replace(".", ",").replace("-", "−")
            return f"{asset} ({_fmt}% vs ATH)"
        return m.group(0)

    new = _ATH_PAREN.sub(_fix, text)
    return (new if new != text else text), fixes


# ── v29 (WA2) — dédup TOPIQUE du digest news hebdo ─────────────────────

_NEWS_STOPWORDS = {
    # FR
    "le", "la", "les", "de", "du", "des", "un", "une", "en", "et", "à", "au",
    "aux", "sur", "pour", "avec", "sans", "dans", "par", "est", "sont", "va",
    "vont", "ne", "pas", "plus", "son", "sa", "ses", "ce", "cette", "ces",
    "qui", "que", "quand", "même", "soir", "vertu", "sera", "être",
    # EN
    "the", "of", "to", "in", "and", "a", "an", "for", "on", "as", "is", "are",
    "will", "be", "with", "without", "its", "his", "her", "this", "that",
    "under", "over", "after", "before", "into", "it", "not", "no", "even",
    "tonight", "anyway", "still", "law",
}


def _news_tokens(title: str) -> set[str]:
    """Tokens significatifs d'un titre, STEMMÉS (6 premiers caractères).

    Le stem grossier absorbe les flexions (« ban »/« banned »,
    « interdiction »/« interdit ») sans dépendre d'une lib NLP.
    """
    words = re.findall(r"[A-Za-zÀ-ÿ0-9$]{2,}", (title or "").lower())
    return {w[:6] for w in words if w not in _NEWS_STOPWORDS}


def is_duplicate_news(title_a: str, title_b: str) -> bool:
    """True si deux titres couvrent le MÊME événement (recouvrement de tokens).

    Le 10/07, le hebdo listait 3 variantes de la même actu CBDC/loi logement
    (Cointelegraph, CoinDesk, Decrypt). Seuils : ≥ 4 stems communs, OU ≥ 3
    stems communs avec un Jaccard ≥ 0,45 (titres courts).
    """
    ta, tb = _news_tokens(title_a), _news_tokens(title_b)
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    if inter >= 4:
        return True
    union = len(ta | tb) or 1
    return inter >= 3 and (inter / union) >= 0.45


def dedupe_weekly_news(items: Any) -> tuple[list[Any], list[str]]:
    """Déduplique le digest news hebdo par ÉVÉNEMENT (le 1er vu est conservé)."""
    fixes: list[str] = []
    if not isinstance(items, list):
        return items, fixes
    kept: list[Any] = []
    for n in items:
        title = str((n or {}).get("title") or "") if isinstance(n, dict) else str(n)
        dup_of = next(
            (str((k or {}).get("title") or "") if isinstance(k, dict) else str(k)
             for k in kept
             if is_duplicate_news(
                 title,
                 str((k or {}).get("title") or "") if isinstance(k, dict) else str(k))),
            None)
        if dup_of is not None:
            fixes.append(f"news doublon retirée : « {title[:50]}… »")
            continue
        kept.append(n)
    return kept, fixes


# ── v29 (WA9) — impact PTF pondéré à côté des « pertes » citées ─────────

def append_ptf_impact(
    text: Any, impacts: Optional[dict[str, dict[str, Any]]],
) -> tuple[Any, list[str]]:
    """Ajoute « (poids X% · impact PTF ±Y pt) » après « SYM (−Z%) » cité.

    Le 10/07, FET (−14,7%) était LA perte commentée du post-mortem… pour un
    poids de 1,1% du PTF (impact −0,17 pt) : l'emphase était disproportionnée.
    On ne réécrit pas le propos — on chiffre son poids réel. N'agit que si la
    mention n'est pas déjà qualifiée (« impact » absent à proximité).
    """
    fixes: list[str] = []
    if not isinstance(text, str) or not impacts:
        return text, fixes

    def _one(m: re.Match) -> str:
        sym = m.group(1).upper()
        fact = impacts.get(sym)
        if not fact:
            return m.group(0)
        after = text[m.end():m.end() + 60]
        if "impact" in (m.group(0) + after).lower():
            return m.group(0)
        w = fact.get("weight_pct")
        ip = fact.get("impact_pt")
        if w is None or ip is None:
            return m.group(0)
        _w = str(round(w, 1)).replace(".", ",")
        _ip = ("+" if ip >= 0 else "−") + str(abs(round(ip, 2))).replace(".", ",")
        fixes.append(f"{sym} : impact PTF chiffré ({_ip} pt)")
        return f"{m.group(0)} (poids {_w}% · impact PTF {_ip} pt)"

    pat = re.compile(
        r"\b([A-Z0-9]{2,10})\b\s*\(\s*[−\-–]\d{1,3}(?:[.,]\d+)?\s?%[^)]{0,15}\)")
    new = pat.sub(_one, text)
    return (new if new != text else text), fixes


def append_ptf_impact_in_payload(
    payload: dict[str, Any], impacts: Optional[dict[str, dict[str, Any]]],
    keys: tuple[str, ...] = ("losses_vs_recos", "my_errors"),
) -> list[str]:
    """Applique ``append_ptf_impact`` aux sections post-mortem (récursif)."""
    fixes: list[str] = []
    if not isinstance(payload, dict) or not impacts:
        return fixes

    def _walk(node: Any) -> Any:
        if isinstance(node, str):
            new, fx = append_ptf_impact(node, impacts)
            fixes.extend(fx)
            return new
        if isinstance(node, list):
            return [_walk(x) for x in node]
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        return node

    for key in keys:
        if key in payload:
            payload[key] = _walk(payload[key])
    return fixes


# ── v29 (WA10) — poussières : la règle frais > valeur est cohérente ─────

_LIQUIDATE_NOW = re.compile(
    r"(?i)(?:nous\s+)?recommandons\s+une\s+liquidation\s+immédiate[^.]*\.|"
    r"liquidation\s+immédiate\s+sans\s+attendre[^.]*\.")


def fix_dust_advice(
    node: Any, tiny_assets: set[str],
) -> tuple[Any, list[str]]:
    """Corrige le conseil « liquidation immédiate » d'une poussière sous le
    seuil de frais : vendre coûterait PLUS que la valeur récupérée.

    Le 10/07 : « Pour SXT (valeur 0,12 $), nous recommandons une liquidation
    immédiate sans attendre, les frais de transaction risquant de dépasser la
    valeur résiduelle » — la prémisse (frais > valeur) contredit l'ordre
    (vendre). La phrase devient un abandon de ligne assumé.
    """
    fixes: list[str] = []
    if not tiny_assets:
        return node, fixes

    def _fn(text: str) -> str:
        if not isinstance(text, str) or not _LIQUIDATE_NOW.search(text):
            return text
        near = {a for a in tiny_assets
                if re.search(rf"\b{re.escape(a)}\b", text)}
        if not near:
            return text
        fixes.append(
            f"conseil poussière incohérent corrigé ({', '.join(sorted(near))} : "
            "frais > valeur → abandon, pas de vente)")
        return _LIQUIDATE_NOW.sub(
            "les frais de transaction dépasseraient la valeur résiduelle : "
            "ligne à ABANDONNER (sortie du suivi), ne pas payer pour vendre. ",
            text).strip()

    def _walk(n: Any) -> Any:
        if isinstance(n, str):
            return _fn(n)
        if isinstance(n, list):
            return [_walk(x) for x in n]
        if isinstance(n, dict):
            return {k: _walk(v) for k, v in n.items()}
        return n

    return _walk(node), fixes


# ── v29 (WA12) — structure CT baissière + action Renforcer : cadrage LT ──

_BEARISH_STRUCT = re.compile(r"(?i)structure\s+(?:daily|journalière)\s+baissière")
_LT_QUALIFIER = re.compile(r"(?i)\bLT\b|long\s+terme|DCA|accumulation")


def reconcile_bearish_reinforce(entries: Any) -> list[str]:
    """Une ligne « structure daily baissière … → Renforcer » reçoit son cadrage
    LT explicite (mutation in-place des ``analysis``).

    Le 10/07, INJ affichait « Structure daily baissière mais rebond en cours »
    avec action RENFORCER sans dire que la logique est l'accumulation LT en
    capitulation — le signal CT et l'action semblaient se contredire.
    """
    fixes: list[str] = []
    if not isinstance(entries, list):
        return fixes
    for e in entries:
        if not isinstance(e, dict):
            continue
        if str(e.get("action") or "").lower() != "renforcer":
            continue
        txt = e.get("analysis")
        if not isinstance(txt, str) or not _BEARISH_STRUCT.search(txt):
            continue
        if _LT_QUALIFIER.search(txt):
            continue  # déjà cadré (« accumulation », « LT », « DCA »…)
        e["analysis"] = (txt.rstrip().rstrip(".")
                         + ". Renfort = accumulation LT (DCA) malgré le signal "
                           "CT défavorable.")
        fixes.append(
            f"{str(e.get('asset') or '?').upper()} : cadrage LT ajouté "
            "(structure CT baissière + Renforcer)")
    return fixes


def sanitize_ath_in_payload_texts(
    payload: dict[str, Any], ath_facts: Optional[dict[str, Any]],
    keys: tuple[str, ...] = ("exit_plan", "losses_vs_recos", "my_errors",
                             "cost_of_errors"),
) -> list[str]:
    """Applique ``sanitize_ath_text`` récursivement aux sections texte libres."""
    fixes: list[str] = []
    if not isinstance(payload, dict) or not isinstance(ath_facts, dict):
        return fixes

    def _walk(node: Any) -> Any:
        if isinstance(node, str):
            new, fx = sanitize_ath_text(node, ath_facts)
            fixes.extend(fx)
            return new
        if isinstance(node, list):
            return [_walk(x) for x in node]
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        return node

    for key in keys:
        if key in payload:
            payload[key] = _walk(payload[key])
    return fixes
