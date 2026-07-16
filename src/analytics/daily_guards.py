"""Gardes déterministes des rapports QUOTIDIENS (matin/soir) — v29.

L'audit des mails du 10/07 (v28) a montré que le prompt seul laisse encore
passer des contradictions internes ENTRE le narratif IA et les chiffres
déterministes affichés dans le même mail :
  * MA3 — adresses actives BTC : −9.2%/7j (tuile Python) vs −11.8% (bilan IA) ;
  * MA4 — « dollar fort (DXY 100.799) » (macro) ET « dollar faible
    (DXY 100.799) » (thèse BTC) dans le même mail ;
  * MA10 — « CPI 4.3% » cité comme un fait alors que le CPI n'était pas
    publié (et incohérent avec le 10Y réel affiché) ;
  * MA11 — « Polymarket donne 84% de chances de toucher 65 000 $ » : aucun
    marché Polymarket fourni ne porte ce chiffre (84.5% = proba Fed recyclée) ;
  * WA4 — bilan Fed « +2.0% » (matin) vs « +1.4% » (hebdo) le même jour ;
  * MA8 — stats chartistes honnêtes mais conclusion IA en spin haussier
    (« −2.3%, positif dans 28% des cas, confirmant que nous achetons avant le
    retournement ») ;
  * MA2 — l'EN BREF annonçait « Renforcement … (BTC, ETH, TAO, LINK) » alors
    que les plans disaient « renfort non suggéré (plafond) » pour BTC/ETH/TAO ;
  * MA12 — le narratif de rotation commentait des secteurs absents des tuiles ;
  * EA1 — news spéculative (« pourrait être interdit ce soir ») taguée
    « actionnable ».

Comme ``weekly_guards`` : chaque garde est pure, best-effort (payload partiel
→ inchangé), et renvoie la liste des corrections opérées pour le log.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

_NUM = r"[+\-−]?\d{1,3}(?:[.,]\d+)?"

# Champs de MÉTADONNÉES à ne jamais réécrire lors d'un walk global (libellés
# d'interface, identifiants, dates) — les gardes ne visent que la prose.
_SKIP_KEYS = {"id", "date", "time_casablanca", "next_report_at", "asset",
              "symbol", "source", "url", "cid", "label"}


def _to_float(token: Any) -> Optional[float]:
    try:
        return float(str(token).replace("−", "-").replace(",", "."))
    except (ValueError, AttributeError, TypeError):
        return None


def _fmt_pct_fr(v: float, nd: int = 1) -> str:
    """−9,2% / +3,1% — virgule décimale, moins typographique."""
    s = f"{abs(round(v, nd)):.{nd}f}"
    if "." in s:  # nd=0 : jamais de rstrip sur un entier (« 100 » → « 1 »)
        s = s.rstrip("0").rstrip(".")
    return ("+" if v >= 0 else "−") + s.replace(".", ",") + "%"


def walk_strings(node: Any, fn: Callable[[str], str]) -> Any:
    """Applique ``fn`` à toutes les chaînes d'une structure imbriquée.

    Les clés de métadonnées (:data:`_SKIP_KEYS`) sont préservées telles
    quelles. Les éléments de liste devenus vides après correction (phrase
    entière retirée) sont supprimés de la liste.
    """
    if isinstance(node, str):
        return fn(node)
    if isinstance(node, list):
        out = []
        for x in node:
            new = walk_strings(x, fn)
            # Seule une puce VIDÉE PAR LA GARDE est retirée — un élément déjà
            # vide/blanc à l'entrée est préservé tel quel (pas notre décision).
            if (isinstance(x, str) and isinstance(new, str)
                    and new != x and not new.strip()):
                continue
            out.append(new)
        return out
    if isinstance(node, dict):
        return {k: (v if k in _SKIP_KEYS else walk_strings(v, fn))
                for k, v in node.items()}
    return node


# ── MA3 — adresses actives : le narratif suit les tuiles Python ──────────

def fix_active_addresses(
    node: Any, real_pcts: dict[str, float], asset_hint: Optional[str] = None,
) -> tuple[Any, list[str]]:
    """Verrouille les « adresses actives … −X% » sur les valeurs des tuiles.

    Deux formes couvertes : « adresses actives … (BTC −11.8%, ETH −4.4%) »
    (actif nommé après le contexte) et « adresses actives −4.4% sur 7j » sans
    actif nommé (l'actif vient alors de ``asset_hint`` — champ d'une thèse).
    Tolérance 0,15 pt (arrondis d'affichage).
    """
    fixes: list[str] = []
    if not real_pcts:
        return node, fixes
    ctx = re.compile(r"(?i)adresses\s+actives")

    def _fn(text: str) -> str:
        if not ctx.search(text):
            return text
        new = text
        for asset, real in real_pcts.items():
            if not isinstance(real, (int, float)):
                continue
            pat = re.compile(
                rf"((?i:adresses\s+actives)[^.!\n]{{0,120}}?\b{asset}\b"
                rf"[^0-9%+\-−]{{0,10}})({_NUM})(\s?%)")

            def _sub(m: re.Match) -> str:
                cited = _to_float(m.group(2))
                if cited is None or abs(cited - real) <= 0.15:
                    return m.group(0)
                fixes.append(
                    f"adresses actives {asset} {m.group(2)}% → {_fmt_pct_fr(real)}")
                return f"{m.group(1)}{_fmt_pct_fr(real).rstrip('%')}{m.group(3)}"

            new = pat.sub(_sub, new)
        # Forme sans actif nommé — l'actif vient du contexte (thèse).
        if asset_hint and asset_hint in real_pcts:
            real = real_pcts[asset_hint]
            pat2 = re.compile(
                rf"((?i:adresses\s+actives)[^0-9%\n]{{0,25}}?)({_NUM})(\s?%)")

            def _sub2(m: re.Match) -> str:
                cited = _to_float(m.group(2))
                if (cited is None or not isinstance(real, (int, float))
                        or abs(cited - real) <= 0.15):
                    return m.group(0)
                fixes.append(
                    f"adresses actives ({asset_hint}) {m.group(2)}% → "
                    f"{_fmt_pct_fr(real)}")
                return f"{m.group(1)}{_fmt_pct_fr(real).rstrip('%')}{m.group(3)}"

            new = pat2.sub(_sub2, new)
        return new

    return walk_strings(node, _fn), fixes


# ── MA4 — un seul qualificatif dollar par mail ────────────────────────────

def dxy_qualifier(delta_points: Any) -> Optional[str]:
    """Qualificatif canonique du dollar depuis le delta DXY du jour (points).

    v30 (#73) — seuil ±0.15 pt (≈0,15%) : le 15/07, un recul RÉEL de −0,28 pt
    (tuile soir ▼) était qualifié « stable » par l'ancien seuil ±0.3 — la
    garde imposait alors un faux « dollar stable » contre le mouvement réel.
    """
    d = _to_float(delta_points)
    if d is None:
        return None
    if d > 0.15:
        return "en hausse"
    if d < -0.15:
        return "en baisse"
    return "stable"


def fix_dxy_wording(node: Any, qualifier: Optional[str]) -> tuple[Any, list[str]]:
    """Remplace « dollar fort / dollar faible / DXY fort|faible » par le
    qualificatif canonique quand ils le contredisent.

    Le 10/07, la MÊME valeur (DXY 100.80, ▬ stable) était qualifiée de
    « fort » dans la macro et de « faible » dans la thèse BTC.
    """
    fixes: list[str] = []
    if qualifier not in ("stable", "en hausse", "en baisse"):
        return node, fixes
    # v30 (#73) — motif élargi : le 15/07, la prose disait « dollar affaibli »
    # et « DXY ferme » selon les sections — seuls fort/faible étaient couverts.
    ok_words = {"stable": ("stable",),
                "en hausse": ("fort", "en hausse", "ferme", "raffermi"),
                "en baisse": ("faible", "en baisse", "affaibli", "en repli")}[qualifier]
    pat = re.compile(r"(?i)\b(dollar|DXY)\s+(fort|faible|ferme|affaibli|raffermi)\b")

    def _fn(text: str) -> str:
        def _sub(m: re.Match) -> str:
            if m.group(2).lower() in ok_words:
                return m.group(0)
            fixes.append(f"« {m.group(0)} » → « {m.group(1)} {qualifier} »")
            return f"{m.group(1)} {qualifier}"
        return pat.sub(_sub, text)

    return walk_strings(node, _fn), fixes


# ── MA10 — CPI : jamais un chiffre non sourcé ─────────────────────────────

def fix_cpi_claims(node: Any, cpi_yoy: Any) -> tuple[Any, list[str]]:
    """Réaligne « CPI X% » sur le YoY FRED réel, ou retire le chiffre.

    ``cpi_yoy`` : dernier CPI YoY publié (FRED, ``display_value``) ou None.
    Avec une valeur → le chiffre divergent est réécrit ; sans valeur → le
    chiffre est RETIRÉ (on garde « CPI » sans nombre : pas de fait inventé).
    """
    fixes: list[str] = []
    real = _to_float(cpi_yoy)
    pat = re.compile(
        rf"((?i:CPI|inflation\s+US(?:\s*\(CPI\))?)\s*(?:à|de|:)?\s+)({_NUM})(\s?%)")

    def _fn(text: str) -> str:
        def _sub(m: re.Match) -> str:
            cited = _to_float(m.group(2))
            if cited is None:
                return m.group(0)
            if real is not None:
                if abs(cited - real) <= 0.2:
                    return m.group(0)
                fixes.append(f"CPI {m.group(2)}% → {_fmt_pct_fr(real)} (FRED)")
                return f"{m.group(1)}{_fmt_pct_fr(real).lstrip('+')}"
            fixes.append(f"CPI {m.group(2)}% retiré (valeur non sourcée)")
            return m.group(1).rstrip(" à de:")
        return pat.sub(_sub, text)

    return walk_strings(node, _fn), fixes


# ── MA11 — Polymarket : seuls les marchés fournis sont citables ──────────

_STRIKE_RE = re.compile(
    r"(\d{1,3}(?:[\s .,]\d{3})+|\d{4,7})\s*(?:\$|USD)|\$\s*(\d[\d\s .,]*)")


def _norm_strikes(text: str) -> set[str]:
    """Niveaux de prix (« 65 000 $ », « $67,500 ») → digits normalisés."""
    out: set[str] = set()
    for m in _STRIKE_RE.finditer(text or ""):
        raw = m.group(1) or m.group(2) or ""
        digits = re.sub(r"[^\d]", "", raw)
        if len(digits) >= 4:  # un strike crypto plausible, pas « 84 »
            out.add(digits)
    return out


def sanitize_polymarket_claims(
    node: Any, known_pcts: list[float],
    known_strikes: Optional[set[str]] = None, tolerance: float = 1.0,
) -> tuple[Any, list[str]]:
    """Retire la PHRASE attribuant à Polymarket un chiffre absent des données.

    Le 10/07, la thèse BTC citait « Polymarket donne 84% de chances de toucher
    65 000 $ » — la proba Fed (84.5%) recyclée sur un marché INEXISTANT (le
    seul marché BTC fourni : 67 500 $ à 50%). Deux contrôles cumulés :
      1. tout « X% » de la phrase doit être à ≤ ``tolerance`` d'une proba
         réellement fournie (fed_bars + marchés extra, et leurs compléments) ;
      2. tout NIVEAU DE PRIX cité (« 65 000 $ ») doit figurer dans la question
         d'un marché fourni — c'est ce contrôle qui attrape le recyclage d'une
         proba plausible sur un strike inventé.
    On ne réécrit pas (pas de sémantique inventée) : la phrase fautive saute,
    le reste du paragraphe est conservé.
    """
    fixes: list[str] = []
    known = [k for k in (_to_float(x) for x in known_pcts) if k is not None]
    if not known:
        return node, fixes
    strikes = known_strikes if known_strikes is not None else set()
    poly = re.compile(r"(?i)polymarket")
    pct = re.compile(rf"({_NUM})\s?%")

    def _sentence_ok(sent: str) -> bool:
        if not poly.search(sent):
            return True
        for m in pct.finditer(sent):
            v = _to_float(m.group(1))
            if v is None:
                continue
            if not any(abs(v - k) <= tolerance for k in known):
                return False
        # Un strike cité doit exister dans un marché fourni (contrôle 2).
        cited_strikes = _norm_strikes(sent)
        if cited_strikes and not (cited_strikes <= strikes):
            return False
        return True

    def _fn(text: str) -> str:
        if not poly.search(text):
            return text
        sentences = re.split(r"(?<=[.!?])\s+", text)
        kept = [s for s in sentences if _sentence_ok(s)]
        if len(kept) == len(sentences):
            return text
        dropped = [s for s in sentences if s not in kept]
        for d in dropped:
            fixes.append(f"phrase Polymarket non sourcée retirée : « {d[:60]}… »")
        return " ".join(kept).strip()

    return walk_strings(node, _fn), fixes


# ── WA4 — bilan Fed : un seul chiffre cross-mail ──────────────────────────

def fix_fed_balance_claims(node: Any, real_pct: Any) -> tuple[Any, list[str]]:
    """Verrouille « bilan (de la) Fed … ±X% » sur le change_pct FRED canonique.

    Le 10/07 : « bilan Fed en hausse (+2.0%) » le matin, « +1,4% (QE) » le
    soir même dans l'hebdo — même donnée source (WALCL), deux chiffres.
    """
    fixes: list[str] = []
    real = _to_float(real_pct)
    if real is None:
        return node, fixes
    pat = re.compile(
        rf"((?i:bilan\s+(?:de\s+la\s+)?Fed)[^.%\n]{{0,60}}?)({_NUM})(\s?%)")

    def _fn(text: str) -> str:
        def _sub(m: re.Match) -> str:
            cited = _to_float(m.group(2))
            if cited is None or abs(cited - real) <= 0.15:
                return m.group(0)
            fixes.append(f"bilan Fed {m.group(2)}% → {_fmt_pct_fr(real)}")
            return f"{m.group(1)}{_fmt_pct_fr(real).rstrip('%')}{m.group(3)}"
        return pat.sub(_sub, text)

    return walk_strings(node, _fn), fixes


# ── MA8 — stats chartistes : la conclusion suit les chiffres ─────────────

_SPIN_CLAUSE = re.compile(
    r",\s*(?:ce qui\s+)?(?:confirmant|confirme|validant|valide|soutenant|"
    r"renforçant|démontrant|prouvant|reflétant|illustrant|témoignant)[^.]*\.")

_HONEST_VERDICT = (
    ". Lecture honnête : configuration historiquement défavorable à 7 j — "
    "le signal est un point d'entrée de long terme, pas un timing court terme.")


def fix_historical_spin(node: Any) -> tuple[Any, list[str]]:
    """Remplace la conclusion en « spin » quand les stats citées sont défavorables.

    Auto-portant : les seuils sont parsés depuis le texte lui-même
    (« rendement moyen à 7 jours est de −2.3%, positif dans 28% des cas,
    confirmant que nous achetons dans la douleur avant le retournement »).
    Rendement moyen < 0 OU win rate < 45% ⇒ la subordonnée affirmative est
    remplacée par un verdict déterministe. Stats favorables → texte intact.
    """
    fixes: list[str] = []
    # Le gap tolère « à 7 jours » (contient un chiffre) mais pas un « % » ni un
    # point (pas de traversée de phrase) — lazy : premier nombre suivi de %.
    avg_re = re.compile(
        rf"(?i)rendement\s+moyen[^%.]{{0,60}}?({_NUM})\s?%")
    win_re = re.compile(r"(?i)positif\s+dans\s+(\d{1,3})\s?%")

    def _fn(text: str) -> str:
        m_avg, m_win = avg_re.search(text), win_re.search(text)
        if not (m_avg and m_win):
            return text
        avg, win = _to_float(m_avg.group(1)), _to_float(m_win.group(1))
        if avg is None or win is None or (avg >= 0 and win >= 45):
            return text
        if not _SPIN_CLAUSE.search(text):
            return text
        fixes.append(
            f"conclusion chartiste en spin remplacée (moy {m_avg.group(1)}%, "
            f"positif {m_win.group(1)}%)")
        return _SPIN_CLAUSE.sub(_HONEST_VERDICT, text, count=1)

    return walk_strings(node, _fn), fixes


# ── MA2 — l'EN BREF ne contredit pas les plans par-actif ──────────────────

def fix_reinforce_claims(
    bullets: Any, reinforce_assets: set[str], capped_assets: set[str],
) -> tuple[Any, list[str]]:
    """Une puce EN BREF annonçant le renforcement d'actifs AU PLAFOND est
    remplacée par le constat honnête (recos fermes réelles + plafonds).

    Le 10/07 : « Renforcement tactique et de conviction sur le cœur (BTC,
    ETH, TAO, LINK) » alors que BTC/ETH/TAO étaient MAINTENIR (plafond) et
    que le geste du jour était RENFORCER RENDER.
    """
    fixes: list[str] = []
    if not isinstance(bullets, list) or not capped_assets:
        return bullets, fixes
    universe = {a.upper() for a in (reinforce_assets | capped_assets)}
    renf = re.compile(r"(?i)renforc")
    token = re.compile(r"\b[A-Z0-9]{2,10}\b")

    out: list[Any] = []
    for b in bullets:
        text = b if isinstance(b, str) else (b.get("text") if isinstance(b, dict) else None)
        if not isinstance(text, str) or not renf.search(text):
            out.append(b)
            continue
        cited = {t for t in token.findall(text) if t in universe}
        offenders = cited & {a.upper() for a in capped_assets}
        if not offenders:
            out.append(b)
            continue
        realf = sorted(a.upper() for a in reinforce_assets)
        if realf:
            new_text = (
                f"Recos fermes du jour : {', '.join(realf)} — "
                f"{', '.join(sorted(offenders))} au plafond de concentration "
                "(maintenir, pas de renfort).")
        else:
            new_text = (
                f"Aucun renfort aujourd'hui : {', '.join(sorted(offenders))} "
                "au plafond de concentration — maintenir les positions.")
        fixes.append(
            f"puce EN BREF « renforcement » contredisant les plans réécrite "
            f"({', '.join(sorted(offenders))} au plafond)")
        out.append({**b, "text": new_text} if isinstance(b, dict) else new_text)
    return out, fixes


# ── MA12 — le narratif de rotation ne cite que les tuiles affichées ──────

_SECTOR_ALIASES: dict[str, tuple[str, ...]] = {
    "AI": ("AI", "IA", "intelligence artificielle"),
    "L1": ("L1", "layer 1", "layer-1"),
    "L2": ("L2", "layer 2", "layer-2"),
    "DeFi": ("DeFi",),
    "IoT/Data": ("IoT/Data", "IoT", "Data"),
    "Oracle/Infra": ("Oracle/Infra", "oracle", "infra"),
    "Memes": ("memes", "memecoins"),
    "Gaming": ("gaming", "jeux"),
    "Privacy": ("privacy", "confidentialité"),
    "Interop": ("interop", "interopérabilité"),
}


def _sector_regex(name: str) -> re.Pattern:
    aliases = _SECTOR_ALIASES.get(name, (name,))
    alts = "|".join(re.escape(a) for a in aliases)
    return re.compile(rf"(?i)(?<![A-Za-z0-9])(?:{alts})(?![A-Za-z0-9])")


def filter_rotation_note(
    note: Any, displayed: list[str], all_sectors: list[str],
) -> tuple[Any, list[str]]:
    """Retire les phrases du narratif de rotation citant un secteur non affiché.

    Le 10/07, les tuiles montraient L2/IoT/Oracle et le texte analysait DeFi
    et IA — le lecteur ne pouvait pas relier l'analyse aux chiffres visibles.
    Une phrase qui cite AUSSI un secteur affiché est conservée (comparatif).
    Fail-safe : si tout saute, le texte original est conservé tel quel.
    """
    fixes: list[str] = []
    if not isinstance(note, str) or not note.strip() or not displayed:
        return note, fixes
    disp_base = {re.sub(r"\s*\(.*\)$", "", d).strip() for d in displayed}
    hidden = [s for s in all_sectors
              if s not in disp_base
              and not str(s).lower().startswith(("autre", "other", "divers"))]
    if not hidden:
        return note, fixes
    disp_res = [_sector_regex(s) for s in disp_base
                if not str(s).lower().startswith(("autre", "other", "divers"))]
    hid_res = [(_sector_regex(s), s) for s in hidden]

    sentences = re.split(r"(?<=[.!?])\s+", note)
    kept: list[str] = []
    for s in sentences:
        hits_hidden = [name for rex, name in hid_res if rex.search(s)]
        hits_disp = any(rex.search(s) for rex in disp_res)
        if hits_hidden and not hits_disp:
            fixes.append(
                f"phrase rotation sur secteur non affiché retirée "
                f"({', '.join(hits_hidden)})")
            continue
        kept.append(s)
    if not kept:
        return note, []  # fail-safe : mieux vaut l'original qu'un vide
    return " ".join(kept).strip(), fixes


# ── EA1 — « actionnable » réservé aux news réellement actionnables ───────

_SPECULATIVE = re.compile(
    r"(?i)\b(pourrait|pourraient|devrait|devraient|envisage(?:rait)?|serait|"
    r"seraient|rumeur|selon\s+(?:des|les)\s+(?:sources|analystes)|éventuel(?:le)?|"
    r"potentiellement|deviendra-t-il|reste à confirmer)\b")


_OVERSOLD_WORDS = re.compile(r"(?i)\bsurvendu(?:e|es|s)?\b|\bsurvente\b")


def fix_oversold_claims(
    node: Any, rsi: Any, asset: str = "?"
) -> tuple[Any, list[str]]:
    """v30 (#78) — « survendu » interdit quand le RSI réel dit le contraire.

    Le 15/07 : « configuration survendue » sur BTC (RSI 54) et ETH (RSI 61).
    RSI ≥ 40 → le vocabulaire de survente devient « en consolidation ».
    """
    fixes: list[str] = []
    r = _to_float(rsi)
    if r is None or r < 40:
        return node, fixes

    def _repl(m: re.Match) -> str:
        # nom (« survente ») → nom (« consolidation ») ; adjectif
        # (« survendu(e) ») → locution (« en consolidation »).
        return ("consolidation" if m.group(0).lower().startswith("survente")
                else "en consolidation")

    def _fn(text: str) -> str:
        if not _OVERSOLD_WORDS.search(text):
            return text
        fixes.append(f"{asset} : « survendu » → « en consolidation » (RSI {r:.0f})")
        return _OVERSOLD_WORDS.sub(_repl, text)

    return walk_strings(node, _fn), fixes


def downgrade_speculative_actionable(
    items: Any, held_assets: Any = None
) -> list[str]:
    """Dégrade le tag « actionnable » d'une news au conditionnel/spéculative.

    Le 10/07 : « Le dollar numérique … POURRAIT être interdit ce soir »
    taguée (actionnable). Une hypothèse n'est pas un geste : le tag devient
    « à suivre ». Mutation in-place, best-effort.
    """
    fixes: list[str] = []
    if not isinstance(items, list):
        return fixes
    for n in items:
        if not isinstance(n, dict):
            continue
        if str(n.get("status") or "").lower() != "actionnable":
            continue
        blob = f"{n.get('title') or ''} {n.get('impact') or ''}"
        if _SPECULATIVE.search(blob):
            n["status"] = "à suivre"
            fixes.append(
                f"news « {str(n.get('title'))[:45]}… » : actionnable → à suivre "
                "(formulation spéculative)")
            continue
        # v30 (#34) — « actionnable » exige un LIEN avec une position détenue
        # (le 14/07, une news Airbnb sans rapport avec le PTF était taguée
        # actionnable). Sans actif détenu cité : « à suivre ».
        if held_assets:
            _held_hit = any(
                re.search(rf"\b{re.escape(str(a))}\b", blob, re.IGNORECASE)
                for a in held_assets if a)
            if not _held_hit:
                n["status"] = "à suivre"
                fixes.append(
                    f"news « {str(n.get('title'))[:45]}… » : actionnable → "
                    "à suivre (aucune position du PTF concernée)")
    return fixes


# ── v30 (#19/#21/#65) — le soir ne contredit plus une thèse LT active ─────

_REDUCE_VERBS = re.compile(
    r"(?i)\b(all[ée]ger|vendre|sortir|liquider|prendre\s+(?:les\s+)?profits?|"
    r"prise\s+de\s+profits?|réduire|couper)\b")
_INCREASE_VERBS = re.compile(
    r"(?i)\b(renforcer|acheter|accumuler|moyenner|recharger)\b")
_DEFINITIVE = re.compile(r"(?i)(100\s?%|définitiv|totalité|toute\s+la\s+position)")


def _action_text(a: Any) -> str:
    if isinstance(a, dict):
        return " ".join(str(a.get(k) or "") for k in ("action", "rationale", "horizon"))
    return str(a or "")


def reconcile_evening_actions(
    actions: Any, active_recos: Any
) -> tuple[Optional[list[Any]], list[str]]:
    """v30 — RÉCONCILIATION LT/CT : filtre les « actions à poser ce soir ».

    Les 13-14/07, le soir ordonnait « Alléger 100% INJ · sortie définitive »
    et « Alléger 25% TAO » pendant que le matin (moteur LT) disait RENFORCER
    les mêmes actifs — INJ a rebondi de +7,1% le lendemain de sa « sortie
    définitive ». Deux moteurs opposés sur le même actif en < 12 h = whipsaw.

    Règles (déterministes, best-effort) :
      * action de RÉDUCTION sur un actif porteur d'une reco OUVERTE
        RENFORCER : si elle est « définitive »/100% → SUPPRIMÉE (le soir n'a
        pas mandat pour clôturer une conviction LT) ; sinon → conservée mais
        requalifiée « couverture tactique CT » avec mention explicite que la
        thèse LT reste active ;
      * action d'ACHAT sur un actif porteur d'une reco OUVERTE ALLÉGER →
        même traitement symétrique.

    Returns:
        (liste filtrée ou None si vide, corrections pour le log).
    """
    fixes: list[str] = []
    if not isinstance(actions, list) or not actions:
        return (actions if isinstance(actions, list) else None), fixes
    stance_by_asset: dict[str, str] = {}
    for r in (active_recos or []):
        if not isinstance(r, dict):
            continue
        if (r.get("status") or "in_progress") != "in_progress":
            continue
        a = str(r.get("asset") or "").upper()
        act = (r.get("action") or "").upper()
        if a and ("RENFORC" in act or "ALLÉG" in act or "ALLEG" in act):
            stance_by_asset[a] = "RENFORCER" if "RENFORC" in act else "ALLEGER"
    if not stance_by_asset:
        return actions, fixes

    kept: list[Any] = []
    for a in actions:
        text = _action_text(a)
        # Le verbe décisif est celui de la ligne d'ACTION (pas la rationale,
        # qui peut légitimement mentionner l'autre direction en contexte).
        primary = str(a.get("action") or "") if isinstance(a, dict) else text
        asset = next((sym for sym in stance_by_asset
                      if re.search(rf"\b{re.escape(sym)}\b", primary)), None)
        if asset is None:  # repli : texte complet (action sans ticker explicite)
            asset = next((sym for sym in stance_by_asset
                          if re.search(rf"\b{re.escape(sym)}\b", text)), None)
        if not asset:
            kept.append(a)
            continue
        stance = stance_by_asset[asset]
        _red = _REDUCE_VERBS.search(primary)
        _inc = _INCREASE_VERBS.search(primary)
        conflict = ((stance == "RENFORCER" and _red and not _inc)
                    or (stance == "ALLEGER" and _inc and not _red))
        if not conflict:
            kept.append(a)
            continue
        if _DEFINITIVE.search(text):
            fixes.append(
                f"action soir « {text[:50].strip()}… » SUPPRIMÉE : sortie "
                f"définitive contraire à la thèse LT {stance} active ({asset})")
            continue
        note = (f"Couverture tactique CT — la thèse LT ({stance}) sur "
                f"{asset} reste active : geste borné, pas une sortie.")
        if isinstance(a, dict):
            a["horizon"] = (f"{a.get('horizon')} · {note}"
                            if a.get("horizon") else note)
        else:
            a = f"{a} — {note}"
        kept.append(a)
        fixes.append(f"action soir sur {asset} requalifiée « couverture "
                     f"tactique CT » (thèse LT {stance} active)")
    return (kept or None), fixes


# ── v30 (#11/#29) — SEUIL DXY UNIQUE par rapport ──────────────────────────

_DXY_LEVEL = re.compile(r"(?i)\bDXY\b[^.\d%]{0,40}?(\d{2,3}(?:[.,]\d{1,2})?)")


def unify_dxy_thresholds(
    node: Any, state: Optional[dict[str, Any]] = None
) -> tuple[Any, list[str]]:
    """Aligne tous les seuils DXY d'un même rapport sur le PREMIER cité.

    Le 15/07 : « DXY < 101.0 » (liens chiffrés) vs « DXY > 101.2 »
    (à surveiller) vs « 101.20 » (niveaux) dans le même mail. Un seul pivot
    par run : la première valeur rencontrée devient canonique, toute valeur
    ultérieure qui s'en écarte de < 1% est réécrite dessus (au-delà de 1%,
    on considère qu'il s'agit d'un AUTRE niveau légitime, ex. 100.5 support
    vs 101.2 résistance — non touché).

    ``state`` : dict partagé entre plusieurs appels (sections différentes du
    même mail) pour que l'ancre canonique soit VRAIMENT unique au rapport —
    sans lui, chaque section repartirait avec sa propre ancre.
    """
    fixes: list[str] = []
    if state is None:
        state = {"canon": None, "canon_txt": None}

    def _fix(text: str) -> str:
        def _sub(m: re.Match) -> str:
            raw = m.group(1)
            val = _to_float(raw)
            if val is None or not (80 <= val <= 130):
                return m.group(0)
            if state["canon"] is None:
                state["canon"] = val
                state["canon_txt"] = raw
                return m.group(0)
            if val != state["canon"] and abs(val - state["canon"]) / state["canon"] < 0.01:
                fixes.append(f"seuil DXY {raw} → {state['canon_txt']} (pivot unique)")
                return m.group(0).replace(raw, str(state["canon_txt"]))
            return m.group(0)
        return _DXY_LEVEL.sub(_sub, text)

    return walk_strings(node, _fix), fixes


# ── v30 (#72) — chiffre ETF sans source structurée : provenance annoncée ──

_ETF_FIGURE = re.compile(
    r"(?i)\bETF\b[^.\n]{0,80}?\d(?:[.,]\d+)?\s?(?:millions?|M\$|Mds?\$)")


def flag_etf_news_provenance(items: Any, etf_available: bool) -> list[str]:
    """Quand les flux ETF structurés (Farside/CoinGlass) sont KO, tout chiffre
    ETF venu d'ailleurs (Telegram) est étiqueté comme NON RECOUPÉ.

    Le 15/07 : un catalyseur titrait « ETF Bitcoin +173,7 M$ » pendant que le
    pied du même mail listait « ⚠ Indisponibles · ETF flows » — illisible
    sans étiquette de provenance.
    """
    fixes: list[str] = []
    if etf_available or not isinstance(items, list):
        return fixes
    for n in items:
        if not isinstance(n, dict):
            continue
        blob = f"{n.get('title') or ''} {n.get('impact') or ''}"
        if _ETF_FIGURE.search(blob) and "non recoupé" not in blob:
            note = (" (chiffre de canal Telegram — non recoupé : source "
                    "structurée Farside/CoinGlass indisponible ce matin)")
            if isinstance(n.get("impact"), str) and n["impact"].strip():
                n["impact"] = n["impact"].rstrip(".") + "." + note
            else:
                n["impact"] = note.strip()
            fixes.append("news ETF : provenance Telegram non recoupée étiquetée")
    return fixes


# ── v30 (#9) — chiffre macro cité LE JOUR de sa re-publication ────────────

def mark_prepub_claims(
    node: Any, metric_pattern: str, metric_label: str
) -> tuple[Any, list[str]]:
    """Marque « avant publication du jour » un chiffre macro re-publié à J0.

    Le 14/07, le mail s'appuyait sur « CPI 4,3% » à 08h35 — le CPI du jour
    sortait à 13h30 (et tombera à 3,5%). Le chiffre reste (c'est le dernier
    connu) mais porte la mention explicite.
    """
    fixes: list[str] = []
    pat = re.compile(metric_pattern)
    done = {"n": 0}

    def _fn(text: str) -> str:
        if done["n"] or not pat.search(text):
            return text

        def _sub(m: re.Match) -> str:
            if done["n"]:
                return m.group(0)
            done["n"] += 1
            fixes.append(f"{metric_label} marqué « avant publication du jour »")
            return m.group(0) + " (dernier connu — nouvelle publication aujourd'hui)"
        return pat.sub(_sub, text, count=1)

    return walk_strings(node, _fn), fixes


# ── v30 (#2/#3) — ton du narratif plafonné quand l'action est gatée ───────

_HYPERBOLE = [
    (re.compile(r"(?i)\bexceptionnel(?:le)?s?\b"), "notable"),
    (re.compile(r"(?i)\bopportunité\s+majeure\b"), "configuration à suivre"),
    (re.compile(r"(?i)\bopportunité\s+historique\b"), "configuration rare"),
    (re.compile(r"(?i)\bunique\b"), "rare"),
    (re.compile(r"(?i)\bimmanquable\b"), "surveillée"),
]


def tone_down_gated_theses(theses: Any) -> list[str]:
    """Plafonne le ton des fiches dont l'action a été REQUALIFIÉE par un gate.

    Le 15/07, la fiche ETH (MAINTENIR — plafond de concentration) ouvrait sur
    « profil d'accumulation exceptionnel » : le narratif vendait un achat que
    l'action ne permettait pas. Sur une thèse gatée, les hyperboles de la
    prose sont remplacées par des termes mesurés. Mutation in-place.
    """
    fixes: list[str] = []
    if not isinstance(theses, list):
        return fixes
    for t in theses:
        if not isinstance(t, dict) or not t.get("_gated"):
            continue
        asset = str(t.get("asset") or "?").upper()
        for key in ("observation", "thesis", "summary"):
            v = t.get(key)
            if not isinstance(v, str):
                continue
            new = v
            for pat, repl in _HYPERBOLE:
                new = pat.sub(repl, new)
            if new != v:
                t[key] = new
                fixes.append(f"{asset} : ton du narratif plafonné (action gatée)")
    return fixes


# ── v30 (#24) — les indices boursiers ne sont pas des montants en $ ───────

_INDEX_DOLLAR = re.compile(
    r"(?i)\b(S&P\s?500|Nasdaq(?:\s?100)?|Dow(?:\s?Jones)?|Euro\s?Stoxx\s?50|"
    r"DAX|CAC\s?40|Nikkei\s?225|Russell\s?2000|VIX)"
    r"([^.!?\n]{0,60}?\d(?:[   .,]?\d)*(?:[.,]\d+)?)\s?\$")


def strip_index_dollar(node: Any) -> tuple[Any, list[str]]:
    """Retire le « $ » collé aux NIVEAUX d'indices (points, pas des dollars).

    Le 14/07 : « Le S&P 500 a gagné +32,28 points à 7 547,62 $ » — un indice
    ne se libelle pas en dollars. Le montant est conservé, le symbole saute.
    """
    fixes: list[str] = []

    def _fix(text: str) -> str:
        def _sub(m: re.Match) -> str:
            fixes.append(f"{m.group(1)} : « $ » retiré (niveau en points)")
            return f"{m.group(1)}{m.group(2)} points"
        return _INDEX_DOLLAR.sub(_sub, text)

    return walk_strings(node, _fix), fixes


# ── v30 (#22) — micro-prix en prose : précision unifiée (4 chiffres sig.) ──

_MICRO_PRICE = re.compile(r"\b0[.,]0(\d{5,})(?=\s?\$)")


def round_micro_prices(node: Any) -> tuple[Any, list[str]]:
    """Arrondit les micro-prix de la PROSE à 4 chiffres significatifs.

    Le 14/07, RSR apparaissait sous 3 précisions différentes dans les mails
    (« 0.00123749 $ », « 0.001280 $ », « 0.001224 $ ») — même règle que les
    tuiles (_fmt_price) : 4 chiffres significatifs, virgule FR.
    """
    fixes: list[str] = []

    def _fix(text: str) -> str:
        def _sub(m: re.Match) -> str:
            raw = "0." + "0" + m.group(1)
            v = _to_float(raw)
            if v is None or v <= 0:
                return m.group(0)
            import math as _m
            exp = _m.floor(_m.log10(v))
            decimals = min(-exp + 3, 18)
            rounded = f"{v:.{decimals}f}".rstrip("0").rstrip(".")
            if rounded == raw:
                return m.group(0)
            fixes.append(f"micro-prix {m.group(0)} → {rounded.replace('.', ',')}")
            return rounded.replace(".", ",")
        return _MICRO_PRICE.sub(_sub, text)

    return walk_strings(node, _fix), fixes


# ── v30 (#28) — niveaux checklist alignés sur les niveaux CALCULÉS ────────

_USD_LEVEL = re.compile(r"(\d{1,3}(?:[  ]\d{3})+|\d{4,6})(?:[.,]\d+)?\s?\$")


def align_checklist_levels(
    node: Any, canonical_levels: list[float]
) -> tuple[Any, list[str]]:
    """Réaligne un niveau « rond » LLM sur le niveau calculé le plus proche.

    Le 13/07 : « Invalidation : BTC clôture sous 60 800 $ » (checklist) vs
    « support 60 862 $ » (niveaux calculés) dans le même mail. Tout montant
    en $ à < 0,6% d'un niveau canonique est réécrit dessus — un seul niveau
    par zone de prix.
    """
    fixes: list[str] = []
    levels = [float(v) for v in (canonical_levels or [])
              if isinstance(v, (int, float)) and v > 0]
    if not levels:
        return node, fixes

    def _fmt_lvl(v: float) -> str:
        return (f"{v:,.0f}".replace(",", "\u202f") if v >= 1000
                else f"{v:.2f}".rstrip("0").rstrip("."))

    def _fix(text: str) -> str:
        def _sub(m: re.Match) -> str:
            val = _to_float(m.group(1).replace(" ", "").replace(" ", ""))
            if val is None or val <= 0:
                return m.group(0)
            near = min(levels, key=lambda lv: abs(lv - val))
            if val != near and abs(val - near) / near < 0.006:
                fixes.append(f"niveau {m.group(1)} $ → {_fmt_lvl(near)} $ "
                             "(aligné sur le niveau calculé)")
                return f"{_fmt_lvl(near)} $"
            return m.group(0)
        return _USD_LEVEL.sub(_sub, text)

    return walk_strings(node, _fix), fixes


# ── v30 (#40) — décimales cassées « -48, 7% » réparées ────────────────────

# Une virgule décimale suivie d'un espace parasite : « -48, 7% », « 4, 86 $ »,
# « 1, 23 : », « 3, 18%/an ». On ne joint que si la partie fractionnaire est
# immédiatement suivie d'une unité (%/$/€), d'un « : » ou de « /an » — jamais
# une énumération légitime (« en 2024, 3 hausses » n'est pas touchée).
# … et jamais après une ANNÉE (« en 2024, 15% des cas ») ni une DATE
# (« le 14/07, 3% ») : lookbehinds d'exclusion.
_BROKEN_DECIMAL = re.compile(
    r"(\d)(?<!19\d\d)(?<!20\d\d)(?<!/\d\d),\s+(\d{1,2})(?=\s?(?:%|\$|€|/an|\s?:))")


def fix_broken_decimals(node: Any) -> tuple[Any, list[str]]:
    """Répare les décimales françaises cassées par un espace (« -48, 7% »).

    L'hebdo du 15/07 en portait ~15 (tableau positions) : chaque nombre
    paraissait coupé en deux. Origine LLM ; la réparation est déterministe.
    """
    fixes: list[str] = []

    def _fix(text: str) -> str:
        new, n = _BROKEN_DECIMAL.subn(r"\1,\2", text)
        if n:
            fixes.append(f"{n} décimale(s) « X, Y » recollée(s)")
        return new

    return walk_strings(node, _fix), fixes
