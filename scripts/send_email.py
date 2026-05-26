"""
Daily job alert email sender.

Reads GMAIL_APP_PASSWORD from environment (or .env file in repo root).
Usage: python3 scripts/send_email.py --subject "..." --html body.html --text body.txt
       or call send_alert() directly from the agent script.

To generate a Gmail App Password:
  https://myaccount.google.com/apppasswords
  (requires 2-Step Verification to be enabled on the account)
"""

import os
import sys
import smtplib
import argparse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


SENDER = "prosper.awuni@gmail.com"
RECIPIENT = "prosper.awuni@gmail.com"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _load_env():
    """Load .env from repo root if present."""
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())


def send_alert(subject: str, html_body: str, text_body: str) -> None:
    _load_env()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not password:
        raise RuntimeError(
            "GMAIL_APP_PASSWORD not set. "
            "Add it to the repo .env file or export it as an environment variable.\n"
            "Generate one at: https://myaccount.google.com/apppasswords"
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SENDER
    msg["To"] = RECIPIENT

    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SENDER, password)
        smtp.sendmail(SENDER, [RECIPIENT], msg.as_string())

    print(f"Email sent to {RECIPIENT}: {subject}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", required=True)
    parser.add_argument("--html", required=True, help="Path to HTML body file")
    parser.add_argument("--text", required=True, help="Path to plain-text body file")
    args = parser.parse_args()

    html_body = Path(args.html).read_text()
    text_body = Path(args.text).read_text()
    send_alert(args.subject, html_body, text_body)
