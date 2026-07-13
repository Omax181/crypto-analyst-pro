"""Notifications push Telegram — BRIEFING D'ANALYSTE (v29 TG-refonte).

Format validé avec Omar (12/07/2026) : Telegram est SON canal principal (il
lit tous les messages) ; le mail n'est ouvert que pour creuser. Le message
doit donc suffire à comprendre l'essentiel ET dire quoi faire, sans blabla.

Structure en 2 zones :

  📌 EN BREF   — 3 lignes autonomes pour l'aperçu de notification :
                 ⚡ le verdict SEC (action ou rien), 📊 le régime TOUJOURS
                 argumenté (« Crypto baissière (BTC −8% vs MM200 · F&G 26) »),
                 💼 l'argent (👁 le niveau clé au soir).
  ──────────
  Le détail   — 🌍 le marché (analyse IA du mail réutilisée, coupée en fin de
                 phrase, jamais en plein chiffre), 🎯 l'ACTION explicite (le
                 pourquoi + la condition — formulation distincte de l'EN BREF),
                 📊 l'ÉVOLUTION DES THÈSES ÉNONCÉES (top 3 : conviction et son
                 évolution, date d'émission, entrée → actuel → cible, lecture
                 courte), ⚠️/📅 le calendrier EXPLIQUÉ (pourquoi l'événement
                 compte), ↩️ la rétro (hebdo), 💬 commandes personnalisées.

Règles fermes (arbitrages Omar 12/07) :
  - régime jamais nu : toujours un argument chiffré déterministe ;
  - thèses plafonnées à 3 (les plus actives) — le reste vit dans le mail ;
  - le soir, une thèse n'apparaît QUE si elle a bougé (stop franchi, cible
    touchée, passage sous pression) — sinon « aucune thèse touchée » suffit ;
  - conviction affichée en évolution (« conv. 72% → 78% ») quand elle change ;
  - ⚡ et 🎯 ne se répètent pas mot pour mot : verdict sec vs pourquoi/condition ;
  - la cote Polymarket Fed n'est JAMAIS accolée à un événement non-Fed.

100% Python depuis le payload (déterministe, zéro coût, zéro hallucination) :
on NE réutilise que des textes déjà rédigés par l'IA pour le mail (synthèse
macro, weekly_summary…), on ne génère jamais de nouvelle prose. Chaque bloc
n'apparaît que s'il a du contenu.

Best-effort : un échec de construction ou d'envoi ne fait JAMAIS échouer la
génération du rapport (le mail reste la livraison principale).
"""

from __future__ import annotations

from typing import Any, Optional

from src.telegram_bot import telegram_api
from src.utils.logger import get_logger

logger = get_logger(__name__)

_KIND_LABELS = {
    "morning": "☀️ MATIN",
    "evening": "🌙 SOIR",
    "weekly": "📊 HEBDO",
}
_MARKET_TITLE = {
    "morning": "Le marché",
    "evening": "Ce qui a bougé",
    "weekly": "La tendance",
}
# Régime BTC → adjectif accordé à « Crypto » (le label seul, jamais nu :
# _market_brief lui adjoint toujours l'argument chiffré).
_REGIME_ADJ = {
    "bull": "haussière",
    "bear": "baissière",
    "range": "en range",
    "transition": "en transition",
}
# Calendrier EXPLIQUÉ (décision Omar 12/07) : pourquoi l'événement compte,
# en une causalité générique et factuelle (jamais une prédiction).
_EVENT_EXPLAIN: list[tuple[tuple[str, ...], str]] = [
    (("CPI", "INFLATION", "PCE", "PPI"),
     "chaud → baisses de taux repoussées, vent contraire crypto ; "
     "froid → soutien au risque"),
    (("FOMC", "FED", "TAUX DIRECTEUR", "RATE DECISION"),
     "le ton sur les taux guide l'appétit pour le risque"),
    (("NFP", "PAYROLL", "EMPLOI", "CHÔMAGE", "CHOMAGE", "UNEMPLOYMENT"),
     "un emploi trop chaud repousse les baisses de taux"),
    (("PIB", "GDP"),
     "surprise de croissance = lecture risk-on/risk-off directe"),
    (("BOE", "BCE", "ECB", "BOJ", "BAILEY", "LAGARDE", "UEDA"),
     "impact via les devises — secondaire pour la crypto"),
]
_FED_KEYWORDS = ("FED", "FOMC", "CPI", "PCE", "TAUX", "RATE", "NFP", "PAYROLL")


# --------------------------------------------------------------------------- #
# Formatage (FR, robuste — ne lève jamais)
# --------------------------------------------------------------------------- #
def _num(v: Any) -> Optional[float]:
    try:
        if isinstance(v, str):
            v = v.replace(" ", "").replace(" ", "").replace(",", ".")
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt_usd(v: Any) -> Optional[str]:
    """Montant en $ au format FR (espace milliers, virgule décimale)."""
    n = _num(v)
    if n is None:
        return None
    a = abs(n)
    if a >= 1000:
        s = f"{n:,.0f}".replace(",", " ")
    elif a >= 100:
        # v29 — pas de décimales parasites sur les centaines (« 340 $ »,
        # pas « 340,00 $ ») : au centime près, elles n'informent plus.
        s = f"{n:.0f}"
    elif a >= 1:
        s = f"{n:.2f}".replace(".", ",")
    else:
        # Sous 1 $ : 4 décimales max, zéros superflus retirés (0,073 et non
        # 0,0730), mais 2 décimales minimum (0,10 pas 0,1).
        s = f"{n:.4f}".rstrip("0")
        if "." in s:
            intp, dec = s.split(".")
            s = f"{intp}.{(dec + '00')[:2] if len(dec) < 2 else dec}"
        s = s.replace(".", ",")
    return f"{s} $"


