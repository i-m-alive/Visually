"""
Email sending — Resend HTTP API (preferred) or SMTP fallback, env-configured.

Env:
  RESEND_API_KEY   — use Resend (https://resend.com) when set
  EMAIL_FROM       — from address (default reports@visually.local)
  SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS — SMTP fallback when Resend unset

send_email() never raises — returns True/False and logs loudly, so schedulers
and alert loops keep running when email is unconfigured.
"""
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

EMAIL_FROM = os.getenv("EMAIL_FROM", "reports@visually.local")


def is_email_configured() -> bool:
    return bool(os.getenv("RESEND_API_KEY") or os.getenv("SMTP_HOST"))


async def send_email(to: str, subject: str, html: str) -> bool:
    """Send an HTML email. Returns True on success, False otherwise (never raises)."""
    if not to or "@" not in to:
        print(f"[email] ✗ invalid recipient: {to!r}", flush=True)
        return False

    resend_key = os.getenv("RESEND_API_KEY")
    if resend_key:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {resend_key}"},
                    json={"from": EMAIL_FROM, "to": [to], "subject": subject, "html": html},
                )
            if resp.status_code in (200, 201):
                print(f"[email] ✓ sent to {to} via Resend", flush=True)
                return True
            print(f"[email] ✗ Resend {resp.status_code}: {resp.text[:200]}", flush=True)
            return False
        except Exception as exc:
            print(f"[email] ✗ Resend error: {exc}", flush=True)
            return False

    smtp_host = os.getenv("SMTP_HOST")
    if smtp_host:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"], msg["From"], msg["To"] = subject, EMAIL_FROM, to
            msg.attach(MIMEText(html, "html"))
            port = int(os.getenv("SMTP_PORT", "587"))
            with smtplib.SMTP(smtp_host, port, timeout=20) as server:
                server.starttls()
                user, pw = os.getenv("SMTP_USER"), os.getenv("SMTP_PASS")
                if user and pw:
                    server.login(user, pw)
                server.sendmail(EMAIL_FROM, [to], msg.as_string())
            print(f"[email] ✓ sent to {to} via SMTP", flush=True)
            return True
        except Exception as exc:
            print(f"[email] ✗ SMTP error: {exc}", flush=True)
            return False

    print(f"[email] ⚠ NOT CONFIGURED (set RESEND_API_KEY or SMTP_HOST) — dropped email to {to}: {subject!r}", flush=True)
    return False
