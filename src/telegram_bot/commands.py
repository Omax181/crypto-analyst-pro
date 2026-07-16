"""Commandes structurées du bot Telegram (Chantier G).

Deux familles :
  • Commandes d'ÉTAT (/dismiss, /validate, /snooze) : modifient un fichier JSON
    du state (recommandations), puis sont committées + poussées par le workflow.
  • Commandes de LECTURE (/recos, /ptf, /risque, /resume, /aide) : produisent une
    réponse directe à partir du contexte, sans IA (rapide et déterministe).

Tout le reste (langage naturel, /ask) est délégué à Gemini avec le contexte
complet (cf. assistant.py).
"""

from __future__ import annotations

from typing import Any, Optional

from src.state import report_memory as mem
from src.utils.logger import get_logger

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Détection
# --------------------------------------------------------------------------- #
_STATE_COMMANDS = {"/dismiss", "/validate", "/snooze", "/remember", "/forget"}
_READ_COMMANDS = {"/recos", "/ptf", "/portefeuille", "/pos", "/positions",
                  "/risque", "/resume", "/résumé", "/aide", "/help", "/start",
                  "/macro", "/memory", "/memoire", "/mémoire",
                  # v27 (TG2/TG5) — analyse à la demande + explication de reco.
                  "/analyse", "/analyze", "/pourquoi", "/why",
                  # OB18 — suivi / track record consultable (mobile).
                  "/suivi", "/historique", "/bilan", "/track"}


def is_command(text: str) -> bool:
    return text.strip().startswith("/")


def parse_command(text: str) -> tuple[str, list[str]]:
    """Découpe '/cmd arg1 arg2' en (cmd_lower, [args])."""
    parts = text.strip().split()
    cmd = parts[0].lower() if parts else ""
    return cmd, parts[1:]


def is_state_command(text: str) -> bool:
    cmd, _ = parse_command(text)
    return cmd in _STATE_COMMANDS


# --------------------------------------------------------------------------- #
# Commandes d'état (modifient le state → commit/push par le workflow)
# --------------------------------------------------------------------------- #
def _find_reco(recos: list[dict[str, Any]], token: str) -> Optional[dict[str, Any]]:
    """Trouve une reco par symbole (insensible à la casse) ou par id."""
    token_up = token.upper()
    for r in recos:
        if (r.get("asset") or "").upper() == token_up or r.get("id") == token:
            return r
    return None


def handle_state_command(text: str) -> tuple[str, bool]:
    """Exécute /dismiss, /validate ou /snooze sur une reco.

    Args:
        text: message complet (ex. '/dismiss TAO').

    Returns:
        Tuple ``(reponse, state_modifie)``.
    """
    cmd, args = parse_command(text)

    # v21 — mémoire durable manuelle.
    if cmd == "/remember":
        fact = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
        if not fact:
            return ("Utilisation : `/remember <fait à retenir>` "
                    "(ex. `/remember accumuler ETH sous 1500`).", False)
        mem.append_bot_memory("note", fact)
        return (f"🧠 Mémorisé : {fact}", True)
    if cmd == "/forget":
        if not args or not args[0].isdigit():
            return ("Utilisation : `/forget <numéro>` (vois les numéros avec "
                    "`/memory`).", False)
        idx = int(args[0]) - 1  # affiché 1-based
        ok = mem.remove_bot_memory(idx)
        return (("🗑️ Entrée oubliée." if ok else
                 "Numéro introuvable. Tape `/memory` pour la liste."), ok)

    if not args:
        return (f"Utilisation : `{cmd} SYMBOLE` (ex. `{cmd} TAO`).", False)

    token = args[0]
    recos = mem.load_active_recommendations()
    reco = _find_reco(recos, token)
    if not reco:
        return (f"Aucune reco active trouvée pour « {token} ». "
                f"Tape /recos pour voir la liste.", False)

    asset = reco.get("asset", token)
    if cmd == "/dismiss":
        # Retire la reco de la liste active (abandon manuel) + TRACE la décision
        # (traçabilité Partie 6), ce qui empêche aussi sa ré-émission immédiate
        # par le matin (cohérence).
        recos = [r for r in recos if r is not reco]
        mem.save_active_recommendations(recos)
        mem.record_reco_dismissal(asset, reco.get("action"), reco.get("id"))
        mem.append_bot_memory("decision", f"Reco {asset} écartée (dismiss).")
        return (f"✅ Reco {asset} écartée (dismiss). Elle ne sera plus suivie "
                "ni ré-émise dans les 48h.", True)

    if cmd == "/validate":
        # Marque la reco comme validée manuellement (clôture gagnante).
        reco["status"] = "validated"
        reco["closed_manually"] = True
        recos = [r for r in recos if r is not reco]
        mem.save_active_recommendations(recos)
        # Archive dans l'historique de prédictions pour le scoring.
        hist = mem.load_prediction_history()
        reco_closed = dict(reco)
        hist.append(reco_closed)
        mem.save_prediction_history(hist)
        mem.append_bot_memory("decision", f"Reco {asset} validée (gagnante, archivée).")
        return (f"✅ Reco {asset} validée et archivée (comptera dans le win rate).", True)

    if cmd == "/snooze":
        # Met en pause : on tag la reco (le moteur peut l'ignorer un temps).
        reco["snoozed"] = True
        mem.save_active_recommendations(recos)
        mem.append_bot_memory("decision", f"Reco {asset} mise en pause (snooze).")
        return (f"😴 Reco {asset} mise en pause (snooze). "
                f"Elle reste active mais signalée comme à revisiter.", True)

    return (f"Commande d'état inconnue : {cmd}.", False)


