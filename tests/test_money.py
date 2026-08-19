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


class UnstatedCurrencyTests(unittest.TestCase):
    """An advert that gives a figure and no currency is not quoting dollars.

    A real Dubai run carried three Abu Dhabi adverts priced 57,000, 42,000-70,000
    and 18,808 with no currency. Read as USD, two of them cleared a $50,000
    floor; in dirhams they are worth about $15,400 and $18,900, so the search
    was showing work paying a third of what was asked for.
    """

    def job(self, **kwargs) -> Job:
        base = dict(title="Electrician", company="Acme",
                    location="Abu Dhabi - United Arab Emirates")
        base.update(kwargs)
        return Job(**base)

    def test_the_country_in_the_location_settles_the_currency(self):
        self.assertEqual(
            money.currency_for(self.job(salary_max=57_000), "United Arab Emirates"),
            "AED")

    def test_a_gulf_salary_no_longer_clears_a_dollar_floor(self):
        value, _basis = money.annual_usd(self.job(salary_min=57_000, salary_max=57_000),
                                         "United Arab Emirates")
        self.assertLess(value, 20_000)

    def test_a_stated_currency_always_wins(self):
        job = self.job(salary_max=50_000, salary_currency="GBP")
        self.assertEqual(money.currency_for(job, "United Arab Emirates"), "GBP")

    def test_the_search_country_settles_it_when_the_advert_does_not(self):
        job = self.job(location="", salary_max=900_000)
        self.assertEqual(money.currency_for(job, "Pakistan"), "PKR")

    def test_several_markets_read_sensibly(self):
        cases = [("Karachi, Pakistan", "Pakistan", "PKR"),
                 ("Tokyo, Japan", "Japan", "JPY"),
                 ("Lagos, Nigeria", "Nigeria", "NGN"),
                 ("Leeds, United Kingdom", "United Kingdom", "GBP"),
                 ("Toronto, Canada", "Canada", "CAD")]
        for location, country, expected in cases:
            with self.subTest(location=location):
                job = self.job(location=location, salary_max=1_000)
                self.assertEqual(money.currency_for(job, country), expected)

    def test_every_known_country_can_be_priced(self):
        from job_agent import region
        for country in region.COUNTRIES:
            with self.subTest(country=country):
                code = config.COUNTRY_TO_CURRENCY.get(country)
                self.assertTrue(code, f"{country} has no currency")
                self.assertIn(code, config.CURRENCY_TO_USD,
                              f"{country} is priced in {code}, which has no rate")

    def test_a_placeless_job_still_falls_back_to_dollars(self):
        """Remote boards quote USD and set it explicitly; this is the last resort."""
        job = self.job(location="Remote", salary_max=90_000)
        value, _ = money.annual_usd(job, "")
        self.assertAlmostEqual(value, 90_000)


class WorldwidePayParsingTests(unittest.TestCase):
    """Adverts quote pay in local units, per month, and in large denominations.

    Judged against a dollar-shaped band, ordinary salaries disappeared: a
    Japanese advert at 8,000,000 yen and an Indian one at 1,200,000 rupees both
    sit above a million and were reported as publishing no pay. Gulf and South
    Asian adverts quote a month's pay, which read as a year's understates by
    twelve and drops good work below a salary floor.
    """

    def parsed(self, text):
        from job_agent.utils import parse_compensation
        return parse_compensation(text)

    def test_a_large_denomination_salary_survives(self):
        cases = [("¥8,000,000 per year", "JPY", 8_000_000),
                 ("₹1,200,000 per annum", "INR", 1_200_000),
                 ("NGN 30,000,000 per annum", "NGN", 30_000_000),
                 ("IDR 250,000,000 per year", "IDR", 250_000_000)]
        for text, currency, expected in cases:
            with self.subTest(text=text):
                got = self.parsed(text)
                self.assertEqual(got["currency"], currency)
                self.assertEqual(got["salary_max"], expected)

    def test_a_month_of_pay_is_annualised(self):
        cases = [("AED 8,000 - 12,000 per month", 96_000, 144_000),
                 ("PKR 150,000/month", 1_800_000, 1_800_000),
                 ("SAR 9,000 monthly", 108_000, 108_000)]
        for text, low, high in cases:
            with self.subTest(text=text):
                got = self.parsed(text)
                self.assertEqual((got["salary_min"], got["salary_max"]), (low, high))

    def test_a_low_paying_market_is_not_judged_by_dollars(self):
        """A floor set in dollars assumed nobody earns under $12,000 a year."""
        got = self.parsed("PKR 150,000/month")
        self.assertIsNotNone(got["salary_max"])

    def test_a_brazilian_real_is_not_a_dollar(self):
        from job_agent.utils import detect_currency
        self.assertEqual(detect_currency("R$ 5.000 por mês"), "BRL")

    def test_every_priced_currency_can_be_named_in_an_advert(self):
        from job_agent.utils import detect_currency
        for code in config.CURRENCY_TO_USD:
            with self.subTest(code=code):
                self.assertEqual(detect_currency(f"Salary 50,000 {code} per annum"), code)

    def test_western_parsing_is_unchanged(self):
        cases = [("£45,000 - £55,000 per annum", "GBP", 45_000, 55_000),
                 ("$120k-$150k", "USD", 120_000, 150_000)]
        for text, currency, low, high in cases:
            with self.subTest(text=text):
                got = self.parsed(text)
                self.assertEqual((got["currency"], got["salary_min"], got["salary_max"]),
                                 (currency, low, high))

    def test_a_day_rate_is_still_a_day_rate(self):
        got = self.parsed("£500 per day")
        self.assertEqual(got["day_rate_max"], 500)
        self.assertIsNone(got["salary_max"])

    def test_a_year_is_not_a_salary(self):
        self.assertIsNone(self.parsed("2024")["salary_max"])
