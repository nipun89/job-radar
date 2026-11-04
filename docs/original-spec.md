> **About this file.** This is the original spec Job Radar was built from, handed to Claude Code in one go
> (lightly edited to remove personal details). The project has moved on since: Streamlit became a plain
> Flask + HTML app, the single status column became a separate Pipeline, and scheduling is now
> cross-platform (`python -m radar schedule`). It's kept as a record of how the project started.

#### Job Radar: build guide for Claude Code

Hand this whole file to Claude Code and say: "Build this in ~/job-radar, then walk me through first run." Everything Claude Code needs is here.

---

#### What we're building

A local job-search tracker for a senior product manager in fintech (lending and payments).

1. **Daily fetch.** A Python script pulls open roles from company careers feeds (public JSON APIs, not HTML scraping), filters them to roles that fit, and stores new ones. It runs once a day automatically and on demand.
2. **Contacts.** It matches each role's company against the user's LinkedIn `Connections.csv` export, so every role shows who the user knows there and how.
3. **Local dashboard.** A browser page on localhost where the user sees and filters roles, sets a status per role, adds notes, and records how they know each contact.

No Google Sheets, no cloud services, no accounts. Everything lives in one folder on the user's machine.

---

#### Stack (keep it simple)

- Python 3.11+, one virtualenv at `~/job-radar/.venv`
- `requests` and `pyyaml` for fetching and config
- SQLite (`jobs.db`, standard library `sqlite3`) for storage
- **Streamlit** for the dashboard. `st.data_editor` gives an editable table with a status dropdown and almost no front-end code.
- macOS `launchd` for the daily run. If the machine is Windows, use Task Scheduler instead; check with `uname` first.

The user considers UX their weakest area and codes "web 1.0" (HTML and CSS). So prefer clear, boring code with comments over cleverness, and explain each step as you go.

---

#### Folder layout

```
~/job-radar/
  config.yaml          # preferences + company list (the user edits this)
  radar/
    fetchers.py        # one function per careers system
    filters.py         # title / location / fit rules
    contacts.py        # Connections.csv import + matching
    db.py              # SQLite schema + helpers
    run.py             # the daily job: fetch → filter → upsert → log
  dashboard.py         # Streamlit app
  data/
    jobs.db
    Connections.csv    # the LinkedIn export goes here
  logs/
    run.log
  README.md            # short "how to use"
```

---

#### Data model (SQLite)

**jobs**
- `job_key` TEXT PRIMARY KEY: `"{company}|{source_id}"`, the dedupe key
- `company`, `title`, `location`, `url`
- `date_posted` (date or null), `date_found` (date)
- `fit` ("High" / "Medium") and `fit_reason` (e.g. "Domain: lending · Level: principal · Preferred location")
- `status`: one of `New`, `Reached out to contact`, `Scheduled with contact`, `Applied`, `Live`, `Rejected`, `Ruled out`. Default `New`. "Live" means the application is active and in process.
- `status_updated` (date), `notes` (text)
- `posting_state` ("Open" / "Closed") and `closed_on` (date)

**contacts**
- `url` TEXT PRIMARY KEY (LinkedIn profile URL from the export)
- `first_name`, `last_name`, `company`, `position`, `connected_on`, `email`
- `how_i_know` (text, the user fills it in) and `strength` ("Strong" / "Medium" / "Weak", the user fills it in)

**runs**
- `run_at`, `companies_checked`, `postings_scanned`, `new_matches`, `errors`

Golden rule: **the daily run never overwrites `status`, `notes`, `status_updated`, `how_i_know` or `strength`.** Those belong to the user. The run only inserts new jobs, refreshes `posting_state`, and upserts contact rows without touching the user's two columns.

---

#### config.yaml (seed it with this)