# --------------------------------------------------------------------------- #
# Commandes de lecture (réponse directe, sans IA)
# --------------------------------------------------------------------------- #
def _fmt_usd(v: Any) -> str:
    try:
        # v30.1 (ré-audit #67) — format FR (« 1 234,56 $ »), plus « $1,234.56 ».
        return (f"{float(v):,.2f}".replace(",", " ")
                .replace(".", ",") + " $")
    except (ValueError, TypeError):
        return "—"


def _cmd_recos() -> str:
    recos = mem.load_active_recommendations()
    if not recos:
        return "Aucune recommandation active pour le moment."
    lines = ["*Recos actives :*"]
    for r in recos:
        asset = r.get("asset", "?")
        action = r.get("action", "?")
        status = r.get("status") or "en cours"
        tag = " 😴" if r.get("snoozed") else ""
        lines.append(f"• {asset} — {action} ({status}){tag}")
    lines.append("\n_Actions : /validate SYM · /dismiss SYM · /snooze SYM_")
    return "\n".join(lines)


def _cmd_portfolio() -> str:
    # v18.1 — valorisation LIVE (quantité × prix courant) si disponible ; repli
    # sur la baseline du YAML sinon. Plus de « valeur figée » trompeuse.
    live = {}
    try:
        from src.telegram_bot.live_data import get_live_portfolio_snapshot
        live = get_live_portfolio_snapshot()
    except Exception:  # noqa: BLE001
        live = {}
    if live.get("available") and live.get("positions"):
        rows = live["positions"]
        total = live.get("total_value_usd") or 0
        priced = live.get("positions_priced_live") or 0
        n = live.get("positions_total") or len(rows)
        header = f"*Portefeuille* ({n} positions · ~{_fmt_usd(total)} live)"
        # P&L latent total vs PRU (v21).
        pnl_usd, pnl_pct = live.get("pnl_usd"), live.get("pnl_pct")
        if isinstance(pnl_usd, (int, float)) and isinstance(pnl_pct, (int, float)):
            arrow = "🟢" if pnl_usd >= 0 else "🔴"
            header += (f"\n{arrow} P&L latent : "
                       + f"{pnl_usd:+,.0f}".replace(",", " ") + " $ ("
                       + f"{pnl_pct:+.1f}".replace(".", ",") + "%) vs PRU")
        lines = [header + " :"]
        for r in rows[:12]:
            pct = r.get("weight_pct")
            ch = r.get("change_24h")
            pl = r.get("pnl_pct")
            ch_txt = (f" · {ch:+.1f}%/24h".replace(".", ",")
                      if isinstance(ch, (int, float)) else "")
            pct_txt = f" ({pct:.0f}%)" if isinstance(pct, (int, float)) else ""
            pl_txt = f" · PRU {pl:+.0f}%" if isinstance(pl, (int, float)) else ""
            lines.append(
                f"• {r['symbol']} · {_fmt_usd(r['value_usd'])}{pct_txt}{pl_txt}{ch_txt}")
        if len(rows) > 12:
            lines.append(f"… et {len(rows) - 12} autres positions.")
        lines.append(f"\n_Valorisation live ({priced}/{n} positions au prix "
                     "courant) · PRU = coût moyen. Pose une question en langage "
                     "naturel pour l'analyse._")
        return "\n".join(lines)

    # Repli baseline (prix live indisponibles).
    try:
        from src.utils.portfolio_loader import load_portfolio
        pf = load_portfolio()
    except Exception:  # noqa: BLE001
        return "Portefeuille indisponible."
    positions = (pf.get("portfolio") or {})
    if not positions:
        return "Portefeuille vide."
    rows2 = sorted(
        positions.items(),
        key=lambda kv: kv[1].get("value_usd", 0) or 0, reverse=True,
    )
    total = sum((i.get("value_usd", 0) or 0) for _, i in rows2)
    lines = [f"*Portefeuille* ({len(rows2)} positions · ~{_fmt_usd(total)} baseline) :"]
    for sym, info in rows2[:12]:
        val = info.get("value_usd", 0) or 0
        pct = (val / total * 100) if total else 0
        lines.append(f"• {sym} · {_fmt_usd(val)} ({pct:.0f}%)")
    if len(rows2) > 12:
        lines.append(f"… et {len(rows2) - 12} autres positions.")
    lines.append("\n_Valeurs baseline (prix live indisponibles). Pour l'analyse, "
                 "pose ta question en langage naturel._")
    return "\n".join(lines)


