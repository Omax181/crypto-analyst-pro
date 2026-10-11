"""Gate de cohérence des recos du matin — v33 : réduit à ce qui vit encore.

Historique (v28 · M-A1/A2/A3/A4, décision d'Omar du 07/07) : quand l'action
venait du modèle de langage, ce module rattrapait les recos incohérentes avec
leurs propres preuves — « MAINTENIR » au plafond de concentration, tactique
dégradée en « SURVEILLER » si EV 30 j < 0 ou R:R < 1,2, confiance plafonnée à
70 % — et le garde « anti-glissement » (v30 #1/#68) bloquait un renfort sous un
stop de prix déjà franchi.

Audit zero-trust (01/10/2026) — ces deux portes sont RETIRÉES :

* l'action est désormais décidée par le moteur d'opportunité (ou par les
  règles de prise de profit du radar) : il n'existe plus de RENFORCER rédigé
  par le modèle à rattraper ;
* leurs critères — plafond de concentration, EV 30 j fondée sur une
  probabilité heuristique, R:R, stop de prix — sont ceux qu'Omar a écartés
  (« pas de hard cap d'exposition » le 01/10 ; « aucun stop de trading
  arbitraire » le 26/08) ;
* sur une décision du moteur, elles ne pouvaient plus rien faire : sans plan
  V30, ni EV ni R:R à lire, et l'avertissement de stop qu'elles posaient
  n'était rendu par aucun gabarit. Du code correct isolément mais sans effet
  en production — exactement ce que l'audit devait éliminer.

Restent la ligne d'abstention et le filtre d'exécutabilité du digest Telegram.
"""

from __future__ import annotations

from typing import Any


def executable_for_top_action(t: dict[str, Any]) -> bool:
    """Un geste est-il exécutable aujourd'hui ? (digest Telegram)

    Une décision du moteur (RENFORCER) ou une règle de prise de profit du radar
    (ALLÉGER) l'est toujours ; toute autre posture ne l'est jamais.
    """
    if not isinstance(t, dict):
        return False
    a = str(t.get("action") or "").upper()
    if ("ALLÉG" in a or "ALLEG" in a) and t.get("v33_trigger"):
        return True
    _v33 = t.get("v33_decision")
    return ("RENFORC" in a and isinstance(_v33, dict)
            and _v33.get("decided") is True)


NOTHING_TO_DO_LINE = (
    "Ne rien faire aujourd'hui — le moteur n'a décidé aucun renfort : aucun "
    "potentiel mesuré ne couvre son rendement requis. S'abstenir est aussi une "
    "décision."
)
