"""Migration de l'état pré-V31 — SPEC V31 §8, §9.2.

L'ancien état (``state/active_recommendations.json``, ``prediction_history``,
``reco_changes``, ``weekly_snapshots``…) n'est PAS convertible tel quel : il a
été produit sous des règles incompatibles avec le carnet V31.

  - Une entrée dont le stop est du MAUVAIS CÔTÉ de l'entrée (INJ, LINK, RSR en
    v30.1) viole I4 : elle ne peut pas exister comme contrat. Elle n'est pas
    migrée — la corriger reviendrait à inventer un contrat qui n'a jamais été
    communiqué.
  - Les entrées structurellement valides sont importées en ÉTAT TERMINAL
    ``SUPERSEDED`` et marquées ``scoring_regime = "pre_v31"`` : elles restent
    consultables mais sont EXCLUES du scoring (SPEC §3, table close), car
    l'ancien régime clôturait sur un délai de trente jours, pas sur une clôture.

Le comportement dépend du paramètre métier ``historical_treatment`` :
``purge`` | ``mark`` | ``reset``. Absent -> la migration est BLOQUÉE, et le dit.

Usage :
    python -m scripts.migrate_v31 --dry-run
    python -m scripts.migrate_v31 --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core import params
from src.core.book import (ContractValidityError, Direction, State,
                           validate_contract)
from src.utils.logger import get_logger

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
BOOK_DIR = STATE / "book"
ARCHIVE = STATE / "pre_v31"

LEGACY_FILES = (
    "active_recommendations.json", "prediction_history.json",
    "reco_changes.json", "reco_dismissals.json", "weekly_snapshots.json",
    "source_health.json", "thesis_scores.json", "weekly_calls.json",
    "market_regime.json", "last_morning_report.json",
    "last_evening_report.json", "last_weekly_report.json",
    "seen_news.json",
)


def _load(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("%s illisible (%s).", path.name, exc)
        return None


def _f(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) \
        else None


def inspect() -> dict[str, Any]:
    """Analyse l'ancien état sans rien écrire."""
    legacy = _load(STATE / "active_recommendations.json") or []
    migratable: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in legacy if isinstance(legacy, list) else []:
        if not isinstance(item, dict):
            continue
        asset = str(item.get("asset") or item.get("symbol") or "?").upper()
        entry = _f(item.get("entry_price") or item.get("entry"))
        target = _f(item.get("ct_target") or item.get("target"))
        stop = _f(item.get("stop_loss") or item.get("stop"))
        if entry is None or target is None or stop is None:
            rejected.append({"asset": asset,
                             "reason": "prix de contrat incomplets"})
            continue
        try:
            validate_contract(asset, Direction.LONG_INCREASE, entry, target,
                              stop)
        except ContractValidityError as exc:
            rejected.append({"asset": asset, "reason": str(exc)})
            continue
        migratable.append({"asset": asset, "entry": entry, "target": target,
                           "stop": stop, "created_at": item.get("created_at")})
    return {"total": len(legacy) if isinstance(legacy, list) else 0,
            "migratable": migratable, "rejected": rejected}


def _to_contract(item: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    created = item.get("created_at") or now
    return {
        "id": f"{item['asset']}-prev31",
        "asset": item["asset"],
        "direction": Direction.LONG_INCREASE.value,
        "horizon": "SWING",
        "created_at": created,
        "source_run": "migration-v31",
        "scored_contract": {
            "entry_price": item["entry"], "target": item["target"],
            "stop": item["stop"], "expires_at": created, "horizon": "SWING",
            "sizing": {}, "viability": {}, "p_null": None,
            "p_breakeven": None, "delta_required": None, "issued_at": created,
        },
        "operational_plan": [],
        "tracking": {},
        # SUPERSEDED : score None dans la table close -> EXCLU des métriques.
        "state": {"value": State.SUPERSEDED.value, "since": now,
                  "reason": "migration_v31"},
        "outcome": {},
        "counters": {"reissues": 0},
        "schema_version": 1,
        "scoring_regime": "pre_v31",
    }


def apply(treatment: str, report: dict[str, Any]) -> None:
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    for name in LEGACY_FILES:
        src = STATE / name
        if src.exists():
            shutil.move(str(src), str(ARCHIVE / name))
            logger.info("Archivé : %s", name)

    if treatment == "purge":
        logger.info("Traitement « purge » : aucun contrat hérité n'est importé.")
        return
    if treatment == "reset":
        if BOOK_DIR.exists():
            shutil.rmtree(BOOK_DIR)
        logger.info("Traitement « reset » : carnet remis à zéro.")
        return

    # treatment == "mark"
    BOOK_DIR.mkdir(parents=True, exist_ok=True)
    contracts_path = BOOK_DIR / "contracts.json"
    existing = _load(contracts_path) or []
    if not isinstance(existing, list):
        existing = []
    imported = [_to_contract(i) for i in report["migratable"]]
    contracts_path.write_text(
        json.dumps(existing + imported, ensure_ascii=False, indent=2),
        encoding="utf-8")
    logger.info("Traitement « mark » : %d contrat(s) importé(s) en SUPERSEDED, "
                "hors scoring.", len(imported))


def main() -> int:
    parser = argparse.ArgumentParser(description="Migration de l'état vers V31.")
    parser.add_argument("--apply", action="store_true",
                        help="applique la migration (sinon, simulation)")
    args = parser.parse_args()

    report = inspect()
    print(f"Anciennes recommandations trouvées : {report['total']}")
    print(f"  migrables    : {len(report['migratable'])}")
    for m in report["migratable"]:
        print(f"    · {m['asset']}")
    print(f"  NON migrables : {len(report['rejected'])}")
    for r in report["rejected"]:
        print(f"    · {r['asset']} — {r['reason']}")

    treatment = params.historical_treatment()
    if treatment is None:
        print("\nMigration BLOQUÉE : le paramètre métier « historical_treatment »"
              " est absent de config/params.yaml.\n"
              "Valeurs acceptées : purge | mark | reset.\n"
              "Aucune valeur n'est choisie à ta place (SPEC §9.3).")
        return 2
    if treatment not in ("purge", "mark", "reset"):
        print(f"\nValeur inconnue pour « historical_treatment » : {treatment}.")
        return 2

    if not args.apply:
        print(f"\nSimulation. Traitement configuré : « {treatment} ». "
              f"Relance avec --apply pour écrire.")
        return 0

    apply(treatment, report)
    print(f"\nMigration appliquée ({treatment}). "
          f"Ancien état archivé dans {ARCHIVE}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
