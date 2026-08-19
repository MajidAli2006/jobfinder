"""Pay, converted to one currency so figures from different markets compare.

Rates are deliberately static: a run must not depend on a live FX service, and
a salary floor does not need spot accuracy. An unknown currency returns None
rather than zero, so the caller treats the figure as unverified instead of
silently deciding the job pays nothing.
"""

from __future__ import annotations

from . import config, region


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


def currency_for(job, fallback_country: str = "") -> str:
    """What this job's pay is quoted in.

    Many adverts publish a figure and no currency. Reading those as dollars
    inflates every non-dollar market — a Gulf advert at 57,000 AED was being
    valued at $57,000 rather than $15,400, which is the difference between
    clearing a salary floor and falling far short of it. Where the advert says
    nothing, the country the work is in answers instead.
    """
    stated = (job.salary_currency or "").strip()
    if stated:
        return stated
    where = getattr(job, "location", "") or getattr(job, "location_raw", "")
    for part in str(where).split(","):
        country = region.match_country(part.strip())
        if country:
            return config.COUNTRY_TO_CURRENCY.get(country, "")
    return config.COUNTRY_TO_CURRENCY.get(fallback_country, "")


def annual_usd(job, fallback_country: str = "") -> tuple[float | None, str]:
    """Best available annual USD figure for a job, and how it was derived."""
    currency = currency_for(job, fallback_country)
    top = to_usd(job.salary_max or job.salary_min, currency)
    if top:
        return top, "salary"
    day_top = to_usd(job.day_rate_max or job.day_rate_min, currency)
    if day_top:
        return day_top * config.CONTRACT_DAYS_PER_YEAR, "day rate"
    return None, "none"

