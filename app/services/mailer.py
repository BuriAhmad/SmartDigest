"""MailerService — sends digest emails via Resend API.

If RESEND_API_KEY is not set, logs the email content (dev mode).
"""

import asyncio
import structlog
import resend

from app.config import get_settings

logger = structlog.get_logger()


async def send_digest_email(
    to_email: str,
    subject: str,
    html_body: str,
) -> bool:
    """Send an HTML email via Resend. Returns True on success."""
    settings = get_settings()

    if not settings.RESEND_API_KEY:
        logger.info(
            "mailer.dev_mode",
            to=to_email,
            subject=subject,
            body_length=len(html_body),
        )
        logger.info("mailer.email_content", html=html_body[:500])
        return True

    resend.api_key = settings.RESEND_API_KEY

    try:
        params = {
            "from": settings.RESEND_FROM_EMAIL,
            "to": [to_email],
            "subject": subject,
            "html": html_body,
        }
        email_response = await asyncio.to_thread(resend.Emails.send, params)
        logger.info(
            "mailer.sent",
            to=to_email,
            subject=subject,
            resend_id=email_response.get("id") if isinstance(email_response, dict) else str(email_response),
        )
        return True
    except Exception as exc:
        logger.error("mailer.failed", to=to_email, error=str(exc))
        return False
