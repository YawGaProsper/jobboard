# Job Board — Claude Code Instructions

## Owner
Prosper Awuni (prosper.awuni@gmail.com) — Senior Cyber Security Engineer, CISSP

## Purpose
Automated daily pipeline: reads LinkedIn job alert emails from Gmail,
filters for London-area remote/hybrid roles, generates tailored CVs,
saves as HTML + PDF, and sends an email digest via Resend.

---

## Daily Routine (how to run each session)

### Step 1 — Fetch today's LinkedIn job alerts from Gmail

Use the Gmail MCP server (already connected) to search for LinkedIn alerts:

```
Query: from:jobalerts-noreply@linkedin.com newer_than:1d
Tool: mcp__search_threads
```

For each thread, fetch the full content with `mcp__get_thread`.
Extract jobs using the `extract_jobs_from_email()` function in
`scripts/daily_job_alerts.py`.

### Step 2 — Filter qualifying jobs

Criteria:
- **Location**: London, London Area, Greater London, City of London, OR
  United Kingdom / England (for fully remote roles)
- **Work type**: Remote OR Hybrid
- **Exclude**: On-site, non-UK locations (Netherlands, Switzerland, Spain, etc.)
- **Unknown work type**: Include only if location is specifically London

### Step 3 — Generate tailored CVs

For each qualifying job, tailor the most recent base CV from `cvs/` by:
- Updating the HTML `<title>` and subtitle line to match the role
- Rewriting the Professional Profile to target the specific company/role
- Reordering Core Skills to lead with the most relevant tools
- Keeping all experience bullets, education, and certifications unchanged

Save as: `cvs/YYYY-MM-DD/Prosper_Awuni_CV_{CompanyTitle}.html`

Convert to PDF using WeasyPrint:
```python
from weasyprint import HTML
HTML(filename="path/to/cv.html").write_pdf("path/to/cv.pdf")
```

### Step 4 — Send email digest

Use `scripts/send_email.py`:
```python
from scripts.send_email import send_alert
send_alert(subject="...", html_body="...", text_body="...")
```

The email should include a table of all qualifying jobs and list the CVs generated.

**Resend IP restriction**: If sending fails with HTTP 403 "allowlist", ask the
user to go to https://resend.com/api-keys, edit their key, and remove all IP
address restrictions.

### Step 5 — Commit and push

```bash
git add cvs/YYYY-MM-DD/
git commit -m "Add tailored CVs for YYYY-MM-DD job alerts"
git push -u origin claude/focused-rubin-AIYyM
```

---

## Standalone script (requires Gmail OAuth + Anthropic API key)

```bash
# One-time Gmail setup
python3 scripts/daily_job_alerts.py --setup-gmail

# Daily run
export ANTHROPIC_API_KEY=sk-ant-...
python3 scripts/daily_job_alerts.py

# Preview only (no CVs generated)
python3 scripts/daily_job_alerts.py --dry-run
```

---

## CV template

Base CV location: `cvs/YYYY-MM-DD/Prosper_Awuni_CV_*.html` (most recent date)

Key candidate facts:
- Name: Prosper Awuni
- Title: Senior Cyber Security Analyst / Engineer
- Certifications: CISSP, CompTIA Security+, MCSA, CompTIA A+, Google IT Support
- Current employer: University of the Arts London (UAL), March 2025–present
- Previous: UAL Cyber Security Analyst (2022–2025), HICX Solutions IT Engineer (2021–2022)
- Tools: CrowdStrike Falcon, Microsoft Sentinel (KQL), Mimecast, BeyondTrust PAM,
  Microsoft Intune, Microsoft Defender (MDE/MDI/365), Tenable.io, Pentera BAS,
  Check Point Harmony, Tanium, Splunk (SPL), PowerShell, Azure AD / Entra ID
- Contact: +44 7403 190750 | prosper.awuni@gmail.com | linkedin.com/in/awuniprosper
- Salary expectation: GBP85,000–GBP100,000
- Location preference: London Hybrid or UK Remote

---

## Repo structure

```
jobboard/
├── cvs/
│   └── YYYY-MM-DD/          # Dated folders of tailored CVs
│       ├── *.html
│       └── *.pdf
├── scripts/
│   ├── daily_job_alerts.py  # Main orchestration script
│   └── send_email.py        # Resend email helper
├── .env                     # RESEND_API_KEY (gitignored)
├── .env.example
└── CLAUDE.md                # This file
```
