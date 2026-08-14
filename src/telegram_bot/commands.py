"""Commandes du bot — SPEC V31 §2, §3 (I45, I46, I47).

Le bot est un LECTEUR du carnet. Une seule commande écrit : ``/dismiss``, qui
produit la transition CANCELLED — l'unique écriture autorisée hors du run du
matin, parce qu'elle traduit une décision explicite de l'utilisateur.

Les commandes ``/validate`` et ``/snooze`` de la v30 sont supprimées :
  - « valider » une recommandation à la main contournait la machine à états
    (une issue ne se décrète pas, elle s'observe sur la clôture) ;
  - « snoozer » reposait sur SUSPENDED, état retiré de la SPEC.

Aucun formatage local : tout nombre passe par ``core.formatter`` (I27).
"""

from __future__ import annotations

import html
from typing import Any, Optional

from src.core import formatter as fmt
from src.core import runlog
from src.core.book import RecommendationBook
from src.state import bot_memory as mem
from src.utils.logger import get_logger

logger = get_logger(__name__)

STATE_COMMANDS = {"/dismiss", "/remember", "/forget"}
READ_COMMANDS = {"/carnet", "/recos", "/ptf", "/portefeuille", "/positions",
                 "/sources", "/resume", "/memoire", "/aide", "/help", "/start"}


def _esc(v: Any) -> str:
    return html.escape(str(v if v is not None else ""), quote=False)


def is_command(text: str) -> bool:
    return isinstance(text, str) and text.strip().startswith("/")


def parse_command(text: str) -> tuple[str, list[str]]:
    parts = (text or "").strip().split()
    if not parts:
        return "", []
    return parts[0].lower().split("@")[0], parts[1:]


def is_state_command(text: str) -> bool:
    return parse_command(text)[0] in STATE_COMMANDS


def _book() -> RecommendationBook:
    """Carnet ouvert en LECTURE. ``/dismiss`` seul lève l'interdiction (§3)."""
    return RecommendationBook(run_kind="bot", run_id="telegram")


# ── commandes d'écriture ──────────────────────────────────────────────────

def handle_state_command(text: str) -> tuple[str, bool]:
    """Retourne (réponse, état_modifié)."""
    cmd, args = parse_command(text)

    if cmd == "/dismiss":
        if not args:
            return "Usage : <code>/dismiss ACTIF</code>", False
        asset = args[0].upper()
        book = _book()
        n = book.cancel(asset)
        if not n:
            return f"Aucun contrat en cours sur {_esc(asset)}.", False
        # ``cancel`` a levé l'interdiction d'écriture le temps de la transition ;
        # le commit demande la même levée explicite, jamais implicite.
        book.writable = True
        book.commit()
        mem.append_bot_memory("decision", f"Contrat {asset} annulé (/dismiss).")
        return (f"{_esc(asset)} : {fmt.integer(n)} contrat(s) annulé(s). "
                f"Ils sortent du scoring — un contrat annulé ne compte "
                f"ni en réussite ni en échec."), True

    if cmd == "/remember":
        fact = " ".join(args).strip()
        if not fact:
            return "Usage : <code>/remember ta note</code>", False
        mem.append_bot_memory("note", fact)
        return "Noté.", True

    if cmd == "/forget":
        if not args or not args[0].lstrip("-").isdigit():
            return "Usage : <code>/forget N</code> (voir <code>/memoire</code>)", False
        idx = int(args[0]) - 1
        return ("Oublié." if mem.remove_bot_memory(idx)
                else "Index inconnu."), True

    return "Commande d'état inconnue.", False


# ── commandes de lecture ──────────────────────────────────────────────────

def _cmd_carnet() -> str:
    book = _book()
    active = book.active()
    if not active:
        return ("Carnet vide : aucun contrat en cours.\n"
                "Un carnet vide n'est pas une anomalie — c'est l'absence de "
                "geste qui franchit les conditions de viabilité.")
    lines = [f"<b>Carnet</b> · {fmt.integer(len(active))} contrat(s)"]
    for rec in active:
        cur = (rec.tracking or {}).get("current_price")
        delta = None
        if isinstance(cur, (int, float)) and rec.entry:
            delta = (cur - rec.entry) / rec.entry * 100.0
        lines.append(
            f"\n<b>{_esc(rec.asset)}</b> · {_esc(rec.horizon)} · "
            f"{fmt.days((rec.tracking or {}).get('days_elapsed'))}")
        lines.append(
            f"entrée {fmt.price(rec.entry)} · cours {fmt.price(cur)} "
            f"({fmt.pct(delta)})")
        lines.append(
            f"cible {fmt.price(rec.target)} · invalidation {fmt.price(rec.stop)} "
            f"· taille {fmt.usd(rec.notional)}")
        lines.append(f"échéance {fmt.date_fr(rec.scored_contract.get('expires_at'))}")
    return "\n".join(lines)


