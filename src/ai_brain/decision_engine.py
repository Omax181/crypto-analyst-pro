"""Moteur de décision V2 : ponts entre données collectées et Gemini.

Construit le prompt approprié (matin/soir/hebdo), appelle Gemini en mode
JSON, et renvoie un payload dégradé si l'IA est indisponible (le rapport part
quand même avec les données brutes).
"""

from __future__ import annotations

import os
import time
from typing import Any

import yaml

from src.ai_brain.gemini_client import GeminiClient, GeminiQuotaError
from src.ai_brain.prompts.evening_prompt import build_evening_prompt
from src.ai_brain.prompts.morning_prompt import build_morning_prompt
from src.ai_brain.prompts.weekly_prompt import build_weekly_prompt
from src.utils.logger import get_logger

logger = get_logger(__name__)

# v26 (E-A2/E-B1) — MODÈLE DE REPLI par défaut. L'audit evening v25 a montré
# qu'un 503 « high demand » sur le SEUL modèle primaire vidait tout le rapport :
# la capacité de repli existait dans GeminiClient mais n'était jamais câblée
# ici. GEMINI_FALLBACK_MODEL="" (vide) désactive explicitement le repli.
_DEFAULT_FALLBACK_MODEL = "gemini-2.5-flash"

# v26 (E-B1) — ULTIME TENTATIVE DIFFÉRÉE. La panne Gemini du 02/07 a duré plus
# de 4 minutes : les retries « rapides » (2 tentatives × 4 essais internes)
# tombaient tous DANS la fenêtre de panne. Avant de dégrader, on attend
# longuement (10 min par défaut) puis on retente UNE fois — un mail complet à
# +10 min vaut infiniment mieux qu'une coquille dégradée à l'heure pile.
_LAST_CHANCE_PAUSE_DEFAULT_S = 600

# v28 (4.2) — INSISTANCE SUR LE MODÈLE PROFOND (hebdo). Le 07/07, un 503
# persistant sur gemini-3.5-flash a fait rédiger l'hebdo — le rapport le plus
# stratégique — par le modèle de repli, SILENCIEUSEMENT. Décision d'Omar :
# différer (~10-12 min) en réessayant le profond SANS repli, et n'accepter le
# repli qu'ensuite, avec un bandeau « mode dégradé » visible dans le mail.
# 4 vagues : immédiate puis pauses 120/210/300 s (≈ 10,5 min + retries internes).
_INSIST_PRIMARY_PAUSES_S: tuple[int, ...] = (0, 120, 210, 300)


def _fallback_model_from_env() -> str | None:
    """Modèle de repli : env GEMINI_FALLBACK_MODEL, défaut gemini-2.5-flash."""
    raw = os.environ.get("GEMINI_FALLBACK_MODEL")
    if raw is None:
        return _DEFAULT_FALLBACK_MODEL
    raw = raw.strip()
    return raw or None  # chaîne vide = repli désactivé volontairement


def _last_chance_pause_s() -> int:
    """Durée (s) de la pause avant l'ultime tentative (0 = désactivée)."""
    raw = os.environ.get("GEMINI_LAST_CHANCE_PAUSE_S", "").strip()
    try:
        return max(0, int(raw)) if raw else _LAST_CHANCE_PAUSE_DEFAULT_S
    except ValueError:
        return _LAST_CHANCE_PAUSE_DEFAULT_S


