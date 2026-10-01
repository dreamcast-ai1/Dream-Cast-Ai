from .base import EmailProvider
from ..config import get_settings
from .brevo import BrevoEmailProvider
from .smtp import SmtpEmailProvider

_provider: EmailProvider | None = None


def get_email_provider() -> EmailProvider:
    global _provider
    if _provider is None:
        _provider = BrevoEmailProvider() if get_settings().email_provider == "brevo" else SmtpEmailProvider()
    return _provider


def set_email_provider(provider: EmailProvider | None) -> None:
    """Swap the email service (used by tests and by a future second provider)."""
    global _provider
    _provider = provider
