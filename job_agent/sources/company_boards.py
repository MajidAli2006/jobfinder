"""Employer boards: a curated seed list, plus whatever this run discovers."""

from __future__ import annotations

import json
import time

from .. import config
from ..models import RawJob
from ..utils import normalize
from . import ats
from .base import Source, seen_urls

DEFAULT_BOARDS = [
    {"type": "greenhouse", "token": "monzo", "name": "Monzo",
     "website": "https://monzo.com", "region": "United Kingdom", "sector": "tech"},
    {"type": "greenhouse", "token": "wise", "name": "Wise",
     "website": "https://wise.com", "region": "United Kingdom", "sector": "tech"},
    {"type": "lever", "token": "toptal", "name": "Toptal",
     "website": "https://toptal.com", "region": "", "sector": "tech"},
]


def load_boards() -> list[dict]:
    """The seed list, from the data file when present."""
    path = config.COMPANY_BOARDS_FILE
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    else:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(DEFAULT_BOARDS, indent=2), encoding="utf-8")
        except OSError:
            pass
    return DEFAULT_BOARDS


def serves(board: dict) -> bool:
    """Is this seeded employer worth asking for the active search?

    A seed with no region is asked about anywhere. One naming a region is asked
    only when the search wants that region — fetching Monzo's board for a
    plumbing search in Lagos is a request nobody benefits from.
    """
    region = normalize(str(board.get("region") or ""))
    if not region:
        return True
    wanted = Source.wanted_countries()
    if not wanted:
        return True
    return any(region in country or country in region for country in wanted)


class CompanyBoards(Source):
    """The curated seed list, filtered to the region being searched."""

    name = "company_boards"
    label = "Employer ATS boards (curated)"

    def boards(self) -> list[dict]:
        return [b for b in load_boards() if serves(b)]

    def fetch(self) -> list[RawJob]:
        jobs: list[RawJob] = []
        for board in self.boards():
            found = ats.fetch_board(board.get("type", ""), board.get("token", ""), board)
            for job in found:
                job.source = self.name
            jobs.extend(found)
            time.sleep(config.HTTP_DELAY)
        return self.dedupe_by_id(jobs)


class DiscoveredBoards(Source):
    """Employer boards found in the adverts this run already collected.

    Aggregators link to the employer's own system constantly, so the postings
    gathered by every other source carry the tokens for boards nobody curated.
    Reading those means a plumbing search in Lagos finds plumbing employers
    without anyone having listed them first.

    Runs last, so the other sources have filled the registry.
    """

    name = "discovered_boards"
    label = "Employer ATS boards (discovered this run)"
    MAX_BOARDS = 60

    def tokens(self) -> list[tuple[str, str]]:
        """(system, token) pairs worth trying, most reliable first."""
        blob = "\n".join(seen_urls())
        found = ats.discover_tokens(blob)
        pairs: list[tuple[str, str]] = []
        for kind, tokens in found.items():
            pairs.extend((kind, token) for token in sorted(tokens))
        return pairs[: self.MAX_BOARDS]

    def fetch(self) -> list[RawJob]:
        if not config.DISCOVER_EMPLOYER_BOARDS:
            return []
        jobs: list[RawJob] = []
        for kind, token in self.tokens():
            found = ats.fetch_board(kind, token)
            for job in found:
                job.source = self.name
            jobs.extend(found)
            time.sleep(config.HTTP_DELAY)
        return self.dedupe_by_id(jobs)
