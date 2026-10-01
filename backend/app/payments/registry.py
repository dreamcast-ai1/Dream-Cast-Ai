from .base import PaymentProvider
from .razorpay import RazorpayPaymentProvider

_provider: PaymentProvider | None = None


def get_payment_provider() -> PaymentProvider:
    global _provider
    if _provider is None:
        _provider = RazorpayPaymentProvider()
    return _provider


def set_payment_provider(provider: PaymentProvider | None) -> None:
    """Swap the gateway (used by tests and by a future second gateway)."""
    global _provider
    _provider = provider
