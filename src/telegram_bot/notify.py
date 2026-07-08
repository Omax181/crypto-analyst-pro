"""Notifications push Telegram — DIGEST à VALEUR (v28 TG-refonte).

À chaque rapport (morning/evening/weekly), un digest structuré est poussé sur
Telegram. Objectif : qu'Omar sache l'essentiel en 30 s SANS ouvrir le mail, et
qu'il ouvre le mail seulement pour le « pourquoi » détaillé.

Structure en 2 zones (validée avec Omar) :

  📌 EN BREF  — 3 lignes autonomes, pensées pour l'aperçu de notification :
                ⚡ le verdict (action à faire OU rien), 📊 l'état du marché en
                une ligne, 💼/👁 l'argent (ou le niveau clé du soir).
  ──────────  — frontière « aperçu / détail ».
  Le détail  — 🌍 un vrai résumé du marché (réutilise l'analyse IA du mail,
                jamais robotique), puis les sections propres au type (actions,
                positions, plan), ⚠️ ce qu'il faut surveiller (relié au book),
                et 💬 les commandes personnalisées (/pourquoi <actif>).

100% Python depuis le payload (déterministe, zéro coût, zéro hallucination) : on
NE réutilise que des textes déjà rédigés par l'IA pour le mail (synthèse macro,
weekly_summary…), on ne génère jamais de nouvelle prose. Chaque bloc n'apparaît
que s'il a du contenu (pas de ligne « rien à signaler »).

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
    "evening": "Le marché ce soir",
    "weekly": "La semaine",
}
# Statut du bilan soir (déterministe) → pastille + libellé lisibles.
_BILAN_STATUS = {
    "on_track": "🟢 sur le bon chemin",
    "under_pressure": "⚠️ sous pression",
    "invalidated": "🔴 invalidée",
    "stable": "● stable",
    "pending": "● en attente",
}


# --------------------------------------------------------------------------- #
# Formatage (FR, robuste — ne lève jamais)
# --------------------------------------------------------------------------- #
def _num(v: Any) -> Optional[float]:
    try:
        if isinstance(v, str):
            v = v.replace(" ", "").replace(" ", "").replace(",", ".")
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
    s = _plain(text)
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)].rstrip(" ,;:·-") + "…"


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


# --------------------------------------------------------------------------- #
# Petits extracteurs partagés
# --------------------------------------------------------------------------- #
def _regime_label(payload: dict[str, Any]) -> Optional[str]:
    reg = payload.get("market_regime") or {}
    if isinstance(reg, dict) and reg.get("available"):
        return reg.get("label_fr") or reg.get("regime")
    return None


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


def _next_event_label(payload: dict[str, Any]) -> Optional[str]:
    """Prochain événement macro À VENIR (priorité aux « high », déjà-tombés
    exclus). Le libellé du « quand » s'adapte à la source (when / weekday)."""
    events = [e for e in _agenda_events(payload)
              if e.get("label") and not e.get("already_published")]
    if not events:
        return None
    ordered = ([e for e in events if e.get("importance") == "high"]
               + [e for e in events if e.get("importance") != "high"])
    e = ordered[0]
    when = e.get("when") or e.get("weekday_label") or e.get("date_label")
    return _plain(e["label"]) + (f" ({_plain(when)})" if when else "")


def _polymarket_fed(payload: dict[str, Any]) -> Optional[str]:
    """Ligne Polymarket Fed compacte, depuis morning (macro_context) ou soir
    (polymarket_facts)."""
    mc = payload.get("macro_context") or {}
    fb = mc.get("polymarket_fed_bars")
    if not isinstance(fb, dict):
        fb = (payload.get("polymarket_facts") or {}).get("fed_bars")
    if isinstance(fb, dict):
        dp = _num(fb.get("dominant_pct"))
        dom = fb.get("dominant")
        if dp is not None and dom:
            return f"~{dp:.0f}% {_plain(dom)} attendu (Polymarket)"
    return None


