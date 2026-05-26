"""
Daily LinkedIn Job Alert Processor
===================================
Reads LinkedIn job alert emails from Gmail, filters for London-area
remote/hybrid roles, generates tailored CVs using the Anthropic API,
converts them to PDF, and sends an email digest via Resend.

SETUP (one-time):
  1. Anthropic API key:
       export ANTHROPIC_API_KEY=sk-ant-...
     Or add ANTHROPIC_API_KEY=... to .env

  2. Gmail OAuth credentials:
       pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client
       Download credentials.json from Google Cloud Console (Gmail API enabled)
       Run once with: python3 scripts/daily_job_alerts.py --setup-gmail
       This opens a browser, you authorise, and token.json is saved.

  3. Resend API key (already configured in .env / send_email.py):
       Must have NO IP address restrictions. Edit at https://resend.com/api-keys

  4. WeasyPrint (for PDF):
       pip install weasyprint  (already installed)

USAGE:
  python3 scripts/daily_job_alerts.py           # process today's alerts
  python3 scripts/daily_job_alerts.py --days 2  # look back 2 days
  python3 scripts/daily_job_alerts.py --dry-run # parse + filter only, no CVs/email

WHEN RUNNING INSIDE A CLAUDE CODE SESSION:
  You can also just tell Claude: "process today's LinkedIn job alerts"
  and Claude will use the Gmail MCP server (already connected) to read emails,
  generate CVs directly, and run the PDF/email steps via this script.
"""

import os
import re
import sys
import json
import html as html_mod
import argparse
import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent.parent
CV_BASE_DIR = BASE_DIR / "cvs"
GMAIL_TOKEN_PATH = BASE_DIR / ".gmail_token.json"
GMAIL_CREDENTIALS_PATH = BASE_DIR / ".gmail_credentials.json"
ENV_PATH = BASE_DIR / ".env"

# Filtering rules
REQUIRED_LOCATIONS = [
    "london", "london area", "greater london", "city of london",
    "united kingdom", "england",          # UK-wide remote is fine
]
ACCEPTED_WORK_TYPES = ["remote", "hybrid"]
EXCLUDED_WORK_TYPES = ["on-site", "onsite", "on site"]
# If work type is unknown AND location is London-specific, include tentatively
LONDON_SPECIFIC = ["london", "greater london", "london area", "city of london"]


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------
def load_env():
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