```yaml
preferences:
  role_keywords: [product manager, product management, product lead, head of product, director of product, product director, group product]
  seniority_keywords: [staff, principal, lead, group, director, head of, senior staff]   # title must contain one
  title_exclude: [associate, intern, product marketing, marketing manager, designer, engineer, analyst, product operations, recruiter, counsel, data scientist]
  domain_keywords: [lending, loan, credit, capital, underwriting, risk, payments, payment, card, issuing, banking, treasury, embedded, financing, financial, money movement, billing, fraud]   # any hit in title → High fit
  preferred_locations: [Seattle, Bellevue, Redmond, Kirkland, Remote]
  open_to_relocation: true      # true = any US city; false = preferred_locations only
  us_only: true
  non_us_location_keywords: [London, Dublin, Ireland, United Kingdom, UK, Toronto, Vancouver, Canada, Singapore, Sydney, Melbourne, Australia, Hong Kong, Shanghai, Beijing, China, Bengaluru, Bangalore, Hyderabad, India, Tokyo, Japan, Paris, Berlin, Amsterdam, Netherlands, Germany, France, Spain, Madrid, Barcelona, Lisbon, Portugal, Mexico, São Paulo, Brazil, Bogota, Colombia, Tel Aviv, Israel, Dubai, Warsaw, Poland, Lithuania, Vilnius, Krakow, Stockholm, Zurich, Manila, Philippines, "Remote - EMEA", "Remote - APAC", "Remote - Canada"]
  max_posting_age_days: 45
  workday_search_text: product manager

companies:
  # ats: greenhouse | ashby | lever | workday | amazon | manual
  - {name: Stripe,      ats: greenhouse, board: stripe,    match: [Stripe]}
  - {name: Brex,        ats: greenhouse, board: brex,      match: [Brex]}
  - {name: Affirm,      ats: greenhouse, board: affirm,    match: [Affirm]}
  - {name: Chime,       ats: greenhouse, board: chime,     match: [Chime]}
  - {name: Mercury,     ats: greenhouse, board: mercury,   match: [Mercury]}
  - {name: Coinbase,    ats: greenhouse, board: coinbase,  match: [Coinbase]}
  - {name: Robinhood,   ats: greenhouse, board: robinhood, match: [Robinhood]}
  - {name: Carta,       ats: greenhouse, board: carta,     match: [Carta]}
  - {name: Gusto,       ats: greenhouse, board: gusto,     match: [Gusto]}
  - {name: Toast,       ats: greenhouse, board: toast,     match: [Toast]}
  - {name: Upstart,     ats: greenhouse, board: upstart,   match: [Upstart]}
  - {name: Block,       ats: greenhouse, board: block,     match: [Block, Square, Cash App, Afterpay]}
  - {name: Ramp,        ats: ashby,      board: ramp,      match: [Ramp]}
  - {name: Plaid,       ats: ashby,      board: plaid,     match: [Plaid]}
  - {name: Parafin,     ats: ashby,      board: parafin,   match: [Parafin]}
  - {name: Airwallex,   ats: ashby,      board: airwallex, match: [Airwallex]}
  - {name: Amazon,      ats: amazon,     board: [principal product manager payments, principal product manager lending, principal product manager fintech, product manager lending], match: [Amazon, Amazon Web Services, AWS]}
  - {name: Capital One, ats: workday,    board: capitalone|wd12|Capital_One, match: [Capital One]}   # UNVERIFIED, test first
  - {name: Atlassian,   ats: manual,     url: https://www.atlassian.com/company/careers/all-jobs, match: [Atlassian]}
  - {name: GitHub,      ats: manual,     url: https://www.github.careers, match: [GitHub, Microsoft]}
  - {name: Revolut,     ats: manual,     url: https://www.revolut.com/careers, match: [Revolut]}
```

The Greenhouse and Ashby feeds above were confirmed to return JSON in early November 2026. Lever's feed for Plaid failed (Plaid is on Ashby now). Capital One's Workday address is a best guess. `manual` rows aren't fetched; the dashboard just shows them as links to check by hand.

---

#### Fetchers (radar/fetchers.py)

Each fetcher returns a list of dicts: `{id, title, location, url, posted}`. Use a 20-second timeout, a plain User-Agent like `job-radar/1.0`, and wrap each company in try/except so one failure never stops the run.

- **greenhouse:** `GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs`
  - `jobs[]`: `id`, `title`, `location.name`, `absolute_url`, `first_published` (ISO; fall back to `updated_at`)