# ───────────────────────────────────────────────────────────────────────────
# v27.1 — ROUTAGE AUTOMATIQUE DU MODÈLE PAR NATURE DE TÂCHE.
#
# Objectif d'Omar : « mettre le pro À DISPOSITION » et laisser le système
# l'utiliser tout seul là où c'est STRATÉGIQUE, sans avoir à assigner un modèle
# à chaque mail. On classe donc chaque tâche en deux niveaux (le code SAIT
# lesquelles sont profondes) :
#   • DEEP = analyse/stratégie (matin : thèses + recos ; hebdo : scénarios +
#     positionnement LT) → utilise le modèle PRO s'il est mis à disposition.
#   • FAST = point rapide / classification (soir : P&L + niveaux ; passe 1
#     régime macro) → reste sur le modèle rapide.
#
# Palier GRATUIT (défaut) : AUCUNE de ces variables n'est requise — les défauts
# $0 ci-dessous (gemini-3.5-flash en profond, gemini-2.5-flash en rapide)
# s'appliquent seuls. Palier PAYANT (optionnel) : poser GEMINI_MODEL_DEEP=
# gemini-2.5-pro « active » le pro pour les tâches profondes, sans changer le
# code. Des overrides EXPLICITES par mail restent possibles (avancé) mais ne
# sont JAMAIS nécessaires.
# Le BOT Telegram est indépendant (GEMINI_BOT_MODEL / GEMINI_BOT_FALLBACK,
# cf. telegram_bot/assistant.py) et ne passe PAS par ce moteur.
# ───────────────────────────────────────────────────────────────────────────
_TASK_TIER = {
    "morning": "deep",       # thèses + recos + plan → stratégique
    "weekly": "deep",        # scénarios + positionnement LT → stratégique
    "evening": "fast",       # complément du matin (P&L, niveaux) → rapide
    "macro_regime": "fast",  # classification de régime → rapide
}

# Override EXPLICITE optionnel par mail (échappatoire avancée, prime sur l'auto).
_MODEL_ENV_BY_KIND = {
    "morning": "GEMINI_MODEL_MORNING",
    "evening": "GEMINI_MODEL_EVENING",
    "weekly": "GEMINI_MODEL_WEEKLY",
}

# Défauts $0 (palier GRATUIT). Le meilleur flash GRATUIT — gemini-3.5-flash
# (mai 2026, plafond ~20 requêtes/jour) — est RÉSERVÉ aux tâches PROFONDES
# (analyse matin + hebdo, ~2 appels/jour) où la qualité compte le plus. Les
# tâches RAPIDES (soir, classification de régime) restent sur gemini-2.5-flash,
# au plafond journalier bien plus large : on préserve ainsi le quota serré du
# 3.5-flash pour l'analyse. Le PRO n'existe pas en gratuit (0/0) → jamais visé
# par défaut ; poser GEMINI_MODEL_DEEP=gemini-2.5-pro (facturation) le
# réactiverait pour les tâches profondes, sans toucher au code.
_DEFAULT_DEEP_MODEL = "gemini-3.5-flash"
_DEFAULT_FAST_MODEL = "gemini-2.5-flash"


def _env_model(name: str) -> str | None:
    """Lit un nom de modèle depuis l'env (None si absent/vide)."""
    return (os.environ.get(name) or "").strip() or None


def _model_for_kind(kind: str) -> str | None:
    """Modèle à utiliser pour cette tâche — CHOIX AUTOMATIQUE par tier.

    Priorité :
      1. override explicite du mail (GEMINI_MODEL_MORNING/EVENING/WEEKLY) — rare ;
      2. modèle par tier posé en secret : DEEP → GEMINI_MODEL_DEEP,
                                          FAST → GEMINI_MODEL_FAST ;
      3. base commune GEMINI_MODEL (pin manuel global) ;
      4. défaut $0 du projet : DEEP → gemini-3.5-flash, FAST → gemini-2.5-flash.
    Ne renvoie JAMAIS None ni chaîne vide : un modèle valide est TOUJOURS choisi,
    même sans aucun secret. Le repli de PANNE (GEMINI_FALLBACK_MODEL) est géré
    séparément par le client et reste actif quel que soit ce choix."""
    override = _MODEL_ENV_BY_KIND.get(kind)
    if override:
        explicit = _env_model(override)
        if explicit:
            return explicit
    base = _env_model("GEMINI_MODEL")
    if _TASK_TIER.get(kind, "fast") == "deep":
        return _env_model("GEMINI_MODEL_DEEP") or base or _DEFAULT_DEEP_MODEL
    return _env_model("GEMINI_MODEL_FAST") or base or _DEFAULT_FAST_MODEL


