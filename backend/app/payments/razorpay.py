"""Razorpay adapter. Orders are created through the REST API with httpx (no SDK needed); signatures are checked locally with HMAC-SHA256.
Secrets come only from settings (environment) and are never logged or returned."""
import hashlib
import hmac
import json
import logging

import httpx

from ..config import get_settings
from .base import PaymentEvent, PaymentProvider, PaymentProviderError, ProviderOrder

log = logging.getLogger("dreamcast.payments")
API = "https://api.razorpay.com/v1"


def _hmac_hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class RazorpayPaymentProvider(PaymentProvider):
    name = "razorpay"

    @property
    def _s(self):
        return get_settings()

    def is_configured(self) -> bool:
        return bool(self._s.razorpay_key_id and self._s.razorpay_key_secret)

    def public_key(self) -> str:
        return self._s.razorpay_key_id

    def _post_order(self, payload: dict) -> dict:
        """The one network call (separate so tests can replace it without touching the signature logic)."""
        try:
            r = httpx.post(f"{API}/orders", json=payload, auth=(self._s.razorpay_key_id, self._s.razorpay_key_secret), timeout=20)
        except httpx.HTTPError as e:
            raise PaymentProviderError("The payment service is unreachable right now. Please try again.", f"network: {type(e).__name__}")
        if r.status_code >= 400:
            log.warning("razorpay order rejected: HTTP %s", r.status_code)         # body not logged: it may echo account details
            raise PaymentProviderError("The payment service couldn't start this payment. Please try again later.", f"http {r.status_code}")
        return r.json()

    def create_order(self, amount_minor: int, currency: str, receipt: str, notes: dict[str, str]) -> ProviderOrder:
        data = self._post_order({"amount": amount_minor, "currency": currency, "receipt": receipt[:40], "notes": notes})
        if not data.get("id"):
            raise PaymentProviderError("The payment service returned an unexpected answer.", "no order id")
        return ProviderOrder(id=str(data["id"]), amount_minor=int(data.get("amount", amount_minor)), currency=data.get("currency", currency))

    def verify_payment(self, order_id: str, payment_id: str, signature: str) -> bool:
        secret = self._s.razorpay_key_secret
        if not (secret and order_id and payment_id and signature):
            return False
        return hmac.compare_digest(_hmac_hex(secret, f"{order_id}|{payment_id}".encode()), signature.strip())

    def verify_webhook(self, body: bytes, signature: str) -> bool:
        secret = self._s.razorpay_webhook_secret
        if not (secret and signature):
            return False                       # an unconfigured secret never accepts webhooks
        return hmac.compare_digest(_hmac_hex(secret, body), signature.strip())

    def parse_webhook(self, body: bytes, headers: dict[str, str]) -> PaymentEvent | None:
        try:
            data = json.loads(body)
            event = data["event"]
            entity = data["payload"]["payment"]["entity"]
        except (ValueError, KeyError, TypeError):
            return None
        kind = {"payment.captured": "PAID", "order.paid": "PAID", "payment.failed": "FAILED"}.get(event)
        order_id = entity.get("order_id")
        if not kind or not order_id:
            return None
        # Razorpay sends a unique id per delivery in X-Razorpay-Event-Id; fall back to a stable id built from the content.
        event_id = headers.get("x-razorpay-event-id") or f"{event}:{entity.get('id')}"
        return PaymentEvent(event_id=event_id, kind=kind, order_id=order_id, payment_id=entity.get("id"),
                            reason=(entity.get("error_description") or None) if kind == "FAILED" else None)
