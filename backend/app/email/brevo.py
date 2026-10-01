"""Brevo (formerly Sendinblue) transactional email over HTTPS (port 443). Use it when the host blocks outgoing SMTP ports (Render's free tier does).
The sender address must be verified in Brevo. The API key comes only from settings (environment) and is never logged."""
import html
import logging

import httpx

from ..config import get_settings
from .base import EmailError, EmailProvider

log = logging.getLogger("dreamcast.email")
API_URL = "https://api.brevo.com/v3/smtp/email"


class BrevoEmailProvider(EmailProvider):
    name = "brevo"

    @property
    def _s(self):
        return get_settings()

    def is_configured(self) -> bool:
        return bool(self._s.brevo_api_key and self._s.smtp_from_email)

    def send(self, to: str, subject: str, text: str) -> None:
        s = self._s
        if not self.is_configured():
            raise EmailError("Brevo is not configured")
        body = "<br>".join(html.escape(line) for line in text.splitlines())      # Brevo wants an HTML body; the plain text goes along too
        payload = {"sender": {"name": s.smtp_from_name, "email": s.smtp_from_email}, "to": [{"email": to}], "subject": subject,
                   "htmlContent": f"<html><body><p>{body}</p></body></html>", "textContent": text}
        try:
            r = httpx.post(API_URL, json=payload, headers={"api-key": s.brevo_api_key, "accept": "application/json"}, timeout=s.smtp_timeout_seconds)
        except httpx.HTTPError as e:
            log.warning("email delivery failed: %s", type(e).__name__)
            raise EmailError("delivery failed") from e
        if r.status_code >= 300:
            log.warning("email delivery refused: HTTP %s", r.status_code)      # status only: the body can echo addresses
            raise EmailError("delivery refused")
