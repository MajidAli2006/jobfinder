"""Offline fixture source."""

from __future__ import annotations

import json
from datetime import timedelta

from .. import config
from ..models import RawJob
from ..utils import now_local
from .base import Source


class FixtureSource(Source):
    name = "fixtures"
    label = "Offline fixture corpus"

    def fetch(self) -> list[RawJob]:
        path = config.SAMPLE_JOBS_FILE
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        now = now_local()
        jobs: list[RawJob] = []
        for index, item in enumerate(data):
            days_ago = item.get("days_ago", 0)
            hours_ago = item.get("hours_ago", 3)
            posted = now - timedelta(days=days_ago, hours=hours_ago)
            jobs.append(RawJob(
                source=item.get("source", self.name),
                source_id=item.get("id") or f"fixture-{index}",
                title=item.get("title", ""),
                company=item.get("company", ""),
                url=item.get("url", ""),
                apply_url=item.get("apply_url") or item.get("url", ""),
                description=item.get("description", ""),
                location_raw=item.get("location", ""),
                posted_at=posted,
                employment_type_raw=item.get("employment_type", ""),
                salary_raw=item.get("salary_raw", ""),
                salary_min=item.get("salary_min"),
                salary_max=item.get("salary_max"),
                salary_currency=item.get("salary_currency", ""),
                company_website=item.get("company_website", ""),
                tags=item.get("tags", []),
                extra=item.get("extra", {}),
            ))
        return jobs
