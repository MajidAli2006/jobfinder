"""Tests for pay conversion.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest

from job_agent import config, money
from job_agent.models import Job


class ConversionTests(unittest.TestCase):
    def test_the_base_currency_is_unchanged(self):
        self.assertAlmostEqual(money.to_usd(100_000, "USD"), 100_000)

    def test_stronger_and_weaker_currencies_move_the_right_way(self):
        self.assertGreater(money.to_usd(60_000, "GBP"), 70_000)
        self.assertLess(money.to_usd(2_000_000, "INR"), 30_000)

    def test_an_unknown_currency_is_unverified_not_zero(self):
        # Returning 0 would read as "this job pays nothing" and reject it.
        self.assertIsNone(money.to_usd(50_000, "XYZ"))

    def test_no_amount_converts_to_nothing(self):
        self.assertIsNone(money.to_usd(0, "USD"))
        self.assertIsNone(money.to_usd(None, "GBP"))

    def test_a_missing_currency_is_treated_as_usd(self):
        self.assertAlmostEqual(money.to_usd(1_000, ""), 1_000)

    def test_the_case_of_the_code_does_not_matter(self):
        self.assertEqual(money.to_usd(1_000, "gbp"), money.to_usd(1_000, "GBP"))


class MarketCoverageTests(unittest.TestCase):
    """Every market the tool searches must be priceable."""

    MARKETS = ("GBP", "USD", "EUR", "AED", "SAR", "QAR", "KWD", "BHD", "OMR",
               "PKR", "INR", "NGN", "GHS", "KES", "ZAR", "CAD", "AUD", "NZD",
               "SGD", "MYR", "PHP", "IDR", "VND", "THB", "BRL", "MXN", "PLN",
               "SEK", "NOK", "DKK", "CHF", "TRY", "EGP", "JPY", "HKD")

    def test_every_market_currency_converts(self):
        for code in self.MARKETS:
            with self.subTest(currency=code):
                self.assertIsNotNone(money.to_usd(1_000, code),
                                     f"{code} salaries cannot be priced")

    def test_gulf_currencies_are_worth_more_than_a_dollar(self):
        for code in ("KWD", "BHD", "OMR"):
            with self.subTest(currency=code):
                self.assertGreater(config.CURRENCY_TO_USD[code], 1.0)


class AnnualisingTests(unittest.TestCase):
    def job(self, **kwargs) -> Job:
        base = dict(title="Engineer", company="Acme", employment_type="Full Time")
        base.update(kwargs)
        return Job(**base)

    def test_a_salary_band_uses_its_top(self):
        value, basis = money.annual_usd(
            self.job(salary_min=50_000, salary_max=70_000, salary_currency="USD"))
        self.assertAlmostEqual(value, 70_000)
        self.assertEqual(basis, "salary")

    def test_a_day_rate_is_annualised(self):
        value, basis = money.annual_usd(
            self.job(employment_type="Contract", day_rate_max=500, salary_currency="USD"))
        self.assertEqual(basis, "day rate")
        self.assertAlmostEqual(value, 500 * config.CONTRACT_DAYS_PER_YEAR)

    def test_unpublished_pay_reports_none(self):
        value, basis = money.annual_usd(self.job())
        self.assertIsNone(value)
        self.assertEqual(basis, "none")


if __name__ == "__main__":
    unittest.main()
