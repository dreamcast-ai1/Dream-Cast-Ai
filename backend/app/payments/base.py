"""Payment provider interface. Routes and services only know this; Razorpay is one adapter (razorpay.py).
To switch gateway later, add another subclass and return it from registry.get_payment_provider()."""
from abc import ABC, abstractmethod
from dataclasses import dataclass


class PaymentProviderError(Exception):
    """The gateway refused or could not be reached. `message` is safe to show users; details stay in the server log."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(detail or message)
        self.message, self.detail = message, detail


@dataclass
class ProviderOrder:
    id: str
    amount_minor: int
    currency: str


@dataclass
class PaymentEvent:
    """A webhook notification normalised across gateways. kind: PAID | FAILED."""
    event_id: str
    kind: str
    order_id: str
    payment_id: str | None = None
    reason: str | None = None


class PaymentProvider(ABC):
    name: str

    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    def public_key(self) -> str:
        """The key the browser may see (never a secret)."""

    @abstractmethod
    def create_order(self, amount_minor: int, currency: str, receipt: str, notes: dict[str, str]) -> ProviderOrder: ...

    @abstractmethod
    def verify_payment(self, order_id: str, payment_id: str, signature: str) -> bool:
        """True only if the gateway's signature over (order, payment) is genuine."""

    @abstractmethod
    def verify_webhook(self, body: bytes, signature: str) -> bool: ...

    @abstractmethod
    def parse_webhook(self, body: bytes, headers: dict[str, str]) -> PaymentEvent | None:
        """None for events we don't act on."""
