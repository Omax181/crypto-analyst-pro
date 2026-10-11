"""Standard de preuve v33.1 — chaque chiffre porte sa classe et sa provenance.

LE PROBLÈME QUE CE MODULE RÉSOUT
================================

La v33 initiale échouait le cas critique d'Omar, et elle l'échouait à l'envers :

    +400 % avec une preuve faible   → RECOMMANDÉ
    +20 %  avec une preuve robuste  → REJETÉ

La cause est mathématique, pas conceptuelle. Le potentiel se calculait
``référence / ratio − 1`` : une fonction **non linéaire et non bornée**. Une
preuve faible multipliait le rendement requis par 1,6 (via la modulation) mais
multipliait le potentiel affiché par 12,7. La pénalité de preuve était donc
noyée, et le système récompensait mécaniquement l'ignorance.

D'où deux corrections, toutes deux structurelles :

1. **Le potentiel se calcule en espace LOGARITHMIQUE.** ``ln(référence/ratio)``
   est symétrique et sa sensibilité est bornée ; la forme précédente explosait
   quand le dénominateur tendait vers zéro.

2. **L'amplitude ne vaut jamais preuve.** Un écart au-delà du SUPPORT OBSERVÉ
   des pairs ne signifie pas « occasion à +400 % » : il signifie que l'actif
   **n'appartient pas à la population de référence**. Un TVL erroné, un
   protocole d'une autre nature, une mesure cassée produisent exactement cette
   signature. On refuse alors de chiffrer.

   La borne est le pair le MOINS CHER réellement observé — **sans paramètre**.
   Un multiple d'écart-type choisi ne sert plus qu'à borner le cas où le
   support lui-même est corrompu par un pair erroné (voir ``comparabilite``).

LA TAXONOMIE
============

Chaque grandeur qui entre dans une décision porte une classe :

===== ============================= ==========================================
Classe Nature                        Peut-elle déclencher une recommandation ?
===== ============================= ==========================================
A     fait mesuré                   oui, en combinaison
B     calcul déterministe sur des A  oui
C     estimation statistique validée oui, si l'erreur est connue ET bornée
D     scénario / hypothèse           **JAMAIS SEULE**
===== ============================= ==========================================

La règle d'Omar est absolue : *si une décision critique dépend principalement
d'une hypothèse non démontrée, il n'y a pas de recommandation.* Ce module la
rend mécanique plutôt que déclarative.

Aucune classe C n'existe aujourd'hui dans le moteur : aucune estimation
statistique de rendement n'est validable sur ces données (±15,8 points à
30 jours, ±192 points à 12 mois). Le moteur n'en revendique donc aucune.
"""

from __future__ import annotations

import math
from statistics import median
from typing import Any, Optional

# ── classes de preuve, de la plus forte à la plus faible ──────────────────
FAIT = "A"            # valeur observée d'une source identifiée
CALCUL = "B"          # calcul exact sur des faits mesurés
ESTIMATION = "C"      # estimation statistique dont l'erreur est connue ET bornée
SCENARIO = "D"        # hypothèse, narratif, extrapolation — jamais seule

_ORDRE = {FAIT: 3, CALCUL: 3, ESTIMATION: 2, SCENARIO: 0}

_LIBELLES = {
    FAIT: "fait mesuré",
    CALCUL: "calcul déterministe",
    ESTIMATION: "estimation statistique validée",
    SCENARIO: "scénario / hypothèse",
}


def libelle(classe: str) -> str:
    """Libellé lisible d'une classe de preuve."""
    return _LIBELLES.get(classe, "classe inconnue")