# ---------------------------------------------------------------------------
# Gmail reader  (standalone, requires OAuth credentials)
# ---------------------------------------------------------------------------
def setup_gmail_oauth():
    """Interactive one-time OAuth flow. Saves token.json for future runs."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Install: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")
        sys.exit(1)

    if not GMAIL_CREDENTIALS_PATH.exists():
        print(f"Place your Google OAuth credentials file at: {GMAIL_CREDENTIALS_PATH}")
        print("Get it from https://console.cloud.google.com → APIs & Services → Credentials")
        sys.exit(1)

    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
    flow = InstalledAppFlow.from_client_secrets_file(str(GMAIL_CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0)
    GMAIL_TOKEN_PATH.write_text(creds.to_json())
    print(f"Gmail token saved to {GMAIL_TOKEN_PATH}")


def get_gmail_service():
    """Return an authenticated Gmail API service object."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        print("Install: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")
        sys.exit(1)

    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
    creds = None
    if GMAIL_TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(GMAIL_TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            print("No valid Gmail token. Run: python3 scripts/daily_job_alerts.py --setup-gmail")
            sys.exit(1)
        GMAIL_TOKEN_PATH.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def fetch_linkedin_alerts_gmail(days: int = 1) -> list[dict]:
    """
    Fetch LinkedIn job alert emails via Gmail API.
    Returns list of raw message dicts (id, subject, htmlBody).
    """
    import base64
    from email import message_from_bytes

    service = get_gmail_service()
    after_date = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y/%m/%d")
    query = f"from:jobalerts-noreply@linkedin.com after:{after_date}"

    result = service.users().messages().list(userId="me", q=query, maxResults=50).execute()
    messages = result.get("messages", [])

    raw_messages = []
    for msg in messages:
        detail = service.users().messages().get(
            userId="me", msgId=msg["id"], format="full"
        ).execute()

        subject = ""
        html_body = ""

        headers = detail.get("payload", {}).get("headers", [])
        for h in headers:
            if h["name"].lower() == "subject":
                subject = h["value"]
                break

        def extract_html(payload):
            if payload.get("mimeType") == "text/html":
                data = payload.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            for part in payload.get("parts", []):
                result = extract_html(part)
                if result:
                    return result
            return ""

        html_body = extract_html(detail.get("payload", {}))

        raw_messages.append({
            "id": msg["id"],
            "subject": subject,
            "htmlBody": html_body,
        })

    return raw_messages


# ---------------------------------------------------------------------------
# Job extraction from LinkedIn email HTML
# ---------------------------------------------------------------------------
def strip_tags(s: str) -> str:
    return html_mod.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def extract_jobs_from_email(html_body: str, subject: str) -> list[dict]:
    """
    Parse LinkedIn job alert email HTML to extract individual job listings.
    LinkedIn emails encode jobs as:
      <a class="font-bold ..." href="linkedin.com/jobs/view/...">Job Title</a>
      <p ...>Company &middot; Location (WorkType)</p>
    """
    jobs = []
    a_pattern = re.compile(
        r'<a\s([^>]*class="[^"]*font-bold[^"]*"[^>]*)>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )

    for m_a in a_pattern.finditer(html_body):
        attrs = m_a.group(1)
        title = strip_tags(m_a.group(2))
        if not title or len(title) < 5 or len(title) > 100:
            continue
        if any(s in title.lower() for s in ["see all", "apply", "linkedin", "manage", "get the"]):
            continue

        # Extract and clean the LinkedIn job URL from the href attribute
        href_m = re.search(r'href="([^"]+)"', attrs)
        raw_url = html_mod.unescape(href_m.group(1)) if href_m else ""
        # Normalise to a clean linkedin.com/jobs/view/{id}/ URL
        job_id_m = re.search(r'/jobs/view/(\d+)', raw_url)
        link = f"https://www.linkedin.com/jobs/view/{job_id_m.group(1)}/" if job_id_m else raw_url

        rest = html_body[m_a.end(): m_a.end() + 1500]

        p_match = re.search(r"<p[^>]*>\s*([^<]+(?:&middot;|·)[^<]+)\s*</p>", rest)
        if not p_match:
            continue

        comp_loc_raw = html_mod.unescape(p_match.group(1))
        cl = re.match(r"^(.+?)\s*·\s*(.+?)(?:\s*\(([^)]+)\))?\s*$", comp_loc_raw.strip())
        if not cl:
            continue

        company = cl.group(1).strip()
        location = cl.group(2).strip()
        worktype = cl.group(3).strip() if cl.group(3) else ""

        rest2 = rest[p_match.end(): p_match.end() + 800]
        sal = re.search(
            r"([\$£][\d,\. ]+(?:-[\$£]?[\d,\. ]+)?\s*/\s*(?:hour|hr|year|annum|yr)"
            r"|\£[\d,]+(?:k|,\d+)?(?:\s*(?:per annum|pa|/yr))?)",
            rest2, re.I,
        )
        salary = sal.group(1).strip() if sal else ""

        if any(s in company.lower() for s in ["linkedin", "©", "sunnyvale", "maude avenue"]):
            continue
        if len(company) > 100 or len(location) > 100:
            continue

        jobs.append({
            "title": title,
            "company": company,
            "location": location,
            "worktype": worktype,
            "salary": salary,
            "link": link,
            "source_email": subject,
        })

    return jobs


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------
def is_qualifying_job(job: dict) -> bool:
    """Return True if job matches London area + Remote/Hybrid criteria."""
    loc = job["location"].lower()
    wt = job["worktype"].lower()

    # Must be in London area or UK-wide (remote)
    location_ok = any(req in loc for req in REQUIRED_LOCATIONS)
    if not location_ok:
        return False

    # Explicitly excluded work types
    if any(ex in wt for ex in EXCLUDED_WORK_TYPES):
        return False

    # Accepted work types
    if any(acc in wt for acc in ACCEPTED_WORK_TYPES):
        return True

    # Unknown work type: only include if location is explicitly London
    if not wt:
        return any(ls in loc for ls in LONDON_SPECIFIC)

    return False


# ---------------------------------------------------------------------------
# CV generation via Anthropic API
# ---------------------------------------------------------------------------
BASE_CV_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Prosper Awuni - CV - {role_title}</title>
<style>
* {{ margin:0;padding:0;box-sizing:border-box; }}
body {{ font-family:'Calibri','Arial',sans-serif;font-size:10.5pt;color:#1a1a1a;background:#fff;padding:28px 36px;max-width:800px;margin:auto; }}
h1 {{ font-size:22pt;font-weight:700;text-align:center;letter-spacing:1px;color:#1a1a1a;margin-bottom:2px; }}
.subtitle {{ text-align:center;font-size:10.5pt;color:#444;margin-bottom:4px; }}
.contact {{ text-align:center;font-size:9.5pt;color:#444;margin-bottom:4px; }}
hr {{ border:none;border-top:1.5px solid #1a5276;margin:7px 0; }}
h2 {{ font-size:10.5pt;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:#1a5276;border-bottom:1px solid #1a5276;padding-bottom:2px;margin-top:11px;margin-bottom:5px; }}
h3 {{ font-size:10.5pt;font-weight:700;color:#1a1a1a;margin-top:8px;margin-bottom:1px; }}
.job-header {{ overflow:hidden;margin-bottom:4px; }}
.job-title {{ font-weight:700;font-size:10.5pt;float:left; }}
.job-date {{ font-style:italic;font-size:9.5pt;color:#555;float:right; }}
.clearfix {{ clear:both; }}
ul {{ margin-left:16px;margin-bottom:4px; }}
ul li {{ margin-bottom:2px;line-height:1.4; }}
p {{ line-height:1.5;margin-bottom:4px; }}
@media print {{ body {{ padding:18px 28px; }} @page {{ margin:0.6cm 0.8cm;size:A4; }} }}
</style>
</head>
<body>
{body}
</body>
</html>"""


def generate_cv_html(job: dict, base_cv_path: Path) -> str:
    """
    Use the Anthropic API to generate a tailored CV HTML for the given job.
    Falls back to lightly modified base CV if API is unavailable.
    """
    load_env()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    base_cv = base_cv_path.read_text()

    if not api_key:
        print(f"  [!] No ANTHROPIC_API_KEY — using base CV without tailoring for: {job['title']}")
        return base_cv

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""You are a professional CV writer. I will give you:
1. A base CV in HTML format for Prosper Awuni (Senior Cyber Security Engineer, CISSP)
2. A job to tailor it for

Your task: Rewrite ONLY the following sections of the CV HTML to target this specific role:
- The <title> tag content
- The subtitle <p> (the bold specialisation line under the name)
- The "Professional Profile" section content
- Optionally reorder/emphasise the most relevant items in "Core Skills and Technical Stack"

DO NOT change:
- The candidate's name
- Contact details
- Any experience bullet points (leave them exactly as-is)
- The Education or Certifications sections
- The HTML structure, CSS styles, or any other sections

Return ONLY the complete, valid HTML file. No explanations, no code fences.

TARGET JOB:
Title: {job['title']}
Company: {job['company']}
Location: {job['location']} ({job['worktype'] or 'work type not stated'})
Salary: {job['salary'] or 'not stated'}

BASE CV:
{base_cv}
"""

        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        cv_html = message.content[0].text.strip()
        # Strip any accidental code fences
        if cv_html.startswith("```"):
            cv_html = re.sub(r"^```[^\n]*\n", "", cv_html)
            cv_html = re.sub(r"\n```$", "", cv_html)

        return cv_html

    except Exception as e:
        print(f"  [!] Anthropic API error: {e} — using base CV")
        return base_cv


# ---------------------------------------------------------------------------
# PDF conversion
# ---------------------------------------------------------------------------
def html_to_pdf(html_path: Path, pdf_path: Path) -> None:
    from weasyprint import HTML as WHTML
    WHTML(filename=str(html_path)).write_pdf(str(pdf_path))


# ---------------------------------------------------------------------------
# Email digest
# ---------------------------------------------------------------------------
def send_digest(qualifying_jobs: list[dict], cv_map: dict, today: str) -> None:
    """Send HTML email digest with job table, apply links, and generated CV list."""
    import sys
    sys.path.insert(0, str(BASE_DIR))
    from scripts.send_email import send_alert

    cv_rows = ""
    for job in qualifying_jobs:
        key = (job["title"], job["company"])
        cv_file = cv_map.get(key, "")
        link = job.get("link", "")
        apply_cell = f'<a href="{link}" style="color:#0a66c2;font-weight:bold;">Apply →</a>' if link else "—"
        row_style = ' style="background:#eaf4fb;"' if cv_file else ""
        cv_cell = f'<span style="font-weight:bold;">{cv_file}</span>' if cv_file else "—"
        cv_rows += (
            f'<tr{row_style}>'
            f'<td>{job["title"]}</td>'
            f'<td>{job["company"]}</td>'
            f'<td>{job["location"]}</td>'
            f'<td>{job["worktype"] or "?"}</td>'
            f'<td>{job["salary"] or "—"}</td>'
            f'<td>{cv_cell}</td>'
            f'<td>{apply_cell}</td>'
            f'</tr>\n'
        )

    html_body = f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
body{{font-family:Arial,sans-serif;font-size:13px;color:#1a1a1a;max-width:780px;margin:auto}}
h1{{font-size:20px;color:#1a5276}}
h2{{font-size:14px;color:#1a5276;border-bottom:1px solid #1a5276;padding-bottom:3px;margin-top:20px}}
table{{width:100%;border-collapse:collapse;margin-top:8px}}
th{{background:#1a5276;color:#fff;padding:7px 10px;text-align:left;font-size:12px}}
td{{padding:6px 10px;border-bottom:1px solid #e0e0e0;font-size:12px;vertical-align:top}}
tr:nth-child(even){{background:#f5f8fa}}
a{{color:#0a66c2}}
</style>
</head><body>
<h1>LinkedIn Job Alerts Digest — {today}</h1>
<p>Filter: London area · Remote &amp; Hybrid only · <strong>{len(qualifying_jobs)} qualifying jobs</strong> · <strong>{len(cv_map)} CVs generated</strong></p>
<h2>Qualifying Jobs</h2>
<table>
<tr><th>Title</th><th>Company</th><th>Location</th><th>Type</th><th>Salary</th><th>CV File</th><th>Apply</th></tr>
{cv_rows}</table>
<br><p style="font-size:11px;color:#888;">Generated by daily_job_alerts.py · cvs/{today}/</p>
</body></html>"""

    text_body = f"LinkedIn Job Alerts Digest — {today}\n\n"
    for job in qualifying_jobs:
        key = (job["title"], job["company"])
        cv = cv_map.get(key, "")
        link = job.get("link", "")
        text_body += f"• {job['title']} — {job['company']} · {job['location']} ({job['worktype'] or '?'})"
        if job.get("salary"):
            text_body += f"  [{job['salary']}]"
        if cv:
            text_body += f"\n  → CV:    {cv}"
        if link:
            text_body += f"\n  → Apply: {link}"
        text_body += "\n\n"

    send_alert(
        subject=f"LinkedIn Job Alerts — {today} ({len(cv_map)} CVs generated)",
        html_body=html_body,
        text_body=text_body,
    )


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------
def run(days: int = 1, dry_run: bool = False) -> None:
    load_env()
    today = datetime.date.today().isoformat()
    output_dir = CV_BASE_DIR / today
    output_dir.mkdir(parents=True, exist_ok=True)

    # Find the most recent base CV to use as template
    base_cv_candidates = sorted(
        (CV_BASE_DIR).glob("*/Prosper_Awuni_CV_*.html"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if not base_cv_candidates:
        print("No base CV found in cvs/ directory. Add one first.")
        sys.exit(1)
    base_cv_path = base_cv_candidates[0]
    print(f"Using base CV: {base_cv_path.relative_to(BASE_DIR)}")

    # Fetch emails from Gmail
    print(f"\nFetching LinkedIn job alerts from Gmail (last {days} day(s))...")
    raw_messages = fetch_linkedin_alerts_gmail(days=days)
    print(f"  {len(raw_messages)} email(s) retrieved.")

    # Extract all jobs
    all_jobs = []
    seen = set()
    for msg in raw_messages:
        jobs = extract_jobs_from_email(msg["htmlBody"], msg["subject"])
        for j in jobs:
            key = (j["title"].lower(), j["company"].lower())
            if key not in seen:
                seen.add(key)
                all_jobs.append(j)
    print(f"  {len(all_jobs)} unique jobs extracted.")

    # Filter
    qualifying = [j for j in all_jobs if is_qualifying_job(j)]
    excluded = len(all_jobs) - len(qualifying)
    print(f"  {len(qualifying)} qualifying (London remote/hybrid). {excluded} excluded.")

    if dry_run:
        print("\n[DRY RUN] Qualifying jobs:")
        for j in qualifying:
            print(f"  • {j['title']} — {j['company']} · {j['location']} ({j['worktype'] or '?'}) {j.get('salary','')}")
        return

    # Generate CVs
    cv_map = {}
    for job in qualifying:
        slug = re.sub(r"[^A-Za-z0-9]+", "", f"{job['company']}_{job['title']}")[:40]
        html_path = output_dir / f"Prosper_Awuni_CV_{slug}.html"
        pdf_path = output_dir / f"Prosper_Awuni_CV_{slug}.pdf"

        print(f"\n  Generating CV: {job['title']} @ {job['company']}")
        cv_html = generate_cv_html(job, base_cv_path)
        html_path.write_text(cv_html)
        print(f"    HTML saved: {html_path.name}")

        html_to_pdf(html_path, pdf_path)
        print(f"    PDF saved:  {pdf_path.name}")

        cv_map[(job["title"], job["company"])] = pdf_path.name

    print(f"\n{len(cv_map)} CVs generated in {output_dir.relative_to(BASE_DIR)}/")

    # Send digest
    print("\nSending email digest...")
    send_digest(qualifying, cv_map, today)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily LinkedIn job alert processor")
    parser.add_argument("--days", type=int, default=1, help="Days to look back (default: 1)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and filter only — no CVs or email")
    parser.add_argument("--setup-gmail", action="store_true", help="One-time Gmail OAuth setup")
    args = parser.parse_args()

    if args.setup_gmail:
        setup_gmail_oauth()
    else:
        run(days=args.days, dry_run=args.dry_run)