def _int_usd(v: Any) -> Optional[str]:
    """Montant arrondi au dollar entier (P&L jour) : « +3 $ »."""
    n = _num(v)
    if n is None:
        return None
    return f"{round(n):,} $".replace(",", " ")


def _pct(v: Any, dec: int = 1) -> Optional[str]:
    """Pourcentage signé FR : « +4,4% » / « −0,5% » (vrai signe moins)."""
    n = _num(v)
    if n is None:
        return None
    sign = "+" if n >= 0 else "−"
    return f"{sign}{abs(n):.{dec}f}".replace(".", ",") + "%"


def _plain(text: Any) -> str:
    """Retire le balisage Markdown (**, *, _, `) d'un texte IA réutilisé, pour
    ne jamais casser le parse Markdown v1 de Telegram, et compacte les blancs."""
    s = str(text or "")
    for mark in ("**", "__", "*", "_", "`", "#"):
        s = s.replace(mark, "")
    return " ".join(s.split()).strip()


def _clip(text: Any, n: int) -> str:
    """Coupe un texte long EN FIN DE PHRASE de préférence (v29 : l'audit du
    10/07 montrait des paragraphes tronqués en plein chiffre « CPI 4.3%… »).
    Repli : coupe au dernier espace, jamais au milieu d'un nombre."""
    s = _plain(text)
    if len(s) <= n:
        return s
    cut = s[:n]
    dot = cut.rfind(". ")
    if dot >= int(n * 0.5):
        return cut[: dot + 1]
    sp = cut.rfind(" ")
    if sp >= int(n * 0.6):
        cut = cut[:sp]
    return cut.rstrip(" ,;:·-—") + "…"


def _join_dots(parts: list[Optional[str]], sep: str = " · ") -> Optional[str]:
    kept = [p for p in parts if p]
    return sep.join(kept) if kept else None


def _sentence(text: Any) -> str:
    """Première phrase d'un texte (repère « . » suivi d'espace)."""
    s = _plain(text)
    if not s:
        return ""
    cut = s.split(". ")
    return cut[0].rstrip(".") if cut else s


def _prefix(text: Optional[str], pfx: str) -> Optional[str]:
    return f"{pfx}{text}" if text else None


def _sym(v: Any) -> str:
    """Ticker sûr pour le Markdown v1 : jamais de `*`/`_` qui déséquilibrerait
    le gras/italique Telegram (v29 audit — hardening données hostiles)."""
    return _plain(str(v or "?").upper()) or "?"


# --------------------------------------------------------------------------- #
# Petits extracteurs partagés
# --------------------------------------------------------------------------- #
def _regime(payload: dict[str, Any]) -> dict[str, Any]:
    reg = payload.get("market_regime") or {}
    return reg if isinstance(reg, dict) and reg.get("available") else {}


def _regime_adj(reg: dict[str, Any]) -> Optional[str]:
    """Adjectif du régime accordé à « Crypto » (depuis la clé canonique regime,
    repli sur label_fr : « baissier » → « baissière », « range » → « en range »)."""
    adj = _REGIME_ADJ.get(str(reg.get("regime") or "").lower())
    if adj:
        return adj
    lbl = str(reg.get("label_fr") or "").lower().strip()
    if not lbl:
        return None
    known = {"haussier": "haussière", "baissier": "baissière",
             "range": "en range", "transition": "en transition"}
    if lbl in known:
        return known[lbl]
    if lbl.endswith("ier"):
        return lbl[:-3] + "ière"
    return f"en {lbl}"


def _fear_greed(payload: dict[str, Any]) -> tuple[Optional[Any], Optional[str]]:
    """(valeur, label) F&G, quelle que soit la source du payload."""
    for node in (payload.get("macro_context"), payload.get("evening_macro")):
        if isinstance(node, dict) and node.get("fear_greed") is not None:
            return node.get("fear_greed"), node.get("fear_greed_label")
    fg = payload.get("fear_greed")
    if isinstance(fg, dict) and fg.get("value") is not None:
        return fg.get("value"), fg.get("label")
    return None, None


def _regime_evidence(payload: dict[str, Any], reg: dict[str, Any]) -> Optional[str]:
    """Argument chiffré DÉTERMINISTE du régime — jamais un label nu (décision
    Omar 12/07) : distance à la MM200, sinon la 1re raison du classifieur,
    plus le sentiment F&G quand il est connu."""
    bits: list[Optional[str]] = []
    pv = _num(reg.get("price_vs_ma200_pct"))
    if pv is not None:
        bits.append(f"BTC {_pct(pv)} vs MM200")
    else:
        reasons = reg.get("reasons")
        if isinstance(reasons, list) and reasons:
            bits.append(_plain(reasons[0]))
    fg, fgl = _fear_greed(payload)
    if fg is not None:
        # Pas de parenthèses ici : l'évidence vit déjà entre parenthèses
        # dans la ligne 📊 (« (BTC −8% vs MM200 · F&G 26 peur) »).
        bits.append(f"F&G {fg}" + (f" {_plain(fgl).lower()}" if fgl else ""))
    return _join_dots(bits)