def _cmd_risk() -> str:
    rep = mem.load_morning_report() or mem.load_evening_report() or {}
    risk = rep.get("risk_score") or {}
    if not risk:
        return ("Pas de score de risque dans le dernier rapport. "
                "Pose ta question en langage naturel pour une analyse à jour.")
    score = risk.get("score")
    level = risk.get("level", "")
    lines = [f"*Risque PTF :* {score}/10 ({level})"]
    for c in (risk.get("components") or [])[:6]:
        lines.append(f"• {c.get('label')} : {c.get('pts')}/{c.get('max')}")
    return "\n".join(lines)


def _summary_text(rep: dict[str, Any]) -> Optional[str]:
    """Extrait une synthèse LISIBLE d'un rapport.

    ``executive_summary`` est un OBJET ``{"bullets": [{"icon","text"}]}`` (format
    v15) : l'ancien handler faisait ``str(summary)`` dessus et affichait le dict
    Python brut. On rend ici les puces proprement, avec repli sur ``synthesis``
    puis sur le readout macro.
    """
    ex = rep.get("executive_summary")
    if isinstance(ex, dict):
        lines: list[str] = []
        for b in (ex.get("bullets") or []):
            if isinstance(b, dict) and b.get("text"):
                lines.append(f"{b.get('icon') or '•'} {str(b['text']).strip()}")
            elif isinstance(b, str) and b.strip():
                lines.append(f"• {b.strip()}")
        if lines:
            return "\n".join(lines)
    elif isinstance(ex, str) and ex.strip():
        return ex.strip()
    syn = rep.get("synthesis")
    if isinstance(syn, str) and syn.strip():
        return syn.strip()
    if isinstance(syn, dict) and (syn.get("reading") or syn.get("text")):
        return str(syn.get("reading") or syn.get("text")).strip()
    rr = rep.get("macro_regime_readout")
    if isinstance(rr, dict) and rr.get("reading"):
        return str(rr["reading"]).strip()
    return None


def _cmd_resume() -> str:
    rep = mem.load_morning_report() or mem.load_evening_report() or {}
    if not rep:
        return ("Aucun rapport récent en mémoire. Les rapports arrivent par mail "
                "matin/soir et le bilan hebdo le dimanche.")
    summary = _summary_text(rep)
    if not summary:
        return ("Le dernier rapport n'a pas de synthèse courte exploitable — "
                "pose-moi une question précise.")
    kind = (rep.get("header") or {}).get("date", "")
    return f"*Synthèse du dernier rapport* {('· ' + kind) if kind else ''}\n{summary}"


