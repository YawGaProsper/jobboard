"""
Create a Gmail draft with optional PDF attachments — uses Gmail API over HTTPS.

AUTHENTICATION (one-time setup — ~10 minutes):
  This script needs a Google OAuth refresh token. Follow these steps once:

  1. Go to https://console.cloud.google.com and create or select a project.
  2. Enable the Gmail API:
       APIs & Services → Enable APIs → search "Gmail API" → Enable
  3. Create OAuth credentials:
       APIs & Services → Credentials → Create Credentials → OAuth client ID
       Application type: Desktop app  →  Name it anything  →  Create
       Download the JSON file (you'll need client_id and client_secret from it).
  4. Get a refresh token via OAuth Playground (no local code needed):
       a. Go to https://developers.google.com/oauthplayground
       b. Click the gear icon (top-right) → tick "Use your own OAuth credentials"
       c. Enter your client_id and client_secret from step 3
       d. In the left panel, find "Gmail API v1" → tick:
            https://www.googleapis.com/auth/gmail.compose
       e. Click "Authorize APIs" → sign in with prosper.awuni@gmail.com → Allow
       f. Click "Exchange authorization code for tokens"
       g. Copy the "Refresh token" value shown.
  5. Add these three values to /home/user/jobboard/.env (create if missing):
       GMAIL_CLIENT_ID=your_client_id_here
       GMAIL_CLIENT_SECRET=your_client_secret_here
       GMAIL_REFRESH_TOKEN=your_refresh_token_here

  Once added, this script runs headlessly on every future routine run.

Usage:
  python3 scripts/gmail_draft.py \
      --to prosper.awuni@gmail.com \
      --subject "Daily Job Alert..." \
      --html /tmp/body.html \
      --text /tmp/body.txt \
      --attach /tmp/cvs/Prosper_Awuni_CV_Company.pdf
"""

import argparse
import base64
import email.encoders
import email.mime.base
import email.mime.multipart
import email.mime.text
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


RECIPIENT = "prosper.awuni@gmail.com"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DRAFTS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"


def _load_env():
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())


def _get_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            resp = json.loads(r.read())
        if "access_token" not in resp:
            print(f"Token exchange failed: {resp}")
            sys.exit(1)
        return resp["access_token"]
    except urllib.error.HTTPError as e:
        print(f"Token exchange HTTP error {e.code}: {e.read().decode()}")
        sys.exit(1)


def _build_mime(to: str, subject: str, html_body: str,
                text_body: str, attachment_paths: list) -> str:
    root = email.mime.multipart.MIMEMultipart("mixed")
    root["to"] = to
    root["subject"] = subject

    alt = email.mime.multipart.MIMEMultipart("alternative")
    if text_body:
        alt.attach(email.mime.text.MIMEText(text_body, "plain", "utf-8"))
    alt.attach(email.mime.text.MIMEText(html_body, "html", "utf-8"))
    root.attach(alt)

    for path_str in (attachment_paths or []):
        path = Path(path_str)
        if not path.exists():
            print(f"WARNING: attachment not found, skipping: {path}")
            continue
        part = email.mime.base.MIMEBase("application", "pdf")
        part.set_payload(path.read_bytes())
        email.encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=path.name)
        root.attach(part)
        print(f"  Attaching: {path.name} ({path.stat().st_size // 1024} KB)")

    return base64.urlsafe_b64encode(root.as_bytes()).decode()


def create_draft(to: str, subject: str, html_body: str,
                 text_body: str = "", attachment_paths: list = None) -> None:
    _load_env()

    client_id = os.environ.get("GMAIL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN", "").strip()

    if not all([client_id, client_secret, refresh_token]):
        print(
            "\nGmail credentials not configured. Add these to .env:\n"
            "  GMAIL_CLIENT_ID=...\n"
            "  GMAIL_CLIENT_SECRET=...\n"
            "  GMAIL_REFRESH_TOKEN=...\n\n"
            "See the docstring at the top of this file for setup instructions."
        )
        sys.exit(1)

    print("Refreshing Gmail access token...")
    access_token = _get_access_token(client_id, client_secret, refresh_token)

    print(f"Building MIME message (to: {to}, attachments: {len(attachment_paths or [])})...")
    raw = _build_mime(to, subject, html_body, text_body, attachment_paths or [])

    payload = json.dumps({"message": {"raw": raw}}).encode()
    req = urllib.request.Request(
        DRAFTS_URL, data=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            resp = json.loads(r.read())
        draft_id = resp.get("id", "unknown")
        print(f"Gmail draft created. Draft ID: {draft_id}")
        print("Open Gmail → Drafts to review and send.")
    except urllib.error.HTTPError as e:
        print(f"Gmail API error {e.code}: {e.read().decode()}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a Gmail draft with PDF attachments")
    parser.add_argument("--to", default=RECIPIENT)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--html", required=True, help="Path to HTML body file")
    parser.add_argument("--text", default="", help="Path to plain-text body file")
    parser.add_argument("--attach", dest="attachments", action="append",
                        default=[], metavar="FILE",
                        help="Path to a PDF to attach (repeatable)")
    args = parser.parse_args()

    create_draft(
        to=args.to,
        subject=args.subject,
        html_body=Path(args.html).read_text(),
        text_body=Path(args.text).read_text() if args.text else "",
        attachment_paths=args.attachments,
    )