- **ashby:** `GET https://api.ashbyhq.com/posting-api/job-board/{board}`
  - `jobs[]`: `id`, `title`, `location`, `secondaryLocations[].location`, `isRemote`, `isListed`, `jobUrl`, `publishedAt`
  - Skip `isListed == false`. Join primary and secondary locations; add "Remote" if `isRemote`. The payload is big (full descriptions), which is fine.
- **lever:** `GET https://api.lever.co/v0/postings/{board}?mode=json`
  - Array of postings: `id`, `text` (title), `categories.location` / `categories.allLocations`, `hostedUrl`, `createdAt` (epoch ms)
- **workday:** board `tenant|wdN|site` → `POST https://{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs`
  - JSON body `{"appliedFacets":{}, "limit":20, "offset":N, "searchText": workday_search_text}`. Page by 20 up to 200.
  - `jobPostings[]`: `title`, `externalPath`, `locationsText`, `postedOn` ("Posted Today", "Posted Yesterday", "Posted 3 Days Ago", "Posted 30+ Days Ago"; convert to a date), `bulletFields[0]` (req ID, use as `id`)
  - URL = `https://{tenant}.{wdN}.myworkdayjobs.com/{site}{externalPath}`
- **amazon:** board is a list of search phrases; run each and dedupe by ID.
  - `GET https://www.amazon.jobs/en/search.json?result_limit=100&sort=recent&base_query={phrase}`
  - `jobs[]`: `id_icims`, `title`, `normalized_location` / `location`, `job_path` (prefix `https://www.amazon.jobs`), `posted_date` (like `"April  8, 2026"`; collapse double spaces before parsing)

**Fallback scraping (phase 2, only if needed):** if a company has no usable feed, add an `html` type that loads its careers page with Playwright and pulls job links and titles with a CSS selector set per company in config. Don't build this until a real company needs it. **Never scrape LinkedIn**: it violates their terms and risks the user's account.

---

#### Filters (radar/filters.py)

For each posting, with `t = title.lower()`:

1. Keep only if `t` contains any `role_keywords` **and** any `seniority_keywords`.
2. Drop if any `title_exclude` keyword appears as a whole word ("engineer" must not match "engineering").
3. Location: `is_us` = location matches United States / USA / US / ", WA"-style state codes / major US cities. `is_non_us` = any `non_us_location_keywords` as a whole word. If `us_only` and `is_non_us` and not `is_us`, drop it. An empty or ambiguous location (e.g. just "Remote") is kept.
4. If not `open_to_relocation`, keep only locations containing a `preferred_locations` entry.
5. Skip postings older than `max_posting_age_days`, but only when first discovered.
6. Fit: **High** if any `domain_keywords` whole-word hit in the title, else **Medium**. `fit_reason` = "Domain: x, y · Level: staff · Preferred location / Relocation".

Write unit tests for these (pytest). Expected results:

| Title | Location | Expected |
|---|---|---|
| Staff Product Manager, Capital | San Francisco, CA; Seattle, WA | keep, High |
| Senior Product Manager, Lending | Seattle | drop (no seniority keyword) |
| Principal Product Manager - Tech, Amazon Business Lending | US, WA, Seattle | keep, High |
| Group Product Manager, Payments | Remote - US | keep, High |
| Staff Product Manager, Payments | Dublin, Ireland | drop (non-US) |
| Lead Product Manager | London, UK; New York, NY | keep, Medium (has a US city) |
| Staff Product Designer | Remote | drop |
| Head of Product Marketing | NYC | drop |
| Principal Product Manager, Engineering Productivity | Remote | keep, Medium |
| Staff Engineer, Payments | Seattle | drop |

---

#### Contacts (radar/contacts.py)

- LinkedIn's `Connections.csv` has a few "Notes:" lines **above** the real header. Find the row whose first cell is `First Name` and read from there. Columns: First Name, Last Name, URL, Email Address, Company, Position, Connected On.
- Import with an upsert keyed on `url`. Update company, position and so on, but **keep** `how_i_know` and `strength`.
- Match a contact to a tracked company when any of that company's `match` names appears in the contact's `Company` as a whole word, case-insensitive. "Amazon" matches "Amazon Web Services (AWS)"; "Block" must not match "Blockchain Capital".
- Rank contacts per company: Strong > Medium > Weak > blank, then people whose position mentions "product", then by name.
- Later the user may also provide `messages.csv` from the full LinkedIn archive. Leave a hook for a future "suggest how I know them" step (message count, last message date), but don't build it now.