def _cmd_macro() -> str:
    rep = mem.load_morning_report() or {}
    macro = rep.get("macro_context") or {}
    if not macro:
        return "Pas de contexte macro récent. Pose ta question en langage naturel."
    bits = []
    if macro.get("btc_price") is not None:
        bits.append("BTC " + f"{macro['btc_price']:,.0f}".replace(",", " ") + " $")
    if macro.get("fear_greed") is not None:
        bits.append(f"F&G {macro['fear_greed']}")
    if macro.get("dxy") is not None:
        bits.append(f"DXY {macro['dxy']}")
    if macro.get("vix") is not None:
        bits.append(f"VIX {macro['vix']}")
    return "*Macro (dernier rapport) :* " + " · ".join(bits) if bits else \
        "Contexte macro indisponible."


def _cmd_memory() -> str:
    mems = mem.load_bot_memory()
    if not mems:
        return ("Aucune mémoire durable pour l'instant. Ajoute un fait avec "
                "`/remember <texte>` (ex. `/remember accumuler ETH sous 1500`).")
    lines = ["*Mémoire durable :*"]
    start = max(0, len(mems) - 20)  # on affiche les 20 plus récentes
    for i in range(start, len(mems)):
        m = mems[i]
        ts = (m.get("ts") or "")[:10]
        tag = {"decision": "📌", "note": "🧠",
               "preference": "⭐"}.get(m.get("kind"), "•")
        # Numéro = index RÉEL +1 (cohérent avec /forget).
        lines.append(f"{i + 1}. {tag} {m.get('text', '')}  _{ts}_")
    lines.append("\n_Oublie une entrée : `/forget <numéro>`_")
    return "\n".join(lines)


def _cmd_help() -> str:
    return (
        "*Assistant Crypto Analyst Pro* 🤖\n"
        "Parle-moi normalement — je connais ton portefeuille, tes recos et les "
        "rapports du jour.\n\n"
        "*Commandes lecture :*\n"
        "/recos — recos actives\n"
        "/analyse SYM — plan complet à la demande (niveaux, R:R, scénarios)\n"
        "/pourquoi SYM — la thèse derrière une reco active\n"
        "/ptf — portefeuille\n"
        "/risque — score de risque PTF\n"
        "/resume — synthèse du dernier rapport\n"
        "/suivi — track record : win rate, espérance, recos actives\n"
        "/macro — contexte macro\n\n"
        "*Gestion des recos :*\n"
        "/validate SYM — valider une reco\n"
        "/dismiss SYM — écarter une reco\n"
        "/snooze SYM — mettre en pause\n\n"
        "*Édition du portefeuille* (mot de passe requis) :\n"
        "/buy SYM QTÉ PRIX <mdp> — achat (recalcule le PRU)\n"
        "/sell SYM QTÉ <mdp> — vente\n"
        "/set SYM QTÉ <mdp> — fixer la quantité\n"
        "_ou en langage naturel : « j'ai acheté 0,1 ETH à 1600 <mdp> »_\n\n"
        "*Mémoire durable :*\n"
        "/memory — ce que je retiens (décisions, notes)\n"
        "/remember <fait> — me faire retenir un fait\n"
        "/forget <n> — oublier l'entrée n\n\n"
        "*Recherche & IA :*\n"
        "/recherche <sujet> — recherche web actu (ex. `/recherche news ETF ETH`)\n"
        "/ask <question> — force l'analyse IA\n\n"
        "*Exemples en langage naturel :*\n"
        "« combien vaut mon PTF maintenant ? »\n"
        "« est-ce le bon moment pour renforcer ETH ? »\n"
        "« quel est mon risque si BTC chute de 15% ? »\n"
        "« qu'est-ce que je dois faire ce soir ? »"
    )