def _agenda_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Liste d'événements macro à venir, quelle que soit la source du payload :
    week_ahead (hebdo, riche) → macro_agenda (matin/soir) → upcoming_calendar."""
    wa = payload.get("week_ahead")
    if isinstance(wa, list) and wa:
        return [e for e in wa if isinstance(e, dict)]
    for key in ("macro_agenda", "upcoming_calendar_facts"):
        node = payload.get(key)
        if isinstance(node, dict) and isinstance(node.get("events"), list) and node["events"]:
            return [e for e in node["events"] if isinstance(e, dict)]
    uc = payload.get("upcoming_calendar")
    if isinstance(uc, list) and uc:
        return [e for e in uc if isinstance(e, dict)]
    return []


def _upcoming_events(payload: dict[str, Any], n: int = 2) -> list[dict[str, Any]]:
    """Événements À VENIR (déjà-tombés exclus), les « high » d'abord."""
    events = [e for e in _agenda_events(payload)
              if e.get("label") and not e.get("already_published")]
    ordered = ([e for e in events if e.get("importance") == "high"]
               + [e for e in events if e.get("importance") != "high"])
    return ordered[:n]


def _event_label(e: dict[str, Any]) -> str:
    when = e.get("when") or e.get("weekday_label") or e.get("date_label")
    return _plain(e["label"]) + (f" ({_plain(when)})" if when else "")


def _next_event_label(payload: dict[str, Any]) -> Optional[str]:
    ev = _upcoming_events(payload, 1)
    return _event_label(ev[0]) if ev else None


def _event_explainer(label: Any) -> Optional[str]:
    """Pourquoi l'événement compte (mapping générique, jamais une prédiction)."""
    up = str(label or "").upper()
    for keys, explain in _EVENT_EXPLAIN:
        if any(k in up for k in keys):
            return explain
    return None


def _is_fed_related(label: Any) -> bool:
    up = str(label or "").upper()
    return any(k in up for k in _FED_KEYWORDS)


def _polymarket_fed(payload: dict[str, Any]) -> Optional[str]:
    """Ligne Polymarket Fed compacte, depuis morning (macro_context) ou soir
    (polymarket_facts). TOUJOURS étiquetée Fed (audit 10/07 : la cote «
    maintien » était accolée à un discours BOE)."""
    mc = payload.get("macro_context") or {}
    fb = mc.get("polymarket_fed_bars")
    if not isinstance(fb, dict):
        fb = (payload.get("polymarket_facts") or {}).get("fed_bars")
    if isinstance(fb, dict):
        dp = _num(fb.get("dominant_pct"))
        dom = fb.get("dominant")
        if dp is not None and dom:
            return f"Fed : ~{dp:.0f}% {_plain(dom)} attendu (Polymarket)"
    return None


def _pick_assets(payload: dict[str, Any], kind: str, n: int = 2) -> list[str]:
    """Actifs les plus pertinents du message, pour personnaliser /pourquoi.
    v29 : uniquement des actifs réellement AFFICHÉS dans le digest (l'audit du
    10/07 relevait « /analyse ANKR » alors qu'ANKR n'apparaissait nulle part)."""
    out: list[str] = []

    def add(a: Any) -> None:
        # _plain via _sym : un ticker exotique (« E*H ») ne casse pas le
        # Markdown de la ligne de commandes (v29 audit — hardening).
        s = _plain(str(a or "").upper().strip())
        if s and s != "?" and s not in out:
            out.append(s)

    if kind == "morning":
        for t in (payload.get("thesis_of_the_day") or []):
            if isinstance(t, dict):
                add(t.get("asset"))
        for r in (payload.get("active_recommendations_tracking") or []):
            if isinstance(r, dict):
                add(r.get("asset"))
    elif kind == "evening":
        for b in (payload.get("reco_bilan") or []):
            if isinstance(b, dict):
                add(b.get("asset"))
        # Movers : seuls ceux ≥ ±10% sont affichés dans 💼 — mêmes règles ici.
        for m in ((payload.get("daily_pnl") or {}).get("top_movers") or []):
            if isinstance(m, dict) and abs(_num(m.get("change")) or 0.0) >= 10:
                add(m.get("symbol"))
    else:
        for r in ((payload.get("predictions_scoring") or {}).get("detail") or []):
            if isinstance(r, dict):
                add(r.get("asset"))
        for p in (payload.get("positions_review") or []):
            if isinstance(p, dict):
                add(p.get("asset"))
    return out[:n]


# --------------------------------------------------------------------------- #
# 🌍 Le marché — narratif (réutilise l'analyse IA du mail, repli déterministe)
# --------------------------------------------------------------------------- #
def _market_fallback(payload: dict[str, Any]) -> Optional[str]:
    parts: list[Optional[str]] = []
    reg = _regime(payload)
    if reg:
        adj = _regime_adj(reg)
        ev = _regime_evidence(payload, reg)
        if adj:
            parts.append(f"Crypto {adj}" + (f" ({ev})" if ev else ""))
    mrr = payload.get("macro_regime_readout") or {}
    if isinstance(mrr, dict) and mrr.get("regime"):
        parts.append(f"macro {_plain(mrr['regime'])}")
    if not reg:
        fg, fgl = _fear_greed(payload)
        if fg is not None:
            parts.append(f"sentiment F&G {fg}" + (f" ({_plain(fgl)})" if fgl else ""))
    return _join_dots(parts)


def _weekly_summary_text(payload: dict[str, Any]) -> Optional[str]:
    ws = payload.get("weekly_summary")
    if isinstance(ws, dict):
        ws = ws.get("bullets")
    bullets: list[str] = []
    if isinstance(ws, list):
        for b in ws[:3]:
            txt = b.get("text") if isinstance(b, dict) else b
            if txt:
                bullets.append(_plain(txt))
    return " ".join(bullets) if bullets else None