---

#### Daily run (radar/run.py)

1. Load config, open the DB.
2. For each non-manual company, fetch, then filter. Insert jobs whose `job_key` is new, with status `New` and `date_found` = today.
3. Posting state: for each company **fetched successfully this run**, mark tracked jobs missing from its feed as `Closed` (set `closed_on` once), and mark any reappearing ones `Open`. Never mark jobs closed for a company whose fetch errored.
4. Write a `runs` row and append a line to `logs/run.log`.
5. Print a short summary (new roles per company). No email.
6. It must be safe to run many times a day: idempotent, with no duplicates.

Entry point: `python -m radar.run`

---

#### Dashboard (dashboard.py, Streamlit)

Run with `streamlit run dashboard.py` and open http://localhost:8501.

**Jobs page (default)**
- Filters along the top: Status (multi-select, default everything except Ruled out and Rejected), Fit, Company, Posting (Open/Closed), and a text search.
- Table via `st.data_editor`, newest first. Columns: Posted, Found, Company, Role, Location, Fit, Why, # Contacts, Top contacts (first 3, with "how I know" in brackets), Status (SelectboxColumn), Notes (editable), Posting, Link (LinkColumn, "Open").
- When Status changes, save it immediately and stamp `status_updated`.
- Clicking or expanding a row shows all contacts at that company with LinkedIn links.
- A **"Run check now"** button calls the same function as `run.py` and shows the summary.

**Contacts page**
- Searchable table of contacts at tracked companies only (not all 1,000), with editable `how_i_know` and `strength`.
- An **Import Connections.csv** button that re-reads `data/Connections.csv`.

**Companies page**
- Read-only view of `config.yaml` companies with last result per company (OK + count, or the error), plus links for `manual` rows.
- Tell the user to edit `config.yaml` to add companies. Keep it simple: no in-app config editing for v1.

**Pipeline strip** at the top: counts per status (New / Reached out / Scheduled / Applied / Live).

---

#### Scheduling (macOS)

- Create `~/Library/LaunchAgents/com.jobradar.daily.plist` that runs `~/job-radar/.venv/bin/python -m radar.run` at 7:00am daily (`StartCalendarInterval`), with `WorkingDirectory` set to `~/job-radar` and output going to `logs/`.
- Load it with `launchctl load`, test it with `launchctl start com.jobradar.daily`, and confirm `run.log` updated.
- Note for the user: launchd runs a missed job when the Mac wakes from sleep, but not if it was fully shut down at 7am. The "Run check now" button covers that.
- Optional: a `Job Radar.command` file on the Desktop (or a shell alias `jobradar`) that starts the dashboard and opens the browser.

---

#### Build order for Claude Code

1. Check the OS, Python version and network access to the four feed hosts (one `curl -I` each).
2. Scaffold the folder and venv; install `requests pyyaml streamlit pandas pytest`.
3. `db.py` with the schema, then the fetchers. Test each against one real company and print counts. Report any that fail; Capital One's Workday is the most likely.
4. `filters.py` plus the pytest table above. All tests must pass.
5. `run.py`. Run it twice and confirm the second run adds 0 duplicates.
6. `contacts.py`. If `data/Connections.csv` isn't there yet, create a 5-row fake one to test with, then delete it.
7. `dashboard.py`. Launch it and walk the user through changing a status and confirming it persists after a refresh and after another run.
8. launchd job, then the README.
9. Finish with a short summary: what ran, how many roles were found, which companies errored, and the three commands the user needs (start dashboard, run now, view log).

#### Acceptance checklist

- [ ] `python -m radar.run` completes with every company showing OK or a clear error
- [ ] Re-running adds no duplicates and doesn't change any status or notes
- [ ] Filter tests pass
- [ ] Dashboard loads; status and notes edits persist
- [ ] Contacts appear on matching roles after importing Connections.csv; `how_i_know` and `strength` survive a re-import
- [ ] Daily launchd job fires (verified with `launchctl start`)
- [ ] No email, no cloud services, no LinkedIn scraping
