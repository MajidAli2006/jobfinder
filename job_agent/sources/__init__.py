"""Source registry."""

from __future__ import annotations

from .. import config
from .base import Source
from .boards import (
    Arbeitnow, CryptocurrencyJobs, EuRemoteJobs, HackerNewsHiring, Himalayas,
    DiscoveredPlatforms, Jobicy, NoDesk, RemoteOK, RemoteRocks, Remotive,
    StructuredBoards, WeWorkRemotely, WorkingNomads,
)
from .company_boards import CompanyBoards, DiscoveredBoards
from .jobgether import Jobgether
from .linkedin import Careerjet, LinkedIn
from .fixtures import FixtureSource
from .market_boards import Adzuna, Jooble, Reed
from .partner_apis import GoogleJobs, Indeed, ZipRecruiter

ALL_SOURCE_CLASSES = (
    RemoteOK,
    Remotive,
    WeWorkRemotely,
    Arbeitnow,
    Jobicy,
    WorkingNomads,
    Himalayas,
    RemoteRocks,
    NoDesk,
    EuRemoteJobs,
    CryptocurrencyJobs,
    HackerNewsHiring,
    CompanyBoards,
    Jobgether,
    LinkedIn,
    Careerjet,
    Adzuna,
    Reed,
    Jooble,
    GoogleJobs,
    Indeed,
    ZipRecruiter,
    StructuredBoards,
    DiscoveredPlatforms,
    # Last: it mines the URLs every source above has collected.
    DiscoveredBoards,
)


def build_sources(offline: bool = False, only: tuple[str, ...] = ()) -> list[Source]:
    if offline:
        return [FixtureSource()]

    selected = only or config.ENABLED_SOURCES
    sources: list[Source] = []
    for cls in ALL_SOURCE_CLASSES:
        instance = cls()
        if selected and instance.name not in selected:
            continue
        if not instance.enabled:
            continue
        if not instance.serves_active_search():
            continue
        sources.append(instance)
    return sources


def known_source_names() -> frozenset[str]:
    """Every name `--sources` accepts, including the offline corpus."""
    return frozenset({cls.name for cls in ALL_SOURCE_CLASSES} | {FixtureSource.name})


def skipped_for_market() -> list[tuple[str, str]]:
    """(label, markets) for configured boards this search cannot use."""
    out = []
    for cls in ALL_SOURCE_CLASSES:
        instance = cls()
        if instance.enabled and not instance.serves_active_search():
            out.append((instance.label, ", ".join(sorted(set(instance.markets))[:4])))
    return out


def source_catalogue() -> list[tuple[str, str, bool]]:
    """(name, label, configured) for every known source."""
    out = []
    for cls in ALL_SOURCE_CLASSES:
        instance = cls()
        out.append((instance.name, instance.label, instance.enabled))
    return out


def keyed_sources() -> list[Source]:
    """Sources that need API credentials, whether or not they are configured."""
    return [cls() for cls in ALL_SOURCE_CLASSES if cls.credentials]


__all__ = ["Source", "build_sources", "source_catalogue", "skipped_for_market",
           "ALL_SOURCE_CLASSES", "known_source_names"]
