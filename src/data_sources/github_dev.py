"""Source GitHub : activité de développement = signal de santé projet.

Pour chaque crypto disposant d'un repo public configuré, on mesure les
commits récents, les contributeurs et la dernière release. Token requis
pour des limites correctes (5000 req/h).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from src.data_sources.http import get_json
from src.utils.cache import CACHE
from src.utils.logger import get_logger
from src.utils.portfolio_loader import load_config

logger = get_logger(__name__)

_SOURCES = load_config("sources")
_REPOS: dict[str, list[str]] = _SOURCES["github_repos"]
_API = "https://api.github.com"


def _headers() -> dict[str, str]:
    token = os.environ.get("GH_TOKEN", "").strip()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def get_dev_activity(symbol: str) -> dict[str, Any]:
    """Mesure l'activité dev d'une crypto sur ~30 jours.

    Args:
        symbol: ticker du portfolio.

    Returns:
        Dict ``{available, repos, commits_30d, last_commit_days_ago,
        last_release, contributors_recent}``. ``available=False`` si aucun
        repo connu.
    """
    repos = _REPOS.get(symbol) or []
    if not repos:
        return {"available": False, "reason": "pas de repo public connu"}

    def _fetch() -> dict[str, Any]:
        total_commits = 0
        last_commit_dt: datetime | None = None
        last_release: str | None = None
        contributors: set[str] = set()
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

        for repo in repos:
            commits = get_json(
                f"{_API}/repos/{repo}/commits",
                params={"since": since, "per_page": 100},
                headers=_headers(),
            )
            if isinstance(commits, list):
                total_commits += len(commits)
                for c in commits:
                    author = (c.get("author") or {}).get("login")
                    if author:
                        contributors.add(author)
                    date_str = (
                        c.get("commit", {}).get("author", {}).get("date")
                    )
                    if date_str:
                        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                        if last_commit_dt is None or dt > last_commit_dt:
                            last_commit_dt = dt

            release = get_json(
                f"{_API}/repos/{repo}/releases/latest", headers=_headers()
            )
            if isinstance(release, dict) and release.get("tag_name"):
                last_release = release.get("tag_name")

        days_ago = (
            (datetime.now(timezone.utc) - last_commit_dt).days
            if last_commit_dt
            else None
        )
        return {
            "available": True,
            "repos": repos,
            "commits_30d": total_commits,
            "last_commit_days_ago": days_ago,
            "last_release": last_release,
            "contributors_recent": len(contributors),
        }

    ttl = 14400  # 4 h
    return CACHE.get_or_compute(f"github:{symbol}", ttl, _fetch)