def _cmd_analyse(args: list[str]) -> str:
    """v27 (TG2) — /analyse SYM : plan complet à la demande pour tout actif.

    Prix + readout technique + S/R calculés + plan (invalidation, cible 30j,
    R:R, EV, zone d'accumulation) + dérivés. 100% déterministe (mêmes moteurs
    que les mails) : aucune hallucination possible.
    """
    if not args:
        return "Utilisation : `/analyse SYM` (ex. `/analyse TAO`)."
    sym = args[0].upper().lstrip("$")
    try:
        from src.reporting.charts import _load_series
        from src.analytics import key_levels as _kl
        from src.analytics.asset_plan import compute_asset_plan
        series = _load_series(sym, days=180)
        if not series or not series.get("closes"):
            return (f"Pas de série de prix pour *{sym}* (ticker inconnu de "
                    "CoinGecko ou source indisponible). Vérifie le symbole.")
        closes = series["closes"]
        kl = _kl.compute_key_levels(sym, closes, series.get("volumes"),
                                    price=closes[-1])
        funding = None
        try:
            from src.data_sources import binance_futures as _bf
            _d = _bf.get_derivatives(sym)
            if _d.get("available"):
                funding = _d.get("funding_annualized_pct")
        except Exception:  # noqa: BLE001
            funding = None
        plan = compute_asset_plan(sym, closes, price=closes[-1],
                                  funding_annualized_pct=funding,
                                  key_levels_result=kl)
        if not plan.get("available"):
            return f"Analyse indisponible pour *{sym}* : {plan.get('reason')}."
        lines = [f"*🔎 {sym} · {plan.get('price_label')}*"]
        if kl.get("readout_line"):
            lines.append(kl["readout_line"])
        sups = kl.get("supports") or []
        ress = kl.get("resistances") or []
        if sups:
            lines.append("Supports : " + " · ".join(
                f"{s['level_label']} ({s['basis']})" for s in sups[:2]))
        if ress:
            lines.append("Résistances : " + " · ".join(
                f"{r['level_label']} ({r['basis']})" for r in ress[:2]))
        lines.append(f"📐 {plan['plan_line']}")
        sc = plan.get("scenarios") or {}
        if sc:
            lines.append(
                f"Scénarios 30j : bull {sc['bull']['probability_pct']}% "
                f"→ {sc['bull']['level_label']} · base "
                f"{sc['base']['probability_pct']}% · bear "
                f"{sc['bear']['probability_pct']}% → {sc['bear']['level_label']}")
        if funding is not None:
            lines.append(f"Funding {funding:+.1f}%/an".replace(".", ","))
        # Contexte PTF : position détenue ?
        try:
            from src.utils.portfolio_loader import load_portfolio
            pos = (load_portfolio().get("portfolio") or {}).get(sym)
            if pos and pos.get("pru"):
                lines.append("💼 En PTF · PRU "
                             + f"{float(pos['pru']):,.4f}".replace(",", " ")
                             .replace(".", ",") + " $")
            elif pos:
                lines.append("💼 En portefeuille")
        except Exception:  # noqa: BLE001
            pass
        lines.append("_Niveaux calculés (pivots/MM/Fibo/Bollinger) · EV indicatif._")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        logger.warning("/analyse %s échoué : %s", sym, exc)
        return f"Analyse de {sym} momentanément indisponible ({exc})."


def _cmd_pourquoi(args: list[str]) -> str:
    """v27 (TG5) — /pourquoi SYM : développe la thèse derrière une reco active.

    Reco (entrée, date, stop, cible, confiance) + observation/raisonnement de
    la thèse du matin + contre-thèse. Sans reco active : le dit honnêtement
    et oriente vers /analyse.
    """
    if not args:
        return "Utilisation : `/pourquoi SYM` (ex. `/pourquoi TAO`)."
    sym = args[0].upper().lstrip("$")
    from src.tracking.prediction_scoring import latest_open_reco_by_asset
    recos = latest_open_reco_by_asset(mem.load_active_recommendations())
    reco = recos.get(sym)
    if not reco:
        return (f"Aucune reco active sur *{sym}*. "
                f"Tape `/analyse {sym}` pour un plan à la demande.")
    lines = [f"*🎯 Pourquoi {reco.get('action', '—')} {sym} ?*"]
    _meta = []
    if reco.get("created_at"):
        _meta.append(f"émise le {str(reco['created_at'])[:10]}")
    if reco.get("entry_price"):
        _meta.append(f"entrée {reco['entry_price']}")
    if reco.get("stop_loss"):
        _meta.append(f"invalidation {reco['stop_loss']}")
    if reco.get("ct_target") or reco.get("target_price"):
        _meta.append(f"cible {reco.get('ct_target') or reco.get('target_price')}")
    if reco.get("confidence"):
        _meta.append(f"confiance {reco['confidence']}%")
    if _meta:
        lines.append(" · ".join(str(m) for m in _meta))
    # La thèse d'origine (rapport du matin persisté).
    morning = mem.load_morning_report() or {}
    thesis = next(
        (t for t in (morning.get("thesis_of_the_day") or [])
         if isinstance(t, dict) and (t.get("asset") or "").upper() == sym),
        None,
    )
    if thesis:
        if thesis.get("observation"):
            lines.append(f"👁 {str(thesis['observation'])[:350]}")
        for i, s in enumerate((thesis.get("reasoning_signals") or [])[:3], 1):
            lines.append(f"{i}. {str(s)[:160]}")
        if thesis.get("counter_thesis"):
            lines.append(f"⚖️ Contre-thèse : {str(thesis['counter_thesis'])[:200]}")
        if thesis.get("plan_line"):
            lines.append(f"📐 {thesis['plan_line']}")
    else:
        lines.append("_Thèse détaillée du matin non retrouvée dans le state — "
                     f"`/analyse {sym}` pour l'état des lieux actuel._")
    return "\n".join(lines)


