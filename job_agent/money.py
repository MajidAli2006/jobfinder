"""Pay, converted to one currency so figures from different markets compare.

Rates are deliberately static: a run must not depend on a live FX service, and
a salary floor does not need spot accuracy. An unknown currency returns None
rather than zero, so the caller treats the figure as unverified instead of
silently deciding the job pays nothing.
"""

from __future__ import annotations

from . import config

def to_usd(amount: float | None, currency: str) -> float | None:
    """Convert to USD using the static table. Unknown currencies return None so the caller
    can treat the figure as unverified rather than as zero.
    """
    if not amount:
        return None
    rate = config.CURRENCY_TO_USD.get((currency or "USD").upper())
    if rate is None:
        return None
    return amount * rate


def annual_usd(job) -> tuple[float | None, str]:
    """Best available annual USD figure for a job, and how it was derived."""
    top = to_usd(job.salary_max or job.salary_min, job.salary_currency)
    if top:
        return top, "salary"
    day_top = to_usd(job.day_rate_max or job.day_rate_min, job.salary_currency)
    if day_top:
        return day_top * config.CONTRACT_DAYS_PER_YEAR, "day rate"
    return None, "none"

