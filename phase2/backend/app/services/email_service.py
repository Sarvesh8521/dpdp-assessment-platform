"""Email service for sending transactional emails via Resend API.

Supports login OTP, password reset OTP, and general system notifications.
Includes graceful error handling and local development console logging.
"""

from __future__ import annotations

import httpx
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


def _get_base_html_template(title: str, subtitle: str, content_html: str) -> str:
    """Wrap content in a modern, responsive HTML email template."""
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    body {{
      margin: 0;
      padding: 0;
      background-color: #f3f4f6;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      color: #1f2937;
    }}
    .container {{
      max-width: 540px;
      margin: 40px auto;
      background: #ffffff;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
    }}
    .header {{
      background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
      padding: 32px 24px;
      text-align: center;
      color: #ffffff;
    }}
    .logo {{
      display: inline-block;
      width: 44px;
      height: 44px;
      background: #3b82f6;
      border-radius: 10px;
      line-height: 44px;
      text-align: center;
      font-weight: bold;
      font-size: 22px;
      color: white;
      margin-bottom: 12px;
    }}
    .header h1 {{
      margin: 0;
      font-size: 22px;
      font-weight: 700;
      letter-spacing: -0.02em;
    }}
    .header p {{
      margin: 6px 0 0;
      color: #94a3b8;
      font-size: 14px;
    }}
    .body {{
      padding: 32px 24px;
    }}
    .otp-box {{
      background: #f8fafc;
      border: 2px dashed #cbd5e1;
      border-radius: 8px;
      padding: 20px;
      text-align: center;
      margin: 24px 0;
    }}
    .otp-code {{
      font-size: 36px;
      font-weight: 800;
      letter-spacing: 8px;
      color: #1e293b;
      font-family: 'Courier New', Courier, monospace;
    }}
    .alert-text {{
      font-size: 13px;
      color: #64748b;
      line-height: 1.5;
      margin-top: 16px;
      border-top: 1px solid #f1f5f9;
      padding-top: 16px;
    }}
    .footer {{
      background: #f8fafc;
      padding: 20px 24px;
      text-align: center;
      font-size: 12px;
      color: #94a3b8;
      border-top: 1px solid #e2e8f0;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="logo">🛡️</div>
      <h1>DPDP Assessment Platform</h1>
      <p>{subtitle}</p>
    </div>
    <div class="body">
      {content_html}
    </div>
    <div class="footer">
      <p>&copy; 2026 DPDP Assessment Platform. All rights reserved.</p>
      <p>Automated security notification — do not reply to this email.</p>
    </div>
  </div>
</body>
</html>"""


async def send_email(
    to: str,
    subject: str,
    html_content: str,
    text_content: str | None = None,
) -> bool:
    """Send an email using Resend API with logging and fallback."""
    settings = get_settings()
    api_key = settings.resend_api_key.strip() if settings.resend_api_key else ""

    if settings.mail_host and settings.mail_user and settings.mail_password:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.mail_from
        msg["To"] = to
        if text_content:
            msg.set_content(text_content)
            msg.add_alternative(html_content, subtype="html")
        else:
            msg.set_content(html_content, subtype="html")
        
        try:
            with smtplib.SMTP(settings.mail_host, settings.mail_port) as server:
                server.starttls()
                server.login(settings.mail_user, settings.mail_password)
                server.send_message(msg)
            logger.info("SMTP email sent to %s", to)
            return True
        except Exception as exc:
            logger.error("Failed to send email to %s via SMTP: %s", to, exc)
            return False

    if not api_key:
        logger.warning(
            "RESEND_API_KEY is not configured in settings. Email to %s skipped.", to
        )
        return False

    payload = {
        "from": settings.mail_from,
        "to": [to],
        "subject": subject,
        "html": html_content,
    }
    if text_content:
        payload["text"] = text_content

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(RESEND_API_URL, json=payload, headers=headers)

        if resp.status_code in (200, 201):
            data = resp.json()
            logger.info("Resend email sent to %s (id: %s)", to, data.get("id"))
            return True
        else:
            logger.warning(
                "Resend API returned status %s for %s: %s",
                resp.status_code,
                to,
                resp.text,
            )
            return False
    except Exception as exc:
        logger.error("Failed to send email to %s via Resend: %s", to, exc)
        return False


async def send_login_otp_email(to: str, otp: str) -> bool:
    """Send a 6-digit login verification OTP via Resend."""
    subject = f"Your Login Code: {otp} - DPDP Scanner"
    content_html = f"""
      <h2 style="margin: 0 0 12px; font-size: 18px; color: #1e293b;">Login Verification</h2>
      <p style="margin: 0 0 16px; font-size: 14px; color: #475569; line-height: 1.5;">
        You requested a one-time passcode to sign in to your DPDP Assessment account.
        Use the verification code below to complete your login:
      </p>
      <div class="otp-box">
        <div class="otp-code">{otp}</div>
        <p style="margin: 8px 0 0; font-size: 13px; color: #64748b;">This code expires in <strong>10 minutes</strong>.</p>
      </div>
      <div class="alert-text">
        <strong>Security Notice:</strong> Never share this code with anyone.
        If you did not request this login code, you can safely ignore this email.
      </div>
    """
    text_content = f"Your DPDP Scanner login code is: {otp}\nThis code expires in 10 minutes. Never share this code with anyone."

    # Always log OTP in server logs for dev/testing visibility
    logger.info("[AUTH OTP LOGIN] Email: %s | OTP: %s", to, otp)

    return await send_email(
        to=to,
        subject=subject,
        html_content=_get_base_html_template(
            title="Your Login Verification Code",
            subtitle="Secure Account Access",
            content_html=content_html,
        ),
        text_content=text_content,
    )


async def send_password_reset_otp_email(to: str, otp: str) -> bool:
    """Send a 6-digit password reset verification OTP via Resend."""
    subject = f"Your Password Reset Code: {otp} - DPDP Scanner"
    content_html = f"""
      <h2 style="margin: 0 0 12px; font-size: 18px; color: #1e293b;">Reset Your Password</h2>
      <p style="margin: 0 0 16px; font-size: 14px; color: #475569; line-height: 1.5;">
        We received a request to reset the password for your DPDP Assessment account.
        Enter the 6-digit verification code below along with your new password:
      </p>
      <div class="otp-box">
        <div class="otp-code">{otp}</div>
        <p style="margin: 8px 0 0; font-size: 13px; color: #64748b;">This code expires in <strong>10 minutes</strong>.</p>
      </div>
      <div class="alert-text">
        <strong>Security Notice:</strong> If you did not request a password reset, please secure your account immediately or ignore this message.
      </div>
    """
    text_content = f"Your DPDP Scanner password reset code is: {otp}\nThis code expires in 10 minutes. If you did not request this, ignore this email."

    # Always log OTP in server logs for dev/testing visibility
    logger.info("[AUTH OTP RESET] Email: %s | OTP: %s", to, otp)

    return await send_email(
        to=to,
        subject=subject,
        html_content=_get_base_html_template(
            title="Reset Your Password",
            subtitle="Account Security & Recovery",
            content_html=content_html,
        ),
        text_content=text_content,
    )