def _cmd_suivi() -> str:
    """OB18 — SUIVI / TRACK RECORD consultable (mobile-first, 100 % déterministe).

    Répond à « comment se comportent mes recos dans le temps ? » sans quitter
    Telegram : win rate, espérance, recos actives, leçon récente. Best-effort —
    chaque bloc est protégé, un historique vide affiche un message honnête.
    """
    from src.tracking.prediction_scoring import PredictionTracker
    lines = ["*📊 Suivi & track record*"]
    tk: Optional[PredictionTracker]
    try:
        tk = PredictionTracker()
    except Exception:  # noqa: BLE001
        tk = None
    if tk is not None:
        try:
            wr = tk.compute_win_rate(90)
            if wr.get("total"):
                lines.append(
                    f"• Clôturées 90j : *{wr.get('win_rate_pct')}%* de réussite "
                    f"({wr.get('validated')} ✓ / {wr.get('invalidated')} ✗)")
            else:
                lines.append("• Clôturées 90j : aucune encore "
                             "(historique en constitution)")
        except Exception:  # noqa: BLE001
            pass
        try:
            ex = tk.compute_expectancy(90)
            if ex.get("available") and ex.get("expectancy_pct") is not None:
                _exp_fr = f"{ex['expectancy_pct']:+.1f}".replace(".", ",")
                lines.append(
                    f"• Espérance : *{_exp_fr}%* / reco "
                    f"(éch. {ex.get('sample')})")
                if ex.get("reading"):
                    lines.append(f"  _{str(ex['reading'])[:160]}_")
        except Exception:  # noqa: BLE001
            pass
    try:
        recos = mem.load_active_recommendations() or []
        if recos:
            lines.append(f"• Recos actives : *{len(recos)}*")
            for r in recos[:5]:
                lines.append(
                    f"   – {r.get('asset', '?')} · {r.get('action', '?')} "
                    f"({r.get('status') or 'en cours'})")
        else:
            lines.append("• Recos actives : aucune")
    except Exception:  # noqa: BLE001
        pass
    if tk is not None:
        try:
            lesson = tk.extract_lesson(30)
            if lesson and str(lesson).strip():
                lines.append(f"💡 {str(lesson).strip()[:300]}")
        except Exception:  # noqa: BLE001
            pass
    lines.append("\n_Détail : /recos · /ptf · /risque_")
    return "\n".join(lines)


def handle_read_command(text: str) -> Optional[str]:
    """Exécute une commande de lecture ; renvoie None si ce n'en est pas une."""
    cmd, _args = parse_command(text)
    if cmd in ("/analyse", "/analyze"):
        return _cmd_analyse(_args)
    if cmd in ("/pourquoi", "/why"):
        return _cmd_pourquoi(_args)
    if cmd in ("/recos",):
        return _cmd_recos()
    if cmd in ("/ptf", "/portefeuille", "/pos", "/positions"):
        return _cmd_portfolio()
    if cmd in ("/risque",):
        return _cmd_risk()
    if cmd in ("/resume", "/résumé"):
        return _cmd_resume()
    if cmd in ("/macro",):
        return _cmd_macro()
    if cmd in ("/memory", "/memoire", "/mémoire"):
        return _cmd_memory()
    if cmd in ("/suivi", "/historique", "/bilan", "/track"):
        return _cmd_suivi()
    if cmd in ("/aide", "/help", "/start"):
        return _cmd_help()
    return None
