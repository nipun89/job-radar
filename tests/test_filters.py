from datetime import date

import pytest

from radar.db import EXAMPLE_CONFIG_PATH, load_config
from radar.filters import evaluate, has_word, too_old

PREFS = load_config(EXAMPLE_CONFIG_PATH)["preferences"]   # the shipped defaults, not your own config.yaml

CASES = [
    ("Staff Product Manager, Capital", "San Francisco, CA; Seattle, WA", True, "High"),
    ("Senior Product Manager, Lending", "Seattle", False, None),
    ("Principal Product Manager - Tech, Amazon Business Lending", "US, WA, Seattle", True, "High"),
    ("Group Product Manager, Payments", "Remote - US", True, "High"),
    ("Staff Product Manager, Payments", "Dublin, Ireland", False, None),
    ("Lead Product Manager", "London, UK; New York, NY", True, "Medium"),
    ("Staff Product Designer", "Remote", False, None),
    ("Head of Product Marketing", "NYC", False, None),
    ("Principal Product Manager, Engineering Productivity", "Remote", True, "Medium"),
    ("Staff Engineer, Payments", "Seattle", False, None),
]


@pytest.mark.parametrize("title,location,keep,fit", CASES)
def test_guide_table(title, location, keep, fit):
    got_keep, got_fit, reason, drop = evaluate(title, location, PREFS)
    assert got_keep == keep, drop
    assert got_fit == fit


def test_whole_word():
    assert has_word("Staff Engineer", "engineer")
    assert not has_word("Engineering Productivity", "engineer")


def test_india_state_code_lookalike_dropped():
    keep, *_ = evaluate("Staff Product Manager", "Bengaluru, IN", PREFS)
    assert not keep


def test_fit_reason_format():
    _, _, reason, _ = evaluate("Staff Product Manager, Capital", "Seattle, WA", PREFS)
    assert reason == "Domain: capital · Level: staff · Preferred location"


def test_too_old():
    assert too_old("2026-01-01", PREFS, today=date(2026, 11, 3))
    assert not too_old("2026-10-30", PREFS, today=date(2026, 11, 3))
    assert not too_old(None, PREFS)


def test_amazon_pm_t_titles():
    keep, fit, *_ = evaluate("Principal PM-T, Amazon Lending", "Seattle, Washington, USA", PREFS)
    assert keep and fit == "High"
    keep, *_ = evaluate("Senior PM-T, Growth", "Seattle, Washington, USA", PREFS)
    assert not keep  # still needs a seniority keyword


def test_skip_seniority_for_ai_labs():
    keep, *_ = evaluate("Product Manager, Claude", "San Francisco, CA", PREFS)
    assert not keep  # normal rule: needs Staff/Principal/Lead
    keep, fit, reason, _ = evaluate("Product Manager, Claude", "San Francisco, CA", PREFS, skip_seniority=True)
    assert keep and "Level: any" in reason
    keep, *_ = evaluate("Product Marketing Manager", "San Francisco, CA", PREFS, skip_seniority=True)
    assert not keep  # exclusions still apply


def test_tracking_slugs():
    from radar.tracking import slug_candidates
    assert slug_candidates("Maven Clinic") == ["mavenclinic", "maven-clinic"]
    assert slug_candidates("Redfin") == ["redfin"]


def test_feed_from_saved_links():
    from radar.tracking import feed_from_urls
    assert feed_from_urls(["https://servicetitan.wd1.myworkdayjobs.com/ServiceTitan/job/US-Remote/x_JR1"]) == ("workday", "servicetitan|wd1|ServiceTitan")
    assert feed_from_urls(["https://wellfound.com/jobs/1-x"]) is None


def test_pay_safety_catch_for_titles_without_a_level():
    from radar.filters import NO_LEVEL
    p = {**PREFS, "min_pay_without_seniority": 250000}
    title, loc = "Product Manager, Financial Management", "San Francisco HQ; Remote"
    assert evaluate(title, loc, p)[3] == NO_LEVEL                     # pay unknown
    assert evaluate(title, loc, p, pay_max=226800)[3] == NO_LEVEL     # below the bar
    keep, fit, reason, _ = evaluate(title, loc, p, pay_max=262800)
    assert keep and fit == "High" and "pays up to $263K" in reason
    off = {k: v for k, v in p.items() if k != "min_pay_without_seniority"}
    assert evaluate(title, loc, off, pay_max=900000)[3] == NO_LEVEL    # catch is off without the setting
    assert evaluate("Product Manager", "London, UK", p, pay_max=300000)[3] == "non-US location"
    # A title that states a lower level never gets the catch
    assert evaluate("Senior Product Manager, Checkout", loc, p, pay_max=300000)[3] == "lower level: senior"
    assert evaluate("Sr. Product Manager, Loyalty", loc, p, pay_max=300000)[3] == "lower level: sr"
    assert evaluate("Senior Staff Product Manager", loc, p)[0]          # has a real level word: fine
