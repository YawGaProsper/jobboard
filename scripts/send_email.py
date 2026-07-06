"""
Daily job alert email sender — uses the Resend HTTP API (HTTPS/443).

IMPORTANT — NETWORK CONSTRAINTS IN CLOUD EXECUTION ENVIRONMENT:
  Only outbound HTTPS (port 443) is available. SMTP ports (25/465/587) are
  blocked, so Gmail SMTP cannot be used. Resend's API works over HTTPS and is
  the correct approach for this environment.

RESEND SETUP (one-time, ~2 minutes):
  The Resend API key must NOT have an IP allowlist restriction, because the
  cloud execution environment's outbound IP changes each session. To fix this:

  1. Go to https://resend.com/api-keys
  2. Click the three-dot menu on your key → Edit
  3. Under "IP Addresses", delete all entries (leave it blank/unrestricted)
  4. Save. The key will then work from any IP including this cloud environment.

  The "from" address is 'onboarding@resend.dev' — Resend's shared test address
  that works for all accounts without domain verification.

Usage:
  python3 scripts/send_email.py --subject "..." --html body.html --text body.txt \
      [--attach /path/to/file.pdf ...]
  or call send_alert() / send_alert_with_attachments() directly.
"""

import os
import sys
import json
import base64
import urllib.request
import argparse
from pathlib import Path
from typing import List


RESEND_API_URL = "https://api.resend.com/emails"
SENDER_FROM = "Job Alerts <onboarding@resend.dev>"
RECIPIENT = "prosper.awuni@gmail.com"


def _load_env():
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())


def _build_attachments(paths: List[str]) -> list:
    """Base64-encode each file and return Resend attachment objects."""
    result = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            print(f"WARNING: attachment not found, skipping: {p}")
            continue
        content = base64.b64encode(path.read_bytes()).decode("ascii")
        result.append({"filename": path.name, "content": content})
    return result


def send_alert(subject: str, html_body: str, text_body: str,
               attachment_paths: List[str] = None) -> None:
    _load_env()
    api_key = os.environ.get("RESEND_API_KEY", "re_JENKzDQe_4q5GsFGi3VXcZv19V8Fe4Lyw").strip()

    body: dict = {
        "from": SENDER_FROM,
        "to": [RECIPIENT],
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }

    if attachment_paths:
        attachments = _build_attachments(attachment_paths)
        if attachments:
            body["attachments"] = attachments
            print(f"Attaching {len(attachments)} file(s): "
                  f"{', '.join(a['filename'] for a in attachments)}")

    payload = json.dumps(body).encode()

    req = urllib.request.Request(
        RESEND_API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as r:
            res = json.loads(r.read())
            print(f"Email sent via Resend. ID: {res.get('id', 'unknown')}")
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        if e.code == 403 and "allowlist" in body_text.lower():
            print(
                "\nRESEND IP ALLOWLIST ERROR — fix in 2 minutes:\n"
                "  1. Go to https://resend.com/api-keys\n"
                "  2. Edit your key → remove all IP address restrictions\n"
                "  3. Save, then re-run the agent.\n"
                f"\nFull error: HTTP {e.code} {body_text}"
            )
        else:
            print(f"Resend HTTP error {e.code}: {body_text}")
        sys.exit(1)
    except Exception as exc:
        print(f"Resend request failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", required=True)
    parser.add_argument("--html", required=True, help="Path to HTML body file")
    parser.add_argument("--text", required=True, help="Path to plain-text body file")
    parser.add_argument("--attach", dest="attachments", action="append",
                        default=[], metavar="FILE",
                        help="Path to a file to attach (repeatable)")
    args = parser.parse_args()

    send_alert(
        subject=args.subject,
        html_body=Path(args.html).read_text(),
        text_body=Path(args.text).read_text(),
        attachment_paths=args.attachments,
    )
