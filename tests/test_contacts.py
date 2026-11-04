from radar import db
from radar.contacts import company_matches, contacts_by_company, import_connections

FAKE_CSV = """Notes:
"When exporting your connection data, you may notice that some of the email addresses are missing."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Ana,Lee,https://www.linkedin.com/in/ana,,Stripe,Engineering Manager,01 Jan 2020
Ben,Ortiz,https://www.linkedin.com/in/ben,,Amazon Web Services (AWS),Principal Product Manager,02 Feb 2021
Cara,Ng,https://www.linkedin.com/in/cara,,Blockchain Capital,Partner,03 Mar 2022
Dev,Shah,https://www.linkedin.com/in/dev,,Stripe,Product Lead,04 Apr 2023
Eli,Moss,https://www.linkedin.com/in/eli,,Square,Designer,05 May 2024
"""

COMPANIES = [
    {"name": "Stripe", "match": ["Stripe"]},
    {"name": "Amazon", "match": ["Amazon", "Amazon Web Services", "AWS"]},
    {"name": "Block", "match": ["Block", "Square", "Cash App", "Afterpay"]},
]


def test_matching_rules():
    assert company_matches("Amazon Web Services (AWS)", ["Amazon"])
    assert not company_matches("Blockchain Capital", ["Block"])
    assert not company_matches("Clark Square Capital", ["Square"], ["Clark Square Capital"])
    assert company_matches("Square", ["Square"], ["Clark Square Capital"])


def test_import_and_reimport_keeps_my_columns(tmp_path):
    csv_path = tmp_path / "Connections.csv"
    csv_path.write_text(FAKE_CSV, encoding="utf-8")
    conn = db.connect(tmp_path / "t.db")

    assert import_connections(conn, csv_path) == 5
    db.update_contact(conn, "https://www.linkedin.com/in/ana", "Worked together at Stripe Capital", "Strong")

    # Re-import with Ana's position changed: position updates, my columns survive
    csv_path.write_text(FAKE_CSV.replace("Engineering Manager", "Director"), encoding="utf-8")
    import_connections(conn, csv_path)
    ana = dict(conn.execute("SELECT * FROM contacts WHERE url LIKE '%ana'").fetchone())
    assert ana["position"] == "Director"
    assert ana["how_i_know"] == "Worked together at Stripe Capital"
    assert ana["strength"] == "Strong"

    by_co = contacts_by_company(conn, COMPANIES)
    # Ana (Strong) ranks above Dev (product, blank strength)
    assert [c["first_name"] for c in by_co["Stripe"]] == ["Ana", "Dev"]
    assert [c["first_name"] for c in by_co["Amazon"]] == ["Ben"]
    assert [c["first_name"] for c in by_co["Block"]] == ["Eli"]  # not Cara (Blockchain)


def test_only_contacts(tmp_path):
    csv_path = tmp_path / "Connections.csv"
    csv_path.write_text(FAKE_CSV, encoding="utf-8")
    conn = db.connect(tmp_path / "t.db")
    import_connections(conn, csv_path)
    by_co = contacts_by_company(conn, [{"name": "Stripe", "match": ["Stripe"], "only_contacts": ["dev shah"]}])
    assert [c["first_name"] for c in by_co["Stripe"]] == ["Dev"]
