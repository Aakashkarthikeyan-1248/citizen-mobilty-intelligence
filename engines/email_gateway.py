"""
============================================================
CMI — Custom Email & SMTP Gateway Engine
============================================================
Handles automated email dispatch for citizen authentication,
OTP verification, and password reset requests.

Features:
  - Standard Python smtplib (STARTTLS & SSL support)
  - Universal provider support: Gmail, SendGrid, Brevo, AWS SES, Outlook
  - Responsive, high-contrast HTML email templates
  - Async execution via asyncio.to_thread to prevent blocking event loop
  - Automatic email masking (e.g., aa•••••n@gmail.com)
  - Zero-crash resiliency with descriptive audit logging
============================================================
"""

from __future__ import annotations

import asyncio
import logging
import re
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

logger = logging.getLogger("cmi.engines.email_gateway")


@dataclass
class EmailDispatchResult:
    status: str            # "success" | "unconfigured" | "error" | "simulated"
    gateway: str           # "smtp" | "local"
    delivered: bool        # True if SMTP server accepted the message
    message: str           # Plain-text user-friendly status message
    email_masked: str      # e.g., "aa•••••n@gmail.com"
    raw_response: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class EmailGatewayEngine:
    """
    Unified Email Gateway Engine for the CMI Platform.
    Manages SMTP connections, HTML email rendering, and delivery verification.
    """

    def __init__(
        self,
        smtp_host: str = "smtp.gmail.com",
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        smtp_from_email: str = "",
        smtp_from_name: str = "CMI Singara Chennai",
    ):
        self.smtp_host = (smtp_host or "smtp.gmail.com").strip()
        self.smtp_port = int(smtp_port or 587)
        self.smtp_user = (smtp_user or "").strip()
        self.smtp_password = (smtp_password or "").strip()
        self.smtp_from_email = (smtp_from_email or self.smtp_user or "noreply@cmi.chennai.gov.in").strip()
        self.smtp_from_name = (smtp_from_name or "CMI Singara Chennai").strip()
        self.audit_log: List[Dict[str, Any]] = []

    @classmethod
    def from_settings(cls, settings) -> EmailGatewayEngine:
        return cls(
            smtp_host=getattr(settings, "smtp_host", "smtp.gmail.com"),
            smtp_port=getattr(settings, "smtp_port", 587),
            smtp_user=getattr(settings, "smtp_user", ""),
            smtp_password=getattr(settings, "smtp_password", ""),
            smtp_from_email=getattr(settings, "smtp_from_email", ""),
            smtp_from_name=getattr(settings, "smtp_from_name", "CMI Singara Chennai"),
        )

    def mask_email(self, email: str) -> str:
        """Masks email address for privacy (e.g. j***e@example.com)."""
        clean = (email or "").strip().lower()
        if "@" not in clean:
            return clean
        user, domain = clean.split("@", 1)
        if len(user) <= 2:
            masked_user = user[0] + "***" if user else "***"
        else:
            masked_user = user[:2] + "•••••" + user[-1:]
        return f"{masked_user}@{domain}"

    def _record_audit(self, entry: EmailDispatchResult, template: str):
        record = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.timestamp)),
            "template": template,
            "status": entry.status,
            "gateway": entry.gateway,
            "delivered": entry.delivered,
            "email_masked": entry.email_masked,
            "message": entry.message,
        }
        self.audit_log.append(record)
        if len(self.audit_log) > 50:
            self.audit_log.pop(0)

    # ── Dispatch Verification Code via Email ──────────────────
    async def send_otp_email(
        self,
        to_email: str,
        otp: str,
        citizen_name: str = "Citizen",
    ) -> EmailDispatchResult:
        """
        Dispatches a 6-digit verification code to the citizen's email address.
        Uses SMTP with TLS if configured in .env, with graceful non-blocking fallback.
        """
        clean_email = (to_email or "").strip().lower()
        masked = self.mask_email(clean_email)

        # Basic email validation
        if not clean_email or "@" not in clean_email or "." not in clean_email.split("@")[-1]:
            res = EmailDispatchResult(
                status="error",
                gateway="local",
                delivered=False,
                message="Invalid email address format.",
                email_masked=to_email,
            )
            self._record_audit(res, "EMAIL_OTP")
            return res

        # Check if SMTP credentials are provided
        if not self.smtp_user or not self.smtp_password:
            logger.info("SMTP not configured in .env — simulated email dispatch for %s", masked)
            res = EmailDispatchResult(
                status="unconfigured",
                gateway="local",
                delivered=False,
                message=f"SMTP server not configured in .env. To enable real email delivery, set SMTP_USER and SMTP_PASSWORD.",
                email_masked=masked,
            )
            self._record_audit(res, "EMAIL_OTP")
            return res

        # Run SMTP dispatch in a background worker thread with 10s timeout
        try:
            delivered, error_msg = await asyncio.wait_for(
                asyncio.to_thread(self._sync_send_smtp, clean_email, otp, citizen_name),
                timeout=10.0,
            )
            if delivered:
                logger.info("Email OTP successfully delivered via SMTP to %s", masked)
                res = EmailDispatchResult(
                    status="success",
                    gateway="smtp",
                    delivered=True,
                    message=f"Verification code successfully sent to {masked} via email.",
                    email_masked=masked,
                )
            else:
                logger.warning("SMTP delivery failed for %s: %s", masked, error_msg)
                res = EmailDispatchResult(
                    status="error",
                    gateway="smtp",
                    delivered=False,
                    message=f"SMTP dispatch failed: {error_msg}",
                    email_masked=masked,
                )
        except asyncio.TimeoutError:
            logger.error("SMTP connection timed out for %s", masked)
            res = EmailDispatchResult(
                status="error",
                gateway="smtp",
                delivered=False,
                message="SMTP server connection timed out (10s). Check SMTP_HOST and SMTP_PORT.",
                email_masked=masked,
            )
        except Exception as exc:
            logger.error("Unexpected error in email dispatch: %s", exc)
            res = EmailDispatchResult(
                status="error",
                gateway="smtp",
                delivered=False,
                message=str(exc),
                email_masked=masked,
            )

        self._record_audit(res, "EMAIL_OTP")
        return res

    def _sync_send_smtp(self, to_email: str, otp: str, citizen_name: str) -> tuple[bool, str]:
        """Synchronous blocking SMTP dispatch executed inside asyncio.to_thread."""
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"🔐 CMI Singara Chennai — Verification Code: {otp}"
            msg["From"] = f"{self.smtp_from_name} <{self.smtp_from_email}>"
            msg["To"] = to_email

            # Plain-text alternative
            text_body = (
                f"Hello {citizen_name},\n\n"
                f"Your 6-digit verification code for CMI Singara Chennai is: {otp}\n\n"
                f"This code will expire in 10 minutes.\n"
                f"If you did not request this password reset, please ignore this email.\n\n"
                f"Citizen Mobility Intelligence Platform — Chennai, Tamil Nadu\n"
            )

            # Styled HTML alternative
            html_body = f"""
            <!DOCTYPE html>
            <html>
            <head>
              <meta charset="utf-8">
              <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f1f5f9; margin: 0; padding: 24px; }}
                .container {{ max-width: 540px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; border: 1px solid #e2e8f0; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }}
                .header {{ background: linear-gradient(135deg, #1e3a8a 0%, #0284c7 100%); padding: 28px 24px; text-align: center; color: #ffffff; }}
                .header h1 {{ margin: 0; font-size: 1.35rem; font-weight: 800; letter-spacing: -0.02em; }}
                .header p {{ margin: 6px 0 0; font-size: 0.85rem; opacity: 0.9; }}
                .content {{ padding: 28px 24px; color: #334155; line-height: 1.6; }}
                .otp-box {{ background: #f8fafc; border: 2px dashed #0284c7; border-radius: 10px; padding: 18px; text-align: center; margin: 22px 0; }}
                .otp-code {{ font-size: 2.2rem; font-weight: 800; letter-spacing: 0.25em; color: #0284c7; font-family: 'Courier New', monospace; margin: 0; }}
                .otp-sub {{ font-size: 0.8rem; color: #64748b; margin-top: 6px; }}
                .notice {{ font-size: 0.82rem; color: #64748b; background: #f1f5f9; padding: 12px; border-radius: 8px; margin-top: 18px; }}
                .footer {{ background: #f8fafc; border-top: 1px solid #e2e8f0; padding: 16px 24px; text-align: center; font-size: 0.75rem; color: #94a3b8; }}
              </style>
            </head>
            <body>
              <div class="container">
                <div class="header">
                  <h1>CMI CityLens Chennai</h1>
                  <p>Citizen Mobility & Road Hazard Intelligence</p>
                </div>
                <div class="content">
                  <p style="margin-top:0;">Hello <strong>{citizen_name}</strong>,</p>
                  <p>You requested a security verification code to authorize access or reset your account password.</p>
                  <div class="otp-box">
                    <div class="otp-code">{otp}</div>
                    <div class="otp-sub">Valid for 10 minutes &middot; One-time use only</div>
                  </div>
                  <div class="notice">
                    🔒 <strong>Security Warning:</strong> Never share this 6-digit code with anyone. CMI staff will never ask you for your verification code.
                  </div>
                </div>
                <div class="footer">
                  &copy; {time.strftime('%Y')} Greater Chennai Citizen Mobility Intelligence &middot; Singara Chennai
                </div>
              </div>
            </body>
            </html>
            """

            msg.attach(MIMEText(text_body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            if self.smtp_port == 465:
                # SSL Direct
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=8.0) as server:
                    server.login(self.smtp_user, self.smtp_password)
                    server.sendmail(self.smtp_from_email, [to_email], msg.as_string())
            else:
                # STARTTLS (Port 587 or 25)
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=8.0) as server:
                    server.ehlo()
                    server.starttls()
                    server.ehlo()
                    server.login(self.smtp_user, self.smtp_password)
                    server.sendmail(self.smtp_from_email, [to_email], msg.as_string())

            return True, ""
        except Exception as exc:
            return False, str(exc)

    def get_audit_log(self) -> List[Dict[str, Any]]:
        return list(reversed(self.audit_log))