def piece(
    valeur: Any,
    classe: str,
    source: str,
    calcul: str = "",
    *,
    reserves: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Une grandeur, sa classe de preuve et sa traçabilité complète.

    Exigence d'Omar (§23) : pour chaque chiffre publié, on doit pouvoir suivre
    ``SOURCE → DONNÉE → CALCUL → OUTPUT``. Un chiffre qui ne peut pas exhiber
    cette chaîne n'est pas présenté comme un fait.

    Args:
        valeur: la grandeur elle-même (``None`` si non mesurable).
        classe: ``FAIT`` / ``CALCUL`` / ``ESTIMATION`` / ``SCENARIO``.
        source: d'où vient la donnée brute (nom de l'API, du module…).
        calcul: comment la valeur en est dérivée.
        reserves: limites connues, publiées avec la valeur.
    """
    return {"valeur": valeur, "classe": classe, "classe_libelle": libelle(classe),
            "source": source, "calcul": calcul, "reserves": list(reserves or [])}


def suffisante(*pieces: dict[str, Any]) -> bool:
    """Ces pièces suffisent-elles à déclencher une recommandation ?

    Deux conditions, et la seconde est celle qu'Omar a posée comme absolue :

    * toute pièce doit exister et porter une valeur ;
    * **aucune pièce critique ne peut être de classe D.** Une hypothèse ne se
      compense pas par une amplitude, ni par le nombre d'autres pièces.
    """
    if not pieces:
        return False
    for p in pieces:
        if not isinstance(p, dict) or p.get("valeur") is None:
            return False
        if _ORDRE.get(p.get("classe"), 0) <= 0:
            return False
    return True


def classe_faible(*pieces: dict[str, Any]) -> Optional[dict[str, Any]]:
    """La pièce la plus faible du lot — celle qui motive un refus."""
    valides = [p for p in pieces if isinstance(p, dict)]
    if not valides:
        return None
    return min(valides, key=lambda p: (_ORDRE.get(p.get("classe"), 0),
                                       p.get("valeur") is not None))


# ── comparabilité : l'actif appartient-il à la population de référence ? ──

def dispersion_log(valeurs: list[float]) -> Optional[dict[str, float]]:
    """Médiane et écart absolu médian (MAD) en espace logarithmique.

    Le MAD est utilisé plutôt que l'écart-type parce qu'il résiste aux valeurs
    aberrantes : sur un échantillon de six pairs, un seul ratio cassé
    déplacerait un écart-type au point de rendre tout comparable.

    Returns:
        ``{mediane_log, mad_log, n, mediane}`` — ``None`` si l'échantillon est
        vide ou contient des valeurs non strictement positives.
    """
    propres = [v for v in valeurs if isinstance(v, (int, float))
               and v == v and math.isfinite(v) and v > 0]
    if not propres:
        return None
    logs = [math.log(v) for v in propres]
    med = median(logs)
    mad = median([abs(x - med) for x in logs])
    tries = sorted(propres)
    pos = (len(tries) - 1) * 0.25
    lo_i = int(pos)
    hi_i = min(lo_i + 1, len(tries) - 1)
    q25 = tries[lo_i] + (tries[hi_i] - tries[lo_i]) * (pos - lo_i)
    return {"mediane_log": med, "mad_log": mad, "n": len(propres),
            "mediane": math.exp(med),
            # Premier quartile (côté BON MARCHÉ) : ancre PRUDENTE. Une thèse
            # qui tient même si la réévaluation s'arrête au premier quartile
            # des pairs est plus robuste qu'une thèse qui exige la médiane.
            "q25": q25,
            # SUPPORT RÉELLEMENT OBSERVÉ de l'échantillon. C'est lui, et non un
            # multiple d'écart choisi, qui borne le potentiel défendable :
            # on ne peut pas revendiquer plus de réévaluation que n'en implique
            # le pair le MOINS CHER déjà observé.
            "min": min(propres), "max": max(propres),
            "borne_haute_log": med - math.log(min(propres)),
            "borne_basse_log": math.log(max(propres)) - med}


def comparabilite(
    ratio: float,
    disp: dict[str, float],
    *,
    k_aberrant: float,
    mad_max: float,
    n_min: int,
) -> dict[str, Any]:
    """L'actif est-il comparable à ses pairs, et la référence est-elle valide ?

    LA BORNE PRIMAIRE EST LE SUPPORT OBSERVÉ, PAS UN MULTIPLE D'ÉCART
    ==================================================================

    Une version antérieure bornait le potentiel à ``k × MAD`` avec ``k = 3``,
    présenté comme « la convention robuste standard (Tukey/MAD) ». **C'était
    faux.** Les conventions réellement standard sont bien plus larges :

    ======================================  =========  ===========
    Convention                              en MAD     en σ
    ======================================  =========  ===========
    Iglewicz–Hoaglin (Z modifié > 3,5)      5,19       3,5
    « 3 sigma » robuste (σ = 1,4826 · MAD)  4,45       3,0
    L'ancien ``k = 3``                      3,00       **2,02**
    ======================================  =========  ===========

    ``k = 3`` flaggait 4,3 % d'une population normale contre 0,27 % pour la
    règle des 3 σ : c'était une valeur choisie parce qu'elle produisait les
    verdicts attendus, pas une convention.

    La borne primaire est désormais **le support réellement observé** :
    on ne peut pas revendiquer une réévaluation supérieure à celle qu'implique
    déjà le pair le moins cher. C'est **sans paramètre**, et c'est la seule
    borne que les données justifient — au-delà du support, aucun actif
    comparable ne trade à ce niveau, donc rien n'atteste qu'il soit atteignable.

    Le MAD reste, à la valeur STANDARD des 3 σ, comme **garde contre un pair
    corrompu** : mesuré, un seul ratio erroné du côté bon marché fait passer le
    support de +56 % à +2400 %, et le MAD le ramène à +94 %. Il n'est donc pas
    décoratif — il borne exactement le cas où le support n'est plus fiable.

    Trois conditions, toutes indispensables :

    1. **Échantillon suffisant** — une médiane sur trois points n'est pas une
       référence.
    2. **Référence informative** — des pairs trop dispersés n'ont pas de
       médiane exploitable : mesuré, un échantillon à MAD_log 0,77 autorise
       +369 %, ce qui est absurde.
    3. **Actif dans le support**, borné par le garde MAD.

    Returns:
        ``{comparable, ecart_log, borne_log, borne_support_log, borne_mad_log,
        borne_liante, motif, potentiel_max_pct}``.
    """
    n, mad = int(disp.get("n", 0)), float(disp.get("mad_log", 0.0))
    ecart = disp["mediane_log"] - math.log(ratio)   # > 0 ⇒ actif moins cher

    def _refus(motif: str, borne: Optional[float] = None) -> dict[str, Any]:
        return {"comparable": False, "ecart_log": ecart,
                "borne_log": borne, "borne_support_log": None,
                "borne_mad_log": None, "borne_liante": None, "motif": motif,
                "potentiel_max_pct": (round((math.exp(borne) - 1) * 100, 1)
                                      if borne is not None else None)}

    if n < n_min:
        return _refus(f"échantillon de pairs insuffisant ({n} < {n_min}) — une "
                      "médiane sur si peu de points n'est pas une référence")
    if mad > mad_max:
        return _refus(f"pairs trop hétérogènes (MAD log {mad:.2f} > "
                      f"{mad_max:.2f}) — leur médiane n'est pas une référence "
                      "exploitable")
    if mad <= 0.0:
        return _refus("dispersion des pairs nulle — aucun écart n'est "
                      "interprétable")

    # Borne PRIMAIRE : le support observé, asymétrique (haut ≠ bas).
    b_sup = float(disp["borne_haute_log"] if ecart >= 0
                  else disp["borne_basse_log"])
    b_mad = k_aberrant * mad
    borne = min(b_sup, b_mad)
    liante = "support observé" if b_sup <= b_mad else "garde MAD (pair corrompu)"

    detail = {"ecart_log": ecart, "borne_log": borne,
              "borne_support_log": b_sup, "borne_mad_log": b_mad,
              "borne_liante": liante,
              "potentiel_max_pct": round((math.exp(borne) - 1) * 100, 1)}

    if abs(ecart) > borne:
        pair = disp.get("min") if ecart >= 0 else disp.get("max")
        return {**detail, "comparable": False,
                "motif": (f"écart de {abs(ecart) / mad:.1f} MAD à la médiane, "
                          f"au-delà du {liante} (pair extrême {pair:.1f}×) — "
                          "l'actif n'appartient pas à cette population : mesure "
                          "douteuse ou nature différente, pas une décote")}
    return {**detail, "comparable": True,
            "motif": (f"écart de {abs(ecart) / mad:.1f} MAD, dans le support "
                      f"observé des pairs (borne : {liante})")}
