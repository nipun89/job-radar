"""LinkedIn Connections.csv import and contact-to-company matching.

Export from LinkedIn: Settings > Data privacy > Get a copy of your data > Connections.
Upload it on the Contacts page (it is saved as data/Connections.csv so it can be re-imported).
"""
import csv
import io
import re

from radar import db

CSV_PATH = db.DATA_DIR / "Connections.csv"

COLUMNS = {
    "First Name": "first_name", "Last Name": "last_name", "URL": "url",
    "Email Address": "email", "Company": "company", "Position": "position",
    "Connected On": "connected_on",
}


def read_connections(path=CSV_PATH):
    """Read the export, skipping LinkedIn's 'Notes:' lines above the real header row."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        return parse_connections(f.read())


def parse_connections(text):
    """Same as read_connections, from the file's text (e.g. an upload)."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    start = next((i for i, r in enumerate(rows) if r and r[0].strip() == "First Name"), None)
    if start is None:
        raise ValueError("Couldn't find the 'First Name' header row in Connections.csv")
    header = [h.strip() for h in rows[start]]
    out = []
    for r in rows[start + 1:]:
        rec = dict(zip(header, r))
        c = {field: (rec.get(col) or "").strip() for col, field in COLUMNS.items()}
        if c["url"]:  # url is the primary key; skip rows without one
            out.append(c)
    return out


def import_connections(conn=None, path=CSV_PATH):
    """Upsert every connection. Keeps how_i_know and strength. Returns the count imported."""
    own = conn is None
    conn = conn or db.connect()
    contacts = read_connections(path)
    for c in contacts:
        db.upsert_contact(conn, c)
    conn.commit()
    if own:
        conn.close()
    return len(contacts)


def _has(text, name):
    return re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.IGNORECASE) is not None


def company_matches(contact_company, match_names, exclude_names=()):
    """Whole-word, case-insensitive. 'Amazon' matches 'Amazon Web Services (AWS)';
    'Block' does NOT match 'Blockchain Capital'. exclude_names knocks out
    look-alikes, e.g. 'Clark Square Capital' for Square."""
    text = contact_company or ""
    if any(_has(text, x) for x in exclude_names):
        return False
    return any(_has(text, m) for m in match_names)


_STRENGTH_RANK = {"Strong": 0, "Medium": 1, "Weak": 2}


def rank_key(c):
    """Strong > Medium > Weak > blank, then product people, then by name."""
    return (
        _STRENGTH_RANK.get(c.get("strength") or "", 3),
        0 if "product" in (c.get("position") or "").lower() else 1,
        f"{c.get('first_name', '')} {c.get('last_name', '')}".lower(),
    )


def contacts_by_company(conn, companies):
    """Return {company name: [contact dicts, ranked]} for every tracked company."""
    all_contacts = [dict(r) for r in conn.execute("SELECT * FROM contacts")]
    result = {}
    for co in companies:
        names = co.get("match") or [co["name"]]
        exclude = co.get("exclude") or []
        hits = [c for c in all_contacts if company_matches(c["company"], names, exclude)]
        # only_contacts: show just these people (by full name), e.g. one go-to person at a former employer
        only = {n.lower() for n in (co.get("only_contacts") or [])}
        if only:
            hits = [c for c in hits if f"{c['first_name']} {c['last_name']}".strip().lower() in only]
        result[co["name"]] = sorted(hits, key=rank_key)
    return result


def contacts_for_name(all_contacts, company_name, companies):
    """Contacts at any company by name, e.g. for a Pipeline role you added by hand.
    Uses that company's config.yaml match/exclude rules if it's tracked, else its name.
    Ignores only_contacts so you can pick anyone you know there."""
    entry = next((c for c in companies if c["name"].lower() == (company_name or "").lower()), None)
    names = (entry.get("match") if entry else None) or [company_name]
    exclude = (entry.get("exclude") if entry else None) or []
    hits = [c for c in all_contacts if company_matches(c["company"], names, exclude)]
    return sorted(hits, key=rank_key)


# Future hook: messages.csv from the full LinkedIn archive could suggest "how I know them"
# (message count, last message date). Not built yet.
def suggest_how_i_know(conn, messages_path):
    raise NotImplementedError("Planned: use messages.csv to suggest how_i_know")
