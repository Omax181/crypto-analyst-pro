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
    s = f"{abs(round(v, nd)):.{nd}f}".rstrip("0").rstrip(".")
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
    """Qualificatif canonique du dollar depuis le delta DXY du jour (points)."""
    d = _to_float(delta_points)
    if d is None:
        return None
    if d > 0.3:
        return "en hausse"
    if d < -0.3:
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
    ok_words = {"stable": ("stable",),
                "en hausse": ("fort", "en hausse"),
                "en baisse": ("faible", "en baisse")}[qualifier]
    pat = re.compile(r"(?i)\b(dollar|DXY)\s+(fort|faible)\b")

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


def downgrade_speculative_actionable(items: Any) -> list[str]:
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
    return fixes