def _market_narrative(payload: dict[str, Any], kind: str) -> Optional[str]:
    """Paragraphe « Le marché » — priorité au texte IA déjà rédigé pour le
    mail (le régime argumenté vit dans l'EN BREF 📊, pas ici : zéro doublon)."""
    if kind == "morning":
        txt = (payload.get("macro_context") or {}).get("regime_synthesis")
        if txt:
            return _clip(txt, 460)
        return _market_fallback(payload)

    if kind == "evening":
        # « marchés US inchangés » vit déjà dans l'EN BREF (📊) : on ne le
        # répète pas ici. On préfère le résumé « depuis le matin » (IA/Python).
        smf = payload.get("since_morning_facts")
        if isinstance(smf, str) and smf.strip():
            return _clip(smf, 400)
        base = _market_fallback(payload)
        if (payload.get("header") or {}).get("us_market_open") is False:
            return base
        return base

    # weekly
    txt = _weekly_summary_text(payload)
    if txt:
        return _clip(txt, 480)
    note = payload.get("regime_reconciliation_note")
    if note:
        return _clip(note, 360)
    return _market_fallback(payload)


# --------------------------------------------------------------------------- #
# Soir — détection des thèses qui ONT BOUGÉ (seuil franchi / statut dégradé)
# --------------------------------------------------------------------------- #
def _evening_moved_rows(payload: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """Lignes du bilan soir où quelque chose s'est PASSÉ : stop franchi
    (invalidated), cible touchée, ou passage sous pression (≤ −1,5% — seuil
    anti-bruit v28). Les « stable » / « on_track » / « pending » ne comptent
    pas comme un mouvement : elles restent silencieuses (décision Omar 12/07)."""
    out: list[tuple[dict[str, Any], str]] = []
    for b in (payload.get("reco_bilan") or []):
        if not isinstance(b, dict):
            continue
        status = str(b.get("status") or "")
        action = str(b.get("action") or "").upper()
        bearish = "ALLÉG" in action or "ALLEG" in action
        cur, tgt = _num(b.get("current")), _num(b.get("target"))
        if status == "invalidated":
            out.append((b, "stop"))
        elif tgt and cur and ((not bearish and cur >= tgt)
                              or (bearish and cur <= tgt)):
            out.append((b, "target"))
        elif status == "under_pressure":
            out.append((b, "pressure"))
    return out[:3]


# --------------------------------------------------------------------------- #
# 📌 EN BREF — 3 lignes autonomes
# --------------------------------------------------------------------------- #
def _first_firm_thesis_line(payload: dict[str, Any]) -> Optional[str]:
    for t in (payload.get("thesis_of_the_day") or []):
        if not isinstance(t, dict):
            continue
        if any(k in (t.get("action") or "").upper()
               for k in ("RENFORC", "ALLÉG", "ALLEG")):
            asset = str(t.get("asset") or "").upper()
            line = f"{t.get('action')} {asset}".strip()
            ap = t.get("action_plan") if isinstance(t.get("action_plan"), dict) else {}
            if ap.get("sizing_note"):
                line += f" · {_plain(ap['sizing_note'])}"
            elif _num(ap.get("entry")):
                line += f" · entrée {_fmt_usd(ap.get('entry'))}"
            return line
    return None


def _verdict(payload: dict[str, Any], kind: str) -> Optional[str]:
    if kind == "morning":
        top = payload.get("top_action") or {}
        if top.get("is_nothing"):
            return "⚡ *Aucun achat aujourd'hui.*"
        if top.get("line"):
            return f"⚡ *{_plain(top['line'])}*"
        firm = _first_firm_thesis_line(payload)
        if firm:
            return f"⚡ *{_plain(firm)}*"
        return "⚡ *Pas de geste prioritaire aujourd'hui.*"

    if kind == "evening":
        moved = _evening_moved_rows(payload)
        if moved:
            if len(moved) == 1:
                b, mkind = moved[0]
                asset = _sym(b.get("asset"))
                what = {"stop": "stop franchi, thèse invalidée",
                        "target": "cible touchée",
                        "pressure": "passe sous pression"}[mkind]
                return f"⚡ *{asset} : {what}.*"
            return f"⚡ *{len(moved)} thèses ont bougé — détail ci-dessous.*"
        if payload.get("reco_bilan"):
            return "⚡ *Aucune thèse touchée aujourd'hui.*"
        pnl = payload.get("daily_pnl") or {}
        p = _num(pnl.get("day_change_pct"))
        if p is None:
            return "⚡ *Bilan du jour prêt.*"
        qual = "blanche" if abs(p) < 0.3 else ("positive" if p > 0 else "sous pression")
        return f"⚡ *Journée {qual} ({_pct(p, 2)})* — rien à faire ce soir."

    # weekly — le verdict = l'action de la semaine (ou son absence).
    plan = payload.get("weekly_action_plan") or []
    if plan and isinstance(plan[0], dict) and plan[0].get("action"):
        return f"⚡ *{_clip(plan[0]['action'], 140)}*"
    snap = payload.get("portfolio_snapshot") or {}
    wk = _pct(snap.get("weekly_pnl_pct"))
    if wk:
        return f"⚡ *Semaine {wk} — aucune action programmée.*"
    return "⚡ *Bilan de la semaine prêt — aucune action programmée.*"


def _market_brief(payload: dict[str, Any], kind: str) -> Optional[str]:
    """Ligne 📊 de l'EN BREF : régime ARGUMENTÉ (jamais un label nu) +
    prochain événement (matin/hebdo) ou note séance US (soir)."""
    reg = _regime(payload)
    seg = None
    if reg:
        ev = _regime_evidence(payload, reg)
        if reg.get("changed") and reg.get("previous_label_fr"):
            # Le signal le plus important : le changement de régime.
            seg = (f"Crypto passe en {_plain(reg.get('label_fr'))} "
                   f"(était {_plain(reg['previous_label_fr']).lower()})")
            if ev:
                seg += f" — {ev}"
        else:
            adj = _regime_adj(reg)
            seg = f"Crypto {adj}" if adj else None
            if seg and ev:
                seg += f" ({ev})"
    else:
        fg, fgl = _fear_greed(payload)
        if fg is not None:
            seg = f"Sentiment F&G {fg}" + (f" ({_plain(fgl)})" if fgl else "")

    if kind == "evening":
        if (payload.get("header") or {}).get("us_market_open") is False:
            return _join_dots([seg, "marchés US inchangés"])
        return seg

    evt = _next_event_label(payload)
    return _join_dots([seg, (f"{evt} en vue" if evt else None)])


def _first_support_level(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    for row in (payload.get("levels_tonight") or []):
        if isinstance(row, dict) and row.get("type") == "support" and row.get("level"):
            return row
    for row in (payload.get("levels_tonight") or []):
        if isinstance(row, dict) and row.get("level"):
            return row
    return None


def _enbref_line3(payload: dict[str, Any], kind: str) -> Optional[str]:
    if kind == "evening":
        row = _first_support_level(payload)
        return f"👁 Niveau clé cette nuit : *{row['level']}*" if row else None

    snap = payload.get("portfolio_snapshot") or {}
    val = _fmt_usd(snap.get("value_usd"))
    if kind == "weekly":
        bits = [val,
                (f"{_pct(snap.get('weekly_pnl_pct'))} / 7j"
                 if snap.get("weekly_pnl_pct") is not None else None),
                (f"vs BTC {_pct(snap.get('vs_btc_7d_pct'))}"
                 if snap.get("vs_btc_7d_pct") is not None else None)]
        body = _join_dots(bits)
        return f"💼 {body}" if body else None

    # morning — la valeur et le 24h, SANS phrase de « santé des positions »
    # (l'audit du 10/07 : « sous pression » contredisait des lignes vertes).
    d = _pct(snap.get("change_24h_pct"))
    base = _join_dots([val, (f"({d} / 24h)" if d else None)], sep=" ")
    return f"💼 {base}" if base else None


# --------------------------------------------------------------------------- #
# 🎯 Action — explicite : le pourquoi + la condition (≠ formulation EN BREF)
# --------------------------------------------------------------------------- #
def _thesis_argument(t: dict[str, Any]) -> Optional[str]:
    obs = t.get("observation")
    if isinstance(obs, str) and obs.strip():
        return _sentence(obs)
    rs = t.get("reasoning_signals")
    if isinstance(rs, list) and rs:
        return _plain(str(rs[0]))
    return None


def _thesis_line(t: dict[str, Any]) -> str:
    from src.analytics.reco_gate import executable_for_top_action

    asset = _sym(t.get("asset"))
    a = str(t.get("action") or "").upper()
    verb = ("renforcer" if "RENFORC" in a
            else "alléger" if ("ALLÉG" in a or "ALLEG" in a)
            else "on garde" if "MAINTEN" in a
            else "on surveille" if "SURVEIL" in a
            else (a.lower() or "—"))
    conf = _num(t.get("confidence"))
    confs = f" (conv. {conf:.0f}%)" if conf is not None else ""

    ctw = t.get("ct_warning")
    if ctw:
        body = _plain(ctw).lstrip("⚠ ").strip()
        return f" • *{asset}*{confs} — on accumule (DCA) : {_clip(body, 150)}"

    gate = t.get("gate_note")
    if gate:
        return f" • *{asset}* — {verb} : {_clip(gate, 150)}"

    ap = t.get("action_plan") if isinstance(t.get("action_plan"), dict) else {}
    if executable_for_top_action(t):
        size = (_plain(ap.get("sizing_note")) if ap.get("sizing_note")
                else (f"entrée {_fmt_usd(ap.get('entry'))}" if _num(ap.get("entry")) else None))
        seg = _join_dots([size, _thesis_argument(t)])
        return f" • *{asset}*{confs} — {verb}" + (f" : {_clip(seg, 170)}" if seg else "")

    arg = _thesis_argument(t)
    return f" • *{asset}*{confs} — {verb}" + (f" : {_clip(arg, 150)}" if arg else "")


def _morning_action_block(payload: dict[str, Any]) -> list[str]:
    theses = payload.get("thesis_of_the_day") or []
    picked: list[dict[str, Any]] = []
    for t in theses:
        if not isinstance(t, dict):
            continue
        a = str(t.get("action") or "").upper()
        relevant = (any(k in a for k in ("RENFORC", "ALLÉG", "ALLEG", "MAINTEN"))
                    or t.get("gate_note") or t.get("ct_warning"))
        if relevant:
            picked.append(t)
        if len(picked) >= 3:
            break
    top = payload.get("top_action") or {}
    lines = ["🎯 *Action du jour*"]
    if top.get("is_nothing"):
        # Formulation distincte de l'EN BREF (verdict sec là-bas, pourquoi ici).
        lines.append("Rien à exécuter ce matin — le pourquoi, par actif :"
                     if picked else
                     "Rien à exécuter ce matin — pas de signal actionnable sur le book.")
    elif top.get("line"):
        lines.append(f"→ {_plain(top['line'])}")
    lines.extend(_thesis_line(t) for t in picked)
    return lines if len(lines) > 1 else []


def _evening_action_block(payload: dict[str, Any]) -> list[str]:
    rows = [b for b in (payload.get("reco_bilan") or []) if isinstance(b, dict)]
    if not rows:
        return []
    moved = _evening_moved_rows(payload)
    lines = ["🎯 *Action ce soir*"]
    if not moved:
        n = len(rows)
        lines.append(
            f"Rien à faire : aucun stop ni cible touché ({n} thèse{'s' if n > 1 else ''} "
            "suivie" + ("s" if n > 1 else "") + "). On garde la main sur le bouton.")
        return lines
    for b, mkind in moved:
        asset = _sym(b.get("asset"))
        if mkind == "stop":
            reason = _clip(b.get("reason"), 90) if b.get("reason") else "stop franchi"
            lines.append(f" • *{asset}* — 🔴 {reason} : thèse invalidée, "
                         "on ne renforce plus.")
        elif mkind == "target":
            tgt = _fmt_usd(b.get("target"))
            lines.append(f" • *{asset}* — ✅ cible{f' {tgt}' if tgt else ''} touchée : "
                         "prise de profit partielle à envisager.")
        else:
            d = _pct(b.get("delta_pct"), 2)
            reason = _clip(b.get("reason"), 80) if b.get("reason") else None
            seg = f" • *{asset}* — ⚠️ sous pression"
            if d:
                seg += f" ({d} vs entrée)"
            if reason:
                seg += f" · {reason}"
            lines.append(seg)
    return lines


def _weekly_action_block(payload: dict[str, Any]) -> list[str]:
    scens = [s for s in (payload.get("scenarios") or []) if isinstance(s, dict)]
    dom = (max(scens, key=lambda s: _num(s.get("probability_pct")) or 0.0)
           if scens else None)
    plan = payload.get("weekly_action_plan") or []
    action = (plan[0].get("action") if plan and isinstance(plan[0], dict) else None)

    parts: list[str] = []
    if action:
        txt = _clip(action, 220).rstrip(".")
        # v29 (audit) — un plan tronqué par _clip finit en « … » : pas de
        # point final en plus (« …. »).
        dot = "" if txt.endswith("…") else "."
        if txt[:3].lower() == "si ":
            parts.append(f"Une seule, conditionnelle : {txt[0].lower()}{txt[1:]}{dot} "
                         "Sinon, on ne bouge pas.")
        else:
            parts.append(f"→ {txt}{dot}")
    else:
        parts.append("Aucune action programmée — on laisse les positions travailler.")
    if dom and dom.get("label"):
        prob = _num(dom.get("probability_pct"))
        probs = f" ({prob:.0f}%)" if prob is not None else ""
        parts.append(f"Scénario dominant : {str(dom['label']).upper()}{probs}.")
    sc = payload.get("scenarios_context") or {}
    if isinstance(sc, dict) and sc.get("bascule"):
        parts.append(f"Bascule : {_plain(sc['bascule'])}.")

    if not (action or dom):
        return []
    return ["🎯 *Action de la semaine*", " ".join(parts)]


# --------------------------------------------------------------------------- #
# 📊 Évolution des thèses énoncées (top 3 — le reste vit dans le mail)
# --------------------------------------------------------------------------- #
def _conv_note(conf: Any, prev: Any) -> Optional[str]:
    """« conv. 78% » — ou « conv. 72% → 78% » quand la conviction a évolué."""
    c = _num(conf)
    if c is None:
        return None
    p = _num(prev)
    if p is not None and abs(p - c) >= 1:
        return f"conv. {p:.0f}% → {c:.0f}%"
    return f"conv. {c:.0f}%"


def _thesis_head_line(asset: str, conf: Any, prev: Any, issued: Any) -> str:
    head = f" • *{asset}*"
    conv = _conv_note(conf, prev)
    if conv:
        head += f" ({conv})"
    if issued:
        head += f" · émise {_plain(issued)}"
    return head


def _thesis_path_line(entry: Any, cur: Any, delta: Any, target: Any,
                      target_label: str = "cible") -> Optional[str]:
    e, c = _fmt_usd(entry), _fmt_usd(cur)
    move = f"{e} → {c}" if e and c else (c or e)
    d = _pct(delta)
    bits = [f"{move} ({d})" if move and d else move,
            (f"{target_label} {target}" if target else None)]
    body = _join_dots(bits)
    return f"   {body}" if body else None


def _morning_thesis_rows(payload: dict[str, Any]) -> list[str]:
    rows = [r for r in (payload.get("active_recommendations_tracking") or [])
            if isinstance(r, dict)]
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: abs(_num(r.get("progress_pct")) or 0.0),
                  reverse=True)[:3]
    lines = ["📊 *Évolution des thèses énoncées*"]
    for r in rows:
        asset = _sym(r.get("asset"))
        lines.append(_thesis_head_line(asset, r.get("confidence"),
                                       r.get("prev_confidence"), r.get("issued_at")))
        tgt = _fmt_usd(r.get("ct_target"))
        path = _thesis_path_line(r.get("entry_price"), r.get("current_price"),
                                 r.get("progress_pct"), tgt,
                                 "cible act." if r.get("ct_target_fallback") else "cible")
        if path:
            lines.append(path)
        # Lecture courte : statut de santé déterministe + son commentaire.
        health = _plain(r.get("health_status"))
        cm = _clip(r.get("comment"), 90) if r.get("comment") else None
        if health and cm:
            lines.append(f"   → {health} — {cm}")
        elif health:
            sd = _num(r.get("stop_distance_pct"))
            lines.append(f"   → {health}" + (f" · stop à {_pct(sd)}" if sd is not None else ""))
    return lines if len(lines) > 1 else []


def _weekly_thesis_rows(payload: dict[str, Any]) -> list[str]:
    detail = [r for r in ((payload.get("predictions_scoring") or {}).get("detail") or [])
              if isinstance(r, dict) and r.get("asset")]
    pr_by = {str(p.get("asset")).upper(): p
             for p in (payload.get("positions_review") or []) if isinstance(p, dict)}

    if detail:
        # Ouvertes d'abord (triées par |Δ| — les plus actives), puis clôturées.
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        opened = sorted((r for r in detail if r.get("status") == "in_progress"),
                        key=lambda r: abs(_num(r.get("delta_pct")) or 0.0),
                        reverse=True)
        closed = [r for r in detail if r.get("status") != "in_progress"]
        for r in opened + closed:
            a = str(r.get("asset")).upper()
            if a not in seen:
                seen.add(a)
                rows.append(r)
            if len(rows) >= 3:
                break
        lines = ["📊 *Évolution des thèses énoncées*"]
        for r in rows:
            asset = _sym(r.get("asset"))
            lines.append(_thesis_head_line(asset, r.get("confidence"),
                                           r.get("prev_confidence"),
                                           r.get("entry_date")))
            pr = pr_by.get(asset) or {}
            tgt = _fmt_usd(r.get("ct_target"))
            tlabel = "cible"
            if not tgt:
                tl, th = _fmt_usd(pr.get("lt_target_low")), _fmt_usd(pr.get("lt_target_high"))
                if tl and th:
                    tgt = f"{tl}–{th}"
                    tk = _plain(pr.get("lt_target_kind"))
                    tlabel = f"cible {tk}" if tk else "cible"
            path = _thesis_path_line(r.get("entry_price"), r.get("current_price"),
                                     r.get("delta_pct"), tgt, tlabel)
            if path:
                lines.append(path)
            # Lecture : phase LT + analyse du weekly ; clôturées = verdict.
            status = str(r.get("status") or "")
            if status == "validated":
                lines.append("   → ✓ validée — objectif atteint.")
            elif status == "invalidated":
                lines.append("   → ✗ invalidée — stop franchi.")
            else:
                st = _plain(pr.get("lt_status"))
                an = _clip(pr.get("analysis"), 90) if pr.get("analysis") else None
                # v29 (audit) — pas de « …. » : une lecture déjà tronquée par
                # _clip (« … ») ne reçoit pas de point final en plus.
                if an:
                    an = an if an.endswith("…") else an.rstrip(".") + "."
                if st and an:
                    lines.append(f"   → {st.capitalize()} — {an}")
                elif st:
                    lines.append(f"   → {st.capitalize()}.")
                elif an:
                    lines.append(f"   → {an}")
        return lines if len(lines) > 1 else []

    # Repli sans tracker : positions clés du weekly (conviction / notables).
    rows_pr = [p for p in (payload.get("positions_review") or []) if isinstance(p, dict)]
    if not rows_pr:
        return []

    def _notable(p: dict[str, Any]) -> bool:
        return bool(p.get("conviction")
                    or (p.get("h30") or {}).get("status") in ("in_progress", "validated")
                    or abs(_num(p.get("pru_pct")) or 0.0) >= 30)

    picked = [p for p in rows_pr if _notable(p)] or rows_pr
    lines = ["📌 *Positions clés*"]
    for p in picked[:3]:
        asset = _sym(p.get("asset"))
        bits: list[Optional[str]] = []
        pru = _pct(p.get("pru_pct"))
        if pru:
            bits.append(f"{pru} vs PRU")
        st = _plain(p.get("lt_status"))
        if st:
            bits.append(st)
        tl, th = _fmt_usd(p.get("lt_target_low")), _fmt_usd(p.get("lt_target_high"))
        if tl and th:
            tk = _plain(p.get("lt_target_kind"))
            bits.append(_join_dots([f"cible {tk}".strip() if tk else "cible",
                                    f"{tl}–{th}"], sep=" "))
        an = _clip(p.get("analysis"), 90) if p.get("analysis") else None
        seg = _join_dots([_join_dots(bits, sep=", "), an])
        lines.append(f" • *{asset}* — {_clip(seg, 200)}" if seg else f" • *{asset}*")
    return lines


# --------------------------------------------------------------------------- #
# 💼 Ta journée (soir)
# --------------------------------------------------------------------------- #
def _evening_journey_block(payload: dict[str, Any]) -> list[str]:
    pnl = payload.get("daily_pnl") or {}
    val = _fmt_usd(pnl.get("value_usd"))
    p = _pct(pnl.get("day_change_pct"), 2)
    u = _num(pnl.get("day_change_usd"))
    usd = ""
    if u is not None and _int_usd(abs(u)):
        usd = f" ({'+' if u >= 0 else '−'}{_int_usd(abs(u))})"
    head = "💼 *Ta journée*"
    tail = _join_dots([val, (f"P&L {p}{usd}" if p else None)])
    if not tail:
        return []
    head += f" · {tail}"
    lines = [head]
    movers = [m for m in (pnl.get("top_movers") or [])
              if isinstance(m, dict) and abs(_num(m.get("change")) or 0.0) >= 10]
    if movers:
        segs = [f"{str(m.get('symbol')).upper()} {_pct(m.get('change'))}"
                for m in movers[:3]]
        lines.append("Gros mouvements 24h : " + " · ".join(segs))
    return lines


# --------------------------------------------------------------------------- #
# ⚠️ / 📅 — surveiller : calendrier EXPLIQUÉ + niveau clé du soir
# --------------------------------------------------------------------------- #
def _event_sentence(e: dict[str, Any], payload: dict[str, Any]) -> str:
    """« CPI US (mardi) — chaud → …, froid → …. » + cote Polymarket UNIQUEMENT
    si l'événement est lié à la Fed/taux US (fini le « ~80% maintien » accolé
    à un discours BOE — audit 10/07)."""
    label = _event_label(e)
    explain = _event_explainer(e.get("label"))
    out = f"{label} — {explain}." if explain else f"{label} à surveiller."
    if _is_fed_related(e.get("label")):
        poly = _polymarket_fed(payload)
        if poly:
            out += f" {poly}."
    return out


def _watch_block(payload: dict[str, Any], kind: str) -> list[str]:
    if kind == "weekly":
        events = _upcoming_events(payload, 2)
        if not events:
            return []
        lines = ["📅 *La semaine à venir*"]
        lines.extend(f" • {_event_sentence(e, payload)}" for e in events)
        return lines

    if kind == "evening":
        sentences: list[str] = []
        row = _first_support_level(payload)
        if row:
            trig = _plain(row.get("trigger")) if row.get("trigger") else None
            if trig:
                # Le niveau vit déjà dans l'EN BREF (👁) : ici, la mécanique
                # seule (« Sous X → … ») — pas de « Niveau clé X — Sous X ».
                sentences.append(f"{trig.rstrip('.')}.")
            else:
                sentences.append(
                    f"Sous {row['level']}, risque de test du support suivant "
                    "(invaliderait la dynamique court terme).")
        ev = _upcoming_events(payload, 1)
        if ev:
            sentences.append(f"Ensuite : {_event_sentence(ev[0], payload)}")
        if not sentences:
            return []
        return ["⚠️ *Cette nuit / demain*", " ".join(sentences)]

    # morning
    ev = _upcoming_events(payload, 1)
    if ev:
        return ["⚠️ *À surveiller*", _event_sentence(ev[0], payload)]
    poly = _polymarket_fed(payload)
    if poly:
        return ["⚠️ *À surveiller*", f"{poly}."]
    return []


# --------------------------------------------------------------------------- #
# ↩️ La semaine passée (hebdo) — la rétro en une ligne
# --------------------------------------------------------------------------- #
def _weekly_retro_block(payload: dict[str, Any]) -> list[str]:
    calls = payload.get("calls_review") or {}
    if isinstance(calls, dict) and calls.get("summary_line"):
        return ["↩️ *La semaine passée*", _clip(calls["summary_line"], 220)]
    return []


def _commands_line(payload: dict[str, Any], kind: str) -> str:
    assets = _pick_assets(payload, kind, 2) or ["BTC", "ETH"]
    verbs = ["/pourquoi", "/analyse"]
    cmds = [f"{verbs[i % 2]} {a}" for i, a in enumerate(assets)]
    return "_" + " · ".join(cmds) + "_"


# --------------------------------------------------------------------------- #
# Assemblage
# --------------------------------------------------------------------------- #
def _build_digest(payload: dict[str, Any], kind: str) -> str:
    """Construit le briefing structuré (EN BREF + détail) pour un type de rapport."""
    payload = payload or {}
    header = payload.get("header") or {}
    when = _plain(header.get("time_casablanca") or header.get("date") or "")

    paras: list[str] = []
    title = f"{_KIND_LABELS.get(kind, 'Rapport')} · {when}".strip(" ·")
    paras.append(f"*{title}*")

    # 📌 EN BREF
    enbref = ["*📌 EN BREF*"]
    for line in (_verdict(payload, kind),
                 _prefix(_market_brief(payload, kind), "📊 "),
                 _enbref_line3(payload, kind)):
        if line:
            enbref.append(line)
    paras.append("\n".join(enbref))
    paras.append("──────────")

    # 🌍 Le marché (narratif IA réutilisé)
    nar = _market_narrative(payload, kind)
    if nar:
        paras.append(f"🌍 *{_MARKET_TITLE.get(kind, 'Le marché')}*\n{nar}")

    # Sections propres au type — ordre validé (Omar 12/07).
    blocks: list[list[str]] = []
    if kind == "morning":
        blocks = [_morning_action_block(payload), _morning_thesis_rows(payload)]
    elif kind == "evening":
        blocks = [_evening_action_block(payload), _evening_journey_block(payload)]
    else:
        blocks = [_weekly_action_block(payload), _weekly_thesis_rows(payload)]
    blocks.append(_watch_block(payload, kind))
    if kind == "weekly":
        blocks.append(_weekly_retro_block(payload))
    for block in blocks:
        if block:
            paras.append("\n".join(block))

    paras.append(_commands_line(payload, kind))
    return "\n\n".join(p for p in paras if p)


def push_report_notification(payload: dict[str, Any], kind: str) -> bool:
    """Pousse le briefing synthétique après génération d'un rapport.

    Args:
        payload: payload du rapport (mêmes données que le rendu mail).
        kind: 'morning' | 'evening' | 'weekly'.

    Returns:
        True si l'envoi a réussi (False si non configuré ou échec — non bloquant).
    """
    if not telegram_api.bot_configured():
        logger.info("Notification push ignorée : bot Telegram non configuré.")
        return False
    try:
        text = _build_digest(payload or {}, kind)
    except Exception as exc:  # noqa: BLE001 — le digest ne bloque jamais l'envoi
        logger.warning("Digest Telegram échoué (%s) — repli court.", exc)
        text = f"*{_KIND_LABELS.get(kind, 'Rapport')}* est prêt 📬"
    try:
        return telegram_api.send_message(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Notification push échouée (non bloquant) : %s", exc)
        return False
