"""Sample data for demo mode (python -m radar demo).

Everything here is made up: the people, the notes and the job links. The company
names match config.example.yaml so the Companies page lines up. Demo data lives in
data/demo/, never in your real database.
"""
from datetime import date, timedelta

from radar import db, pipeline


def _ago(days):
    return (date.today() - timedelta(days=days)).isoformat()


# (company, title, location, posted days ago, found days ago, fit, reason)
JOBS = [
    ("Stripe", "Staff Product Manager, Capital", "Seattle, WA; San Francisco, CA", 2, 0, "High", "Level: staff · Preferred location"),
    ("Ramp", "Principal Product Manager, Bill Pay", "New York, NY", 1, 0, "High", "Level: principal · Relocation"),
    ("Zillow", "Principal Product Manager, Rental Listings", "Remote-USA", 3, 0, "Medium", "Level: principal · Preferred location"),
    ("Expedia", "Principal Product Manager", "Washington - Seattle Campus", 4, 1, "Medium", "Level: principal · Preferred location"),
    ("Capital One", "Director, Product Management - Card Acquisition", "McLean, VA", 5, 1, "High", "Level: director · Relocation"),
    ("Plaid", "Group Product Manager, Payments", "Remote - US", 6, 1, "High", "Level: group · Preferred location"),
    ("Anthropic", "Product Manager, Enterprise", "San Francisco, CA | Seattle, WA", 2, 2, "Medium", "Any level · Preferred location"),
    ("Affirm", "Staff Product Manager, Underwriting", "Remote US", 8, 3, "High", "Level: staff · Preferred location"),
    ("Brex", "Lead Product Manager, Expense Management", "San Francisco, CA", 9, 4, "Medium", "Level: lead · Relocation"),
    ("Coinbase", "Senior Staff Product Manager, Institutional", "Remote - USA", 11, 5, "Medium", "Level: senior staff · Preferred location"),
    ("Chime", "Principal Product Manager, Credit Builder", "San Francisco, CA", 12, 6, "High", "Level: principal · Relocation"),
    ("Gusto", "Group Product Manager, Payroll", "Denver, CO", 14, 8, "Medium", "Level: group · Relocation"),
    ("OpenAI", "Product Manager, API Platform", "San Francisco, CA", 15, 9, "Medium", "Any level · Relocation"),
    ("Robinhood", "Staff Product Manager, Retirement", "Menlo Park, CA", 20, 12, "Medium", "Level: staff · Relocation"),
    ("Block", "Lead Product Manager, Cash App Lending", "Remote", 30, 20, "High", "Level: lead · Preferred location"),
]

# (first, last, LinkedIn employer, position, how I know, strength)
CONTACTS = [
    ("Ana", "Lee", "Stripe", "Engineering Manager", "Worked together on billing, 2021-23", "Strong"),
    ("Dev", "Shah", "Stripe", "Product Lead", "", ""),
    ("Maya", "Chen", "Ramp", "Head of Product, Payables", "Same MBA cohort", "Medium"),
    ("Jordan", "Park", "Zillow Group", "Principal PM", "Met at a fintech meetup", "Weak"),
    ("Priya", "Nair", "Expedia Group", "Director of Product", "Former manager", "Strong"),
    ("Luis", "Ortega", "Capital One", "Senior Director, Product", "", ""),
    ("Sam", "Rivera", "Affirm", "Staff Software Engineer", "College friend", "Strong"),
    ("Grace", "Kim", "Plaid", "Product Manager", "Interviewed me in 2022", "Weak"),
    ("Tom", "Becker", "Square", "Product Lead, Lending", "Worked together at a startup", "Medium"),
    ("Nina", "Patel", "Anthropic", "Recruiter", "", ""),
]


def seed(conn):
    """Fill an empty demo database. Does nothing if it already has jobs."""
    if conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]:
        return False
    keys = {}
    for i, (co, title, loc, posted, found, fit, reason) in enumerate(JOBS):
        key = f"{co}|demo-{i}"
        keys[co] = key
        db.insert_job_if_new(conn, {
            "job_key": key, "company": co, "title": title, "location": loc,
            "url": f"https://example.com/jobs/{co.lower().replace(' ', '-')}/{i}",
            "date_posted": _ago(posted), "date_found": _ago(found), "fit": fit, "fit_reason": reason,
        })
    # One role that has since come down
    conn.execute("UPDATE jobs SET posting_state='Closed', closed_on=? WHERE job_key=?", (_ago(2), keys["Robinhood"]))

    for i, (first, last, employer, position, how, strength) in enumerate(CONTACTS):
        url = f"https://www.linkedin.com/in/demo-{first.lower()}-{last.lower()}"
        db.upsert_contact(conn, {"url": url, "first_name": first, "last_name": last, "company": employer,
                                 "position": position, "connected_on": _ago(400 + 90 * i), "email": ""})
        db.update_contact(conn, url, how, strength)

    # Pipeline: a spread of stages, backdated so the "last 7 days" counts have something to show
    p = pipeline.add_from_job(conn, keys["Affirm"])
    pipeline.update_item(conn, p, "Applied", "Referred", "Sam Rivera", _ago(2), "Sam submitted the referral on Monday.")
    p = pipeline.add_from_job(conn, keys["Plaid"])
    pipeline.update_item(conn, p, "Interested", "Reached out", "Grace Kim", None, "Asked Grace about the team.")
    pipeline.add_from_job(conn, keys["Capital One"])
    pipeline.add_manual(conn, "Expedia", "Director of Product, Loyalty",
                        stage="Interviewing", referral="Referred", contact="Priya Nair", date_applied=_ago(16),
                        notes="Hiring manager screen done. Panel next week.", at=_ago(18))
    pipeline.add_manual(conn, "Brex", "Lead Product Manager, Card", stage="Rejected", date_applied=_ago(25),
                        notes="Closed after the recruiter screen.", at=_ago(26))
    pipeline.add_manual(conn, "Northwind Lending", "Head of Product", url="https://example.com/northwind",
                        stage="Applied", date_applied=_ago(5), notes="Found via a newsletter.", at=_ago(5))

    conn.execute("INSERT INTO runs (run_at, companies_checked, postings_scanned, new_matches, errors) VALUES (?,?,?,?,?)",
                 (_ago(0) + "T07:00:12", 16, 1874, 3, None))
    conn.commit()
    return True
