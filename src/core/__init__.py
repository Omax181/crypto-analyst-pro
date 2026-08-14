"""Noyau V31 — autorités uniques.

Chaque module de ce paquet est une AUTORITÉ UNIQUE au sens de la SPEC V31 :
aucun autre module du projet n'a le droit de dupliquer sa responsabilité.

  params        paramètres métier (absent => NON_EVALUABLE, jamais de défaut)
  formatter     UNIQUE représentation textuelle d'un nombre (I27, I59, I60)
  source_result enveloppe de transport (I30, I31)
  registry      registre des sources, fraîcheur, criticité, DEAD (I32, I34)
  volatility    sigma journalière (Parkinson) et sigma d'horizon
  horizon       HorizonSpec et détermination déterministe de l'horizon (I24)
  viability     moteur de viabilité V1..V4 (I8, I14, I20)
  book          RecommendationBook : machine à états, autorité unique (I1, I2)
  facts         FactStore : faits, fraîcheur, formatage (I33)
  content       contrat de contenu, validation par REJET (I25..I29)
  metrics       les six métriques et leurs règles de publication (I49..I52)
  runlog        RunSummary (I58)
"""