def _concentration_note(payload: dict[str, Any]) -> Optional[str]:
    """Signal de concentration DÉTERMINISTE depuis la heatmap (top position ≥
    25% du PTF) — jamais inventé."""
    for key in ("portfolio_heatmap", "portfolio_heatmap_7d"):
        hm = payload.get(key) or {}
        cells = hm.get("cells") if isinstance(hm, dict) else None
        if isinstance(cells, list) and cells:
            top = max(
                (c for c in cells if isinstance(c, dict)),
                key=lambda c: _num(c.get("ptf_pct")) or 0.0,
                default=None,
            )
            if top:
                p = _num(top.get("ptf_pct"))
                sym = top.get("symbol")
                if p is not None and sym and p >= 25:
                    return f"{sym} pèse ~{p:.0f}% du portefeuille"
    return None


def _pick_assets(payload: dict[str, Any], kind: str, n: int = 2) -> list[str]:
    """Actifs les plus pertinents du message, pour personnaliser /pourquoi."""
    out: list[str] = []

    def add(a: Any) -> None:
        s = str(a or "").upper().strip()
        if s and s not in out:
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
        for m in ((payload.get("daily_pnl") or {}).get("top_movers") or []):
            if isinstance(m, dict):
                add(m.get("symbol"))
    else:
        for p in (payload.get("positions_review") or []):
            if isinstance(p, dict):
                add(p.get("asset"))
    return out[:n]


# --------------------------------------------------------------------------- #
# 🌍 Le marché — narratif (réutilise l'analyse IA du mail, repli déterministe)
# --------------------------------------------------------------------------- #
def _market_fallback(payload: dict[str, Any]) -> Optional[str]:
    parts: list[Optional[str]] = []
    lbl = _regime_label(payload)
    if lbl:
        parts.append(f"Régime BTC {lbl}")
    mrr = payload.get("macro_regime_readout") or {}
    if isinstance(mrr, dict) and mrr.get("regime"):
        parts.append(f"macro {_plain(mrr['regime'])}")
    mc = payload.get("macro_context") or {}
    fg = mc.get("fear_greed")
    if fg is None:
        fg = (payload.get("evening_macro") or {}).get("fear_greed")
    if fg is not None:
        fgl = mc.get("fear_greed_label")
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
    """Paragraphe « Le marché » — priorité au texte IA déjà rédigé pour le mail."""
    if kind == "morning":
        txt = (payload.get("macro_context") or {}).get("regime_synthesis")
        if txt:
            return _clip(txt, 380)
        return _market_fallback(payload)

    if kind == "evening":
        # « marchés US inchangés » vit déjà dans l'EN BREF (📊) : on ne le
        # répète pas ici. On préfère le résumé « depuis le matin » (IA/Python),
        # sinon un repli régime + note US si séance fermée.
        smf = payload.get("since_morning_facts")
        if isinstance(smf, str) and smf.strip():
            return _clip(smf, 380)
        base = _market_fallback(payload)
        if (payload.get("header") or {}).get("us_market_open") is False:
            return _join_dots([base, "marchés US inchangés"]) or "Marchés US inchangés."
        return base

    # weekly
    txt = _weekly_summary_text(payload)
    if txt:
        return _clip(txt, 440)
    note = payload.get("regime_reconciliation_note")
    if note:
        return _clip(note, 360)
    return _market_fallback(payload)


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
            return "⚡ *Rien à faire aujourd'hui* — le portefeuille est déjà bien exposé."
        if top.get("line"):
            return f"⚡ *{_plain(top['line'])}*"
        firm = _first_firm_thesis_line(payload)
        if firm:
            return f"⚡ *{_plain(firm)}*"
        return "⚡ *Pas de geste prioritaire aujourd'hui.*"

    if kind == "evening":
        pnl = payload.get("daily_pnl") or {}
        p = _num(pnl.get("day_change_pct"))
        if p is None:
            return "⚡ *Bilan du jour prêt.*"
        qual = "blanche" if abs(p) < 0.3 else ("positive" if p > 0 else "sous pression")
        us_closed = (payload.get("header") or {}).get("us_market_open") is False
        if us_closed and abs(p) < 0.5:
            tail = "rien n'a changé depuis ce matin"
        elif abs(p) < 0.5:
            tail = "peu de mouvement sur la journée"
        else:
            tail = "portefeuille en hausse" if p > 0 else "portefeuille sous pression"
        return f"⚡ *Journée {qual} ({_pct(p, 2)})* — {tail}."

    # weekly
    snap = payload.get("portfolio_snapshot") or {}
    wk = _num(snap.get("weekly_pnl_pct"))
    vsb = _num(snap.get("vs_btc_7d_pct"))
    head = f"Semaine {_pct(wk)}" if wk is not None else "Bilan de la semaine"
    if wk is not None and vsb is not None:
        head += (", mieux que BTC" if vsb > 0.5
                 else ", moins bien que BTC" if vsb < -0.5
                 else ", en ligne avec BTC")
    lbl = _regime_label(payload)
    tail = f" — fond {lbl}" if lbl else ""
    return f"⚡ *{head}*{tail}."