class DecisionEngine:
    """Encapsule les appels Gemini pour les 4 types de rapport."""

    def __init__(self, client: GeminiClient | None = None) -> None:
        # Init "gardé" : si GeminiClient() lève (ex. GEMINI_API_KEY absente ou
        # expirée), on NE propage PAS l'exception. Sinon elle remonterait hors
        # de run_morning/evening/weekly -> main() -> return 1 -> job ROUGE et
        # AUCUN mail envoyé. On mémorise l'erreur et on laisse _safe_json
        # basculer sur le payload dégradé (le rapport part quand même, en mode
        # secours). C'est le comportement attendu par le cahier des charges :
        # une IA indisponible dégrade le rapport, elle ne le bloque jamais.
        self._init_error: str | None = None
        # v26 (E-B1c) — sleep injectable : les tests remplacent time.sleep pour
        # vérifier l'ultime tentative différée sans attendre 10 minutes.
        self._sleep = time.sleep
        if client is not None:
            self.client = client
        else:
            try:
                # v26 (E-A2/E-B1a) — repli de modèle ENFIN câblé : un 503 sur le
                # modèle primaire bascule automatiquement sur le repli au lieu
                # de vider le rapport (audit evening v25 : 503 → mail coquille).
                self.client = GeminiClient(fallback_model=_fallback_model_from_env())
            except Exception as exc:  # noqa: BLE001
                self.client = None
                self._init_error = str(exc)
                logger.error(
                    "Init GeminiClient impossible (%s) : mode dégradé activé.", exc
                )

    def generate_morning(
        self, *, timestamp: str, data: dict[str, Any],
        portfolio_data: dict[str, Any], evening_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Génère le rapport du matin en 2 passes chaînées (V10).

        Passe 1 : régime macro (cadre). Passe 2 : analyse par actif + recos,
        contrainte par le régime de la passe 1. La passe 1 est best-effort :
        si elle échoue, la passe 2 s'exécute quand même (sans cadre macro).
        Le contrat de dégradation est préservé : client absent / quota épuisé /
        échecs répétés → payload dégradé (le rapport part toujours).
        """
        if self.client is None:
            logger.error("Client Gemini indisponible : payload dégradé (morning).")
            return self._degraded(
                "morning", data, self._init_error or "Client IA non initialisé."
            )

        # PASSE 1 — régime macro (optionnelle, jamais bloquante).
        macro_regime = self._macro_regime_pass(timestamp, data)
        if macro_regime:
            data = {**data, "macro_regime": macro_regime}

        # PASSE 2 — rapport complet, cadré par le régime macro.
        portfolio_yaml = yaml.safe_dump(
            portfolio_data, allow_unicode=True, sort_keys=False
        )
        prompt = build_morning_prompt(
            timestamp=timestamp, data=data, portfolio_yaml=portfolio_yaml,
            evening_state=evening_state, macro_regime=macro_regime or None,
        )
        payload = self._safe_json(prompt, data, kind="morning")
        # B6 — expose le verdict de la PASSE 1 dans le payload pour que le rendu
        # puisse l'afficher même si la passe 2 ne l'a pas recopié fidèlement.
        if isinstance(payload, dict) and macro_regime:
            payload["macro_regime_pass1"] = macro_regime
        return payload

    def _macro_regime_pass(
        self, timestamp: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Passe 1 : évalue le régime macro. Best-effort, jamais d'exception.

        Renvoie le dict régime, ou ``{}`` si indisponible (la passe 2 continuera
        sans cadre macro plutôt que de bloquer). Le quota éventuel sera de toute
        façon re-géré (dégradation propre) par la passe 2.
        """
        try:
            from src.ai_brain.prompts.macro_regime_prompt import (
                build_macro_regime_prompt,
            )

            prompt = build_macro_regime_prompt(timestamp=timestamp, data=data)
            # v27.1 — passe 1 = classification légère → modèle RAPIDE (flash),
            # même quand le rapport complet tourne en pro.
            result = self.client.generate_json(
                prompt, model=_model_for_kind("macro_regime"))
            if isinstance(result, dict) and result:
                logger.info(
                    "Passe 1 régime macro : %s (conf. %s%%)",
                    result.get("regime", "?"), result.get("confidence_pct", "?"),
                )
                return result
            logger.warning("Passe 1 régime macro : réponse vide, passe 2 sans cadre.")
        except Exception as exc:  # noqa: BLE001 — passe optionnelle, on n'échoue jamais ici
            logger.warning("Passe 1 régime macro indisponible (%s), on continue.", exc)
        return {}

    def generate_evening(
        self, *, timestamp: str, data: dict[str, Any], morning_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Génère le rapport du soir."""
        prompt = build_evening_prompt(
            timestamp=timestamp, data=data, morning_state=morning_state
        )
        return self._safe_json(prompt, data, kind="evening")

    def generate_weekly(
        self, *, timestamp: str, data: dict[str, Any], week_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Génère le rapport hebdomadaire.

        v28 (4.2) — ``insist_primary=True`` : l'hebdo INSISTE sur le modèle
        profond (vagues différées ~10,5 min sans repli) avant d'accepter le
        modèle de repli, qui est alors signalé par un bandeau dans le mail.
        """
        prompt = build_weekly_prompt(
            timestamp=timestamp, data=data, week_state=week_state
        )
        return self._safe_json(prompt, data, kind="weekly", insist_primary=True)

    def _insist_on_primary(self, prompt: str, task_model: str, kind: str):
        """v28 (4.2) — vagues différées sur le modèle PROFOND, repli désactivé.

        Renvoie le résultat dès qu'une vague aboutit ; ``None`` si le profond
        reste indisponible (l'appelant reprend alors le flux normal AVEC repli).
        Un quota épuisé sur le profond interrompt immédiatement l'insistance
        (différer n'y changerait rien) sans lever : le flux normal gère.
        """
        for pause_s in _INSIST_PRIMARY_PAUSES_S:
            if pause_s:
                logger.warning(
                    "Gemini (%s) : modèle profond %s indisponible — pause %ds "
                    "puis nouvel essai (sans repli).", kind, task_model, pause_s)
                self._sleep(pause_s)
            # getattr : robuste aux clients injectés (tests/stubs) sans l'attribut.
            saved_fallback = getattr(self.client, "fallback_model", None)
            self.client.fallback_model = None  # cette vague vise le profond SEUL
            try:
                result = self.client.generate_json(prompt, model=task_model)
                if result:
                    return result
            except GeminiQuotaError:
                logger.warning(
                    "Gemini (%s) : quota du modèle profond épuisé — insistance "
                    "abandonnée, flux normal (repli possible).", kind)
                return None
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Gemini (%s) : vague profonde échouée (%s).",
                    kind, type(exc).__name__)
            finally:
                self.client.fallback_model = saved_fallback
        return None

    def _safe_json(
        self, prompt: str, data: dict[str, Any], *, kind: str, attempts: int = 2,
        insist_primary: bool = False,
    ) -> dict[str, Any]:
        """Appelle Gemini en JSON avec retry + payload dégradé.

        Conformément à l'exigence « ne pas dégrader à la première erreur » :
        on tente ``attempts`` fois (2 par défaut) avant de basculer en dégradé,
        EN PLUS du retry interne du client sur erreurs transitoires (503…). Une
        réponse vide ou un échec non-quota déclenche une nouvelle tentative ;
        seul le quota épuisé dégrade immédiatement (réessayer serait inutile).
        """
        if self.client is None:
            # Le constructeur a échoué (clé manquante/expirée). On dégrade
            # proprement au lieu de crasher : le rapport est envoyé en secours.
            logger.error("Client Gemini indisponible : payload dégradé (%s).", kind)
            return self._degraded(
                kind, data, self._init_error or "Client IA non initialisé."
            )
        # v27.1 — MODÈLE ROUTÉ PAR TÂCHE (pro/flash selon le type de rapport).
        # Le repli automatique du client (GEMINI_FALLBACK_MODEL) reste actif
        # quel que soit le modèle primaire choisi ici.
        task_model = _model_for_kind(kind)

        # v28 (4.2) — hebdo : vagues différées sur le profond AVANT tout repli.
        if insist_primary:
            result = self._insist_on_primary(prompt, task_model, kind)
            if result:
                return result

        def _tag(result: dict[str, Any]) -> dict[str, Any]:
            # v28 (4.2) — résultat produit par le modèle de REPLI ? Le rendu
            # affichera un bandeau « mode dégradé » (décision Omar 07/07) au
            # lieu de dégrader silencieusement le rapport stratégique.
            used = getattr(self.client, "last_used_model", None)
            if used and used != task_model:
                result["_model_degraded"] = True
                result["_model_degraded_note"] = (
                    f"Modèle profond {task_model} indisponible — analyse "
                    f"générée par le modèle de repli {used}.")
            return result

        last_reason = "Génération IA indisponible."
        for attempt in range(1, attempts + 1):
            try:
                result = self.client.generate_json(prompt, model=task_model)
                if result:
                    return _tag(result)
                last_reason = "Réponse IA vide."
                logger.warning(
                    "Gemini (%s) tentative %d/%d : réponse vide — nouvelle tentative.",
                    kind, attempt, attempts,
                )
            except GeminiQuotaError:
                logger.error("Quota Gemini épuisé : payload dégradé (%s).", kind)
                return self._degraded(kind, data, "Quota IA épuisé.")
            except Exception as exc:  # noqa: BLE001
                last_reason = "Génération IA indisponible."
                logger.exception(
                    "Échec Gemini (%s) tentative %d/%d : %s", kind, attempt, attempts, exc
                )
        # v26 (E-B1c) — ULTIME TENTATIVE DIFFÉRÉE avant de dégrader. Les pannes
        # 503 « high demand » durent typiquement quelques minutes (celle du
        # 02/07 a couvert TOUTE la fenêtre de retry rapide, 14:03→14:07). On
        # attend longuement (GEMINI_LAST_CHANCE_PAUSE_S, défaut 10 min) puis on
        # retente une dernière fois : un rapport complet en retard bat toujours
        # une coquille dégradée à l'heure.
        pause_s = _last_chance_pause_s()
        if pause_s > 0:
            logger.warning(
                "Gemini (%s) : %d tentatives épuisées — pause %ds puis ultime "
                "tentative avant dégradation.", kind, attempts, pause_s,
            )
            self._sleep(pause_s)
            try:
                result = self.client.generate_json(prompt, model=task_model)
                if result:
                    logger.info(
                        "Gemini (%s) : ultime tentative RÉUSSIE après pause.", kind
                    )
                    return _tag(result)
                last_reason = "Réponse IA vide."
            except GeminiQuotaError:
                logger.error("Quota Gemini épuisé : payload dégradé (%s).", kind)
                return self._degraded(kind, data, "Quota IA épuisé.")
            except Exception as exc:  # noqa: BLE001
                last_reason = "Génération IA indisponible."
                logger.exception(
                    "Échec Gemini (%s) ultime tentative : %s", kind, exc
                )
        logger.error(
            "Gemini (%s) : tentatives épuisées → payload dégradé.", kind
        )
        return self._degraded(kind, data, last_reason)

    @staticmethod
    def _degraded(kind: str, data: dict[str, Any], reason: str) -> dict[str, Any]:
        """Payload minimal de secours, propre à chaque type de rapport."""
        base = {
            "header": {"title": "Veille crypto", "subtitle": "rapport dégradé"},
            "_degraded": True,
            "footer": {"note": f"{reason} Données brutes disponibles."},
        }
        if kind == "morning":
            base["essentiel"] = [f"⚠️ {reason}"]
            base["all_positions_summary"] = data.get("all_positions_summary", [])
            base["win_rate"] = data.get("win_rate", {})
        elif kind == "evening":
            # v26 (E-A13) — la clé historique ``delta_of_the_day`` n'était lue
            # par AUCUN template (clé morte) : même l'avertissement du mode
            # secours n'apparaissait pas. Le template lit ``delta_summary``
            # (objets {icon, text}) — on émet donc le bon format au bon endroit.
            base["delta_summary"] = [{"icon": "⚠", "text": reason}]
        elif kind == "weekly":
            base["weekly_narrative"] = reason
            base["weekly_predictions_scoring"] = data.get("win_rate", {})
        return base