def _cmd_portfolio() -> str:
    from src.telegram_bot.live_data import get_live_portfolio_snapshot
    snap = get_live_portfolio_snapshot()
    if not snap.get("available"):
        return f"Portefeuille indisponible : {_esc(snap.get('reason'))}."
    rows = sorted(snap.get("positions") or [],
                  key=lambda r: r.get("value_usd") or 0, reverse=True)
    lines = [f"<b>Portefeuille</b> · {fmt.usd(snap.get('total_value_usd'))}"]
    for r in rows[:20]:
        lines.append(
            f"{_esc(r.get('symbol'))} — {fmt.usd(r.get('value_usd'))} "
            f"({fmt.pct(r.get('weight_pct'), sign=False)}) · "
            f"{fmt.price(r.get('price'))} · 24 h {fmt.pct(r.get('change_24h'))}")
    live = snap.get("positions_priced_live")
    total = snap.get("positions_total")
    if live is not None and total is not None and live < total:
        lines.append(f"\n{fmt.integer(total - live)} position(s) valorisée(s) "
                     f"sur le dernier instantané connu, faute de prix live.")
    return "\n".join(lines)


def _cmd_sources() -> str:
    last = runlog.load_last()
    if not last:
        return "Aucun run enregistré."
    matrix = last.get("source_matrix") or []
    lines = [f"<b>Sources</b> · run {_esc(last.get('run_id'))}"]
    if not matrix:
        lines.append("Matrice de sources non enregistrée pour ce run.")
    for row in matrix:
        missed = row.get("missed")
        suffix = ""
        if isinstance(missed, int) and missed >= 1:
            suffix = f" · {fmt.integer(missed)} publication(s) manquée(s)"
        lines.append(f"• {_esc(row.get('source'))} — {_esc(row.get('status'))}"
                     f"{suffix}")
    degradations = last.get("degradations") or []
    if degradations:
        lines.append("\n<b>Dégradations</b>")
        lines.extend(f"• {_esc(d)}" for d in degradations)
    disabled = last.get("disabled_features") or []
    if disabled:
        lines.append("\nFonctions désactivées faute de paramètre : "
                     + ", ".join(_esc(d) for d in disabled))
    return "\n".join(lines)


def _cmd_resume() -> str:
    kind, snap = mem.load_latest_snapshot()
    if not snap:
        return "Aucun rapport envoyé pour l'instant."
    lines = [f"<b>{_esc(snap.get('title'))}</b> · {_esc(snap.get('date_label'))}"]
    if snap.get("banner"):
        lines.append(f"⚠ {_esc(snap['banner'])}")
    top = snap.get("top_action")
    if top:
        lines.append(f"\nGeste : {_esc(top['direction'])} {_esc(top['asset'])} "
                     f"— {_esc(top['notional'])}")
        lines.append(f"entrée {_esc(top['entry'])} · cible {_esc(top['target'])} "
                     f"· invalidation {_esc(top['stop'])}")
    elif snap.get("nothing_to_do"):
        lines.append(f"\n{_esc(snap['nothing_to_do'])}")
    for t in (snap.get("transitions") or [])[:6]:
        lines.append(f"• {_esc(t['asset'])} — {_esc(t['to'])}")
    for w in (snap.get("intraday") or [])[:6]:
        lines.append(f"• {_esc(w['asset'])} — {_esc(w['kind'])} franchie "
                     f"en séance")
    lines.append(f"\n(rapport « {_esc(kind)} »)")
    return "\n".join(lines)


def _cmd_memory() -> str:
    items = mem.load_bot_memory(limit=30)
    if not items:
        return "Mémoire vide. <code>/remember ta note</code> pour ajouter."
    lines = ["<b>Mémoire durable</b>"]
    for i, it in enumerate(items, 1):
        lines.append(f"{fmt.integer(i)}. [{_esc(it.get('kind'))}] "
                     f"{_esc(it.get('text'))}")
    lines.append("\n<code>/forget N</code> pour retirer une entrée.")
    return "\n".join(lines)


def _cmd_help() -> str:
    return (
        "<b>Commandes</b>\n"
        "/carnet — contrats en cours, cibles et invalidations\n"
        "/ptf — portefeuille valorisé au prix live\n"
        "/sources — état des sources du dernier run\n"
        "/resume — dernier rapport envoyé\n"
        "/memoire — mémoire durable · /remember · /forget N\n"
        "/dismiss ACTIF — annule un contrat (il sort du scoring)\n"
        "\nLe bot LIT le carnet. Seul le rapport du matin peut créer, réviser "
        "ou clôturer un contrat ; seul /dismiss fait exception, parce que "
        "c'est ta décision explicite.")


_READ_HANDLERS = {
    "/carnet": _cmd_carnet, "/recos": _cmd_carnet,
    "/ptf": _cmd_portfolio, "/portefeuille": _cmd_portfolio,
    "/positions": _cmd_portfolio,
    "/sources": _cmd_sources,
    "/resume": _cmd_resume,
    "/memoire": _cmd_memory,
    "/aide": _cmd_help, "/help": _cmd_help, "/start": _cmd_help,
}


def handle_read_command(text: str) -> Optional[str]:
    """Retourne la réponse d'une commande de lecture, ou ``None``."""
    cmd, _ = parse_command(text)
    handler = _READ_HANDLERS.get(cmd)
    if handler is None:
        return None
    try:
        return handler()
    except Exception as exc:                                    # noqa: BLE001
        logger.exception("Commande %s en échec : %s", cmd, exc)
        return f"Commande {_esc(cmd)} indisponible pour le moment."