def _market_brief(payload: dict[str, Any], kind: str) -> Optional[str]:
    lbl = _regime_label(payload)
    base = f"Fond {lbl}" if lbl else None

    if kind == "evening":
        if (payload.get("header") or {}).get("us_market_open") is False:
            return _join_dots([base, "marchés US inchangés"], sep=", ") or "Marchés US inchangés"
        return base

    ev = _next_event_label(payload)
    if kind == "weekly":
        return _join_dots([base, (f"{ev} en vue" if ev else None)])

    # morning
    if ev:
        return _join_dots([base, f"{ev} en vue"])
    fgl = (payload.get("macro_context") or {}).get("fear_greed_label")
    return _join_dots([base, (f"sentiment {_plain(fgl)}" if fgl else None)])


def _first_support_level(payload: dict[str, Any]) -> Optional[str]:
    for row in (payload.get("levels_tonight") or []):
        if isinstance(row, dict) and row.get("type") == "support" and row.get("level"):
            return str(row["level"])
    for row in (payload.get("levels_tonight") or []):
        if isinstance(row, dict) and row.get("level"):
            return str(row["level"])
    return None


def _positions_health_phrase(payload: dict[str, Any]) -> Optional[str]:
    rows = [r for r in (payload.get("active_recommendations_tracking") or [])
            if isinstance(r, dict)]
    if not rows:
        return None
    good = sum(1 for r in rows
               if str(r.get("health_status") or "").startswith(("🟢", "✅")))
    bad = sum(1 for r in rows
              if str(r.get("health_status") or "").startswith(("🔴", "⚠")))
    if bad and bad >= good:
        return "certaines positions sous pression"
    if good:
        return "tes positions avancent bien"
    return None


def _enbref_line3(payload: dict[str, Any], kind: str) -> Optional[str]:
    if kind == "evening":
        lv = _first_support_level(payload)
        return f"👁 Niveau clé : *{lv}*" if lv else None

    snap = payload.get("portfolio_snapshot") or {}
    val = _fmt_usd(snap.get("value_usd"))
    if kind == "weekly":
        bits = [val,
                (f"Semaine {_pct(snap.get('weekly_pnl_pct'))}"
                 if snap.get("weekly_pnl_pct") is not None else None),
                (f"vs BTC {_pct(snap.get('vs_btc_7d_pct'))}"
                 if snap.get("vs_btc_7d_pct") is not None else None)]
        body = _join_dots(bits)
        return f"💼 {body}" if body else None

    # morning
    d = _pct(snap.get("change_24h_pct"))
    base = _join_dots([val, (f"({d} / 24h)" if d else None)], sep=" ")
    if not base:
        return None
    ph = _positions_health_phrase(payload)
    return f"💼 {base}" + (f" · {ph}" if ph else "")


# --------------------------------------------------------------------------- #
# Sections « détail »
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

    asset = str(t.get("asset") or "?").upper()
    a = str(t.get("action") or "").upper()
    verb = ("renforcer" if "RENFORC" in a
            else "alléger" if ("ALLÉG" in a or "ALLEG" in a)
            else "on garde" if "MAINTEN" in a
            else "on surveille" if "SURVEIL" in a
            else (a.lower() or "—"))
    conf = _num(t.get("confidence"))
    confs = f" (conf. {conf:.0f}%)" if conf is not None else ""

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


