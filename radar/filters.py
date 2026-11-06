"""Title / location / fit rules. All settings come from config.yaml `preferences`.

Main entry point: evaluate(title, location, prefs) -> (keep: bool, fit, fit_reason, drop_reason)
"""
import re
from datetime import date, timedelta

US_STATE_CODES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
    "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC"
).split()

US_SIGNALS = [
    "United States", "USA", "US", "U.S.", "NYC", "New York", "San Francisco", "Seattle", "Bellevue",
    "Redmond", "Kirkland", "Los Angeles", "Chicago", "Boston", "Austin", "Denver", "Atlanta", "Miami",
    "Dallas", "Houston", "Washington DC", "Philadelphia", "Phoenix", "San Diego", "San Jose",
    "Palo Alto", "Mountain View", "Menlo Park", "Oakland", "Portland", "Salt Lake City", "Charlotte",
    "Nashville", "Minneapolis", "Detroit", "Pittsburgh", "Raleigh", "Columbus", "Richmond",
    "McLean", "Plano", "Wilmington", "Arlington", "Sunnyvale", "Jersey City",
]

# ", WA" / ", NY" style state codes, e.g. "Seattle, WA" or "US, WA, Seattle"
_STATE_RE = re.compile(r",\s*(" + "|".join(US_STATE_CODES) + r")\b")


def has_word(text, phrase):
    """Whole-word, case-insensitive match. 'engineer' does NOT match 'engineering'."""
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text, re.IGNORECASE) is not None


def words_found(text, phrases):
    return [p for p in phrases if has_word(text, p)]


def _segment_is_us(seg, non_us_words):
    if words_found(seg, non_us_words):
        return False  # e.g. "Bengaluru, IN" has a state-code look-alike but is India
    return bool(_STATE_RE.search(seg) or words_found(seg, US_SIGNALS))


def location_check(location, prefs):
    """Return (is_us, is_non_us). Locations are split on ';' so 'London, UK; New York, NY' counts as US."""
    loc = location or ""
    non_us_words = prefs.get("non_us_location_keywords", [])
    segments = [s.strip() for s in loc.split(";") if s.strip()] or [""]
    is_us = any(_segment_is_us(s, non_us_words) for s in segments)
    is_non_us = bool(words_found(loc, non_us_words))
    return is_us, is_non_us


NO_LEVEL = "no seniority keyword"


def evaluate(title, location, prefs, skip_seniority=False, pay_max=None):
    """Apply the title, location and fit rules to one posting.

    skip_seniority: for companies whose titles don't use Staff/Principal/Lead
    (e.g. AI labs post plain "Product Manager, X"). Set per company in config.yaml.
    pay_max: the highest annual pay the posting mentions, if known. A title with no
    seniority word still counts when this reaches min_pay_without_seniority: a plain
    "Product Manager" paying that much is often an opening levelled after interviews.
    """
    t = (title or "").lower()
    loc = location or ""

    # 1. Must look like a PM role. Seniority is checked last (step 5), so the pay
    #    safety catch is only tried on postings that pass everything else.
    if not any(k in t for k in prefs["role_keywords"]):
        return False, None, None, "not a PM role"
    levels = words_found(t, prefs["seniority_keywords"])

    # 2. Exclusions (whole word)
    excluded = words_found(t, prefs.get("title_exclude", []))
    if excluded:
        return False, None, None, f"excluded: {excluded[0]}"

    # 3. Location: drop clearly non-US postings. Empty/ambiguous ("Remote") is kept.
    is_us, is_non_us = location_check(loc, prefs)
    if prefs.get("us_only", True) and is_non_us and not is_us:
        return False, None, None, "non-US location"

    # 4. Preferred locations (only enforced if not open to relocation)
    preferred = words_found(loc, prefs.get("preferred_locations", []))
    if not prefs.get("open_to_relocation", True) and not preferred:
        return False, None, None, "not a preferred location"

    # 5. Senior enough: a seniority word, the company setting, or the pay safety catch
    min_pay = prefs.get("min_pay_without_seniority")
    if levels:
        level = max(levels, key=len)  # "senior staff" beats "staff"
    elif skip_seniority:
        level = "any (company setting)"
    elif words_found(t, prefs.get("lower_level_keywords", [])):
        # e.g. "Senior Product Manager": the title states a level, just below yours
        return False, None, None, "lower level: " + words_found(t, prefs["lower_level_keywords"])[0]
    elif min_pay and pay_max and pay_max >= min_pay:
        level = f"not in title, pays up to ${pay_max / 1000:.0f}K"
    else:
        return False, None, None, NO_LEVEL

    # 6. Fit
    domains = words_found(t, prefs.get("domain_keywords", []))
    fit = "High" if domains else "Medium"
    parts = []
    if domains:
        parts.append("Domain: " + ", ".join(domains))
    parts.append(f"Level: {level}")
    parts.append("Preferred location" if preferred else "Relocation")
    return True, fit, " · ".join(parts), None


def too_old(posted, prefs, today=None):
    """5. True if the posting is older than max_posting_age_days. Only applied when first discovered."""
    if not posted:
        return False
    today = today or date.today()
    try:
        posted_d = date.fromisoformat(posted[:10])
    except ValueError:
        return False
    return posted_d < today - timedelta(days=prefs.get("max_posting_age_days", 45))
