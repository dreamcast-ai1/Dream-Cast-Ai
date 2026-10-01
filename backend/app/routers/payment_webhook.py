from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import payments

router = APIRouter(prefix="/api/payments", tags=["payments"])


@router.post("/razorpay/webhook")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    """Server-to-server call from Razorpay. No login: the request is authenticated by its HMAC signature instead,
    and anything without a valid signature is rejected before it is looked at."""
    body = await request.body()
    return payments.handle_webhook(db, body, request.headers.get("x-razorpay-signature", ""), {k.lower(): v for k, v in request.headers.items()})