def _actions_block(payload: dict[str, Any]) -> list[str]:
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
    if not picked:
        return []
    lines = ["🎯 *Ce qu'on fait*"]
    if (payload.get("top_action") or {}).get("is_nothing"):
        lines.append("Pas de nouvelle entrée.")
    lines.extend(_thesis_line(t) for t in picked)
    return lines


def _tracking_line(r: dict[str, Any]) -> str:
    asset = str(r.get("asset") or "?").upper()
    entry = _fmt_usd(r.get("entry_price"))
    cur = _fmt_usd(r.get("current_price"))
    move = f"{entry} → {cur}" if entry and cur else (cur or entry or "")
    prog = _pct(r.get("progress_pct"))
    progs = f" ({prog})" if prog else ""
    health = _plain(r.get("health_status"))
    tail = ""
    cm = r.get("comment")
    if cm:
        tail = f" · {_clip(cm, 90)}"
    body = f"{move}{progs}"
    if health:
        body += f" {health}"
    return f" • *{asset}* — {body}{tail}".rstrip()


def _tracking_block(payload: dict[str, Any]) -> list[str]:
    rows = [r for r in (payload.get("active_recommendations_tracking") or [])
            if isinstance(r, dict)]
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: abs(_num(r.get("progress_pct")) or 0.0),
                  reverse=True)[:3]
    return ["📈 *Tes positions*", *[_tracking_line(r) for r in rows]]


def _reco_bilan_line(b: dict[str, Any]) -> str:
    asset = str(b.get("asset") or "?").upper()
    conf = _num(b.get("confidence"))
    confs = f" (conf. {conf:.0f}%)" if conf is not None else ""
    entry = _fmt_usd(b.get("entry"))
    cur = _fmt_usd(b.get("current"))
    move = f"{entry} → {cur}" if entry and cur else (cur or "")
    d = _pct(b.get("delta_pct"), 2)
    head = _join_dots([move, (f"({d})" if d else None)], sep=" ")
    status = _BILAN_STATUS.get(str(b.get("status") or ""), "")
    reason = _clip(b.get("reason"), 110) if b.get("reason") else ""
    out = f" • *{asset}*{confs} — {head}"
    if status:
        out += f" {status}"
    if reason:
        out += f" · {reason}"
    return out.rstrip()


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
    if tail:
        head += f" · {tail}"
    lines = [head]

    movers = [m for m in (pnl.get("top_movers") or [])
              if isinstance(m, dict) and abs(_num(m.get("change")) or 0.0) >= 10]
    if movers:
        segs = [f"{str(m.get('symbol')).upper()} {_pct(m.get('change'))}"
                for m in movers[:3]]
        lines.append("Gros mouvements 24h : " + " · ".join(segs))

    for b in (payload.get("reco_bilan") or [])[:3]:
        if isinstance(b, dict):
            lines.append(_reco_bilan_line(b))
    return lines


def _weekly_plan_block(payload: dict[str, Any]) -> list[str]:
    scens = [s for s in (payload.get("scenarios") or []) if isinstance(s, dict)]
    dom = (max(scens, key=lambda s: _num(s.get("probability_pct")) or 0.0)
           if scens else None)
    if dom and dom.get("label"):
        prob = _num(dom.get("probability_pct"))
        probs = f" ({prob:.0f}%)" if prob is not None else ""
        lines = [f"🎯 *Le plan* — scénario {str(dom['label']).upper()}{probs}"]
    else:
        lines = ["🎯 *Le plan*"]
    plan = payload.get("weekly_action_plan") or []
    if plan and isinstance(plan[0], dict) and plan[0].get("action"):
        lines.append(f" • {_clip(plan[0]['action'], 240)}")
    calls = payload.get("calls_review") or {}
    if isinstance(calls, dict) and calls.get("summary_line"):
        lines.append(f"Semaine passée : {_clip(calls['summary_line'], 150)}")
    # On garde le bloc s'il porte un scénario (dans l'en-tête) OU une ligne utile.
    return lines if (dom or len(lines) > 1) else []


