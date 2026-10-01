"""Email provider interface. Services only know this; SMTP is one adapter (smtp.py). To use another service later
(SendGrid, Resend, SES...), add a subclass and return it from registry.get_email_provider()."""
from abc import ABC, abstractmethod


class EmailError(Exception):
    """The message could not be sent. Details stay in the server log (never the message body, which holds codes/links)."""


class EmailProvider(ABC):
    name: str

    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    def send(self, to: str, subject: str, text: str) -> None:
        """Sends a plain-text message or raises EmailError."""
