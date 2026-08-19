"""Isolation that applies to the whole suite.

The tests must behave the same on a maintainer's laptop as on a bare CI
checkout. `candidate.local.json` is personal and git-ignored, so it exists on
exactly one of those — and every `profile.reset()` reads it. Pointing the
lookup at a path that does not exist means the built-in profile describes
nobody everywhere, which is what CI actually runs against.
"""

from __future__ import annotations

import pytest

from job_agent import profile


@pytest.fixture(autouse=True, scope="session")
def ignore_the_local_candidate_file(tmp_path_factory):
    real = profile.LOCAL_CANDIDATE_FILE
    profile.LOCAL_CANDIDATE_FILE = tmp_path_factory.mktemp("home") / "candidate.local.json"
    profile.reset()
    try:
        yield
    finally:
        profile.LOCAL_CANDIDATE_FILE = real
        profile.reset()