def _positions_review_line(p: dict[str, Any]) -> str:
    asset = str(p.get("asset") or "?").upper()
    bits: list[Optional[str]] = []
    pru = _pct(p.get("pru_pct"))
    if pru:
        bits.append(f"{pru} vs PRU")
    st = _plain(p.get("lt_status"))
    if st:
        bits.append(st)
    head = _join_dots(bits, sep=", ")
    analysis = _clip(p.get("analysis"), 100) if p.get("analysis") else None

    tk = _plain(p.get("lt_target_kind"))
    tl = _fmt_usd(p.get("lt_target_low"))
    th = _fmt_usd(p.get("lt_target_high"))
    tt = _fmt_usd(p.get("lt_target"))
    if tl and th:
        tgt = _join_dots([f"cible {tk}".strip(), f"{tl}–{th}"], sep=" ")
    elif tt:
        tgt = _join_dots([f"cible {tk}".strip(), tt], sep=" ")
    else:
        tgt = None

    seg = _join_dots([head, analysis, tgt])
    return f" • *{asset}* — {_clip(seg, 220)}" if seg else f" • *{asset}*"


def _weekly_positions_block(payload: dict[str, Any]) -> list[str]:
    rows = [p for p in (payload.get("positions_review") or []) if isinstance(p, dict)]
    if not rows:
        return []

    def _notable(p: dict[str, Any]) -> bool:
        return bool(p.get("conviction")
                    or (p.get("h30") or {}).get("status") in ("in_progress", "validated")
                    or abs(_num(p.get("pru_pct")) or 0.0) >= 30)

    picked = [p for p in rows if _notable(p)] or rows
    return ["📌 *Tes positions à suivre*",
            *[_positions_review_line(p) for p in picked[:3]]]


def _watch_block(payload: dict[str, Any], kind: str) -> list[str]:
    label = {"morning": "⚠️ *À surveiller*",
             "evening": "⚠️ *Cette nuit / demain*",
             "weekly": "📅 *La semaine à venir*"}[kind]
    sentences: list[str] = []

    if kind == "evening":
        lv = _first_support_level(payload)
        if lv:
            sentences.append(
                f"Sous {lv}, risque de test du support suivant "
                "(invaliderait la dynamique court terme).")

    ev = _next_event_label(payload)
    poly = _polymarket_fed(payload)
    if ev and poly:
        sentences.append(f"{ev} — {poly}.")
    elif ev:
        sentences.append(f"{ev} à surveiller.")
    elif poly:
        sentences.append(f"{poly}.")

    conc = _concentration_note(payload)
    if conc:
        sentences.append(
            f"Book concentré ({conc}) : une cassure pèserait plus que la moyenne.")

    if not sentences:
        return []
    return [label, " ".join(sentences)]


def _commands_line(payload: dict[str, Any], kind: str) -> str:
    assets = _pick_assets(payload, kind, 2) or ["BTC", "ETH"]
    verbs = ["/pourquoi", "/analyse"]
    cmds = [f"{verbs[i % 2]} {a}" for i, a in enumerate(assets)]
    return "_" + " · ".join(cmds) + "_"


# --------------------------------------------------------------------------- #
# Assemblage
# --------------------------------------------------------------------------- #
def _build_digest(payload: dict[str, Any], kind: str) -> str:
    """Construit le digest structuré (EN BREF + détail) pour un type de rapport."""
    payload = payload or {}
    header = payload.get("header") or {}
    when = header.get("time_casablanca") or header.get("date") or ""

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

    # 🌍 Le marché (narratif)
    nar = _market_narrative(payload, kind)
    if nar:
        paras.append(f"🌍 *{_MARKET_TITLE.get(kind, 'Le marché')}*\n{nar}")

    # Sections propres au type
    blocks: list[list[str]] = []
    if kind == "morning":
        blocks = [_actions_block(payload), _tracking_block(payload)]
    elif kind == "evening":
        blocks = [_evening_journey_block(payload)]
    else:
        blocks = [_weekly_plan_block(payload), _weekly_positions_block(payload)]
    blocks.append(_watch_block(payload, kind))
    for block in blocks:
        if block:
            paras.append("\n".join(block))

    paras.append(_commands_line(payload, kind))
    return "\n\n".join(p for p in paras if p)


def _prefix(text: Optional[str], pfx: str) -> Optional[str]:
    return f"{pfx}{text}" if text else None


def push_report_notification(payload: dict[str, Any], kind: str) -> bool:
    """Pousse le digest synthétique après génération d'un rapport.

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
