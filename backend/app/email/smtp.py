"""SMTP adapter (Gmail, Brevo, Mailgun SMTP, Zoho... any SMTP server). Credentials come only from settings (environment)."""
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from ..config import get_settings
from .base import EmailError, EmailProvider

log = logging.getLogger("dreamcast.email")


class SmtpEmailProvider(EmailProvider):
    name = "smtp"

    @property
    def _s(self):
        return get_settings()

    def is_configured(self) -> bool:
        s = self._s
        return bool(s.smtp_host and s.smtp_from_email and (not s.smtp_username or s.smtp_password))

    def send(self, to: str, subject: str, text: str) -> None:
        s = self._s
        if not self.is_configured():
            raise EmailError("SMTP is not configured")
        msg = EmailMessage()
        msg["From"] = formataddr((s.smtp_from_name, s.smtp_from_email))
        msg["To"], msg["Subject"] = to, subject
        msg.set_content(text)
        try:
            if s.smtp_port == 465:
                server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds, context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds)
            with server:
                if s.smtp_port != 465:
                    server.ehlo()
                    if server.has_extn("starttls"):
                        server.starttls(context=ssl.create_default_context())
                        server.ehlo()
                if s.smtp_username:
                    server.login(s.smtp_username, s.smtp_password)
                server.send_message(msg)
        except (smtplib.SMTPException, OSError) as e:
            log.warning("email delivery failed: %s", type(e).__name__)          # class only: server replies can echo addresses/credentials
            raise EmailError("delivery failed") from e
