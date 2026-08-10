"""Stripe Connect adapter. Stripe is the payment source of truth; local rows are an auditable projection."""
from __future__ import annotations

from typing import Any

from app.core.config import settings


class MarketplacePaymentError(RuntimeError):
    pass


def _stripe():
    if not settings.stripe_secret_key:
        raise MarketplacePaymentError("STRIPE_SECRET_KEY is not configured")
    try:
        import stripe
    except ImportError as exc:  # keeps local non-payment development usable
        raise MarketplacePaymentError("stripe package is not installed") from exc
    stripe.api_key = settings.stripe_secret_key
    return stripe


def create_connect_onboarding(*, creator_email: str | None, idempotency_key: str) -> dict[str, Any]:
    stripe = _stripe()
    account = stripe.Account.create(
        type="express",
        email=creator_email,
        capabilities={"transfers": {"requested": True}},
        idempotency_key=idempotency_key,
    )
    link = stripe.AccountLink.create(
        account=account.id,
        refresh_url=settings.stripe_connect_refresh_url,
        return_url=settings.stripe_connect_return_url,
        type="account_onboarding",
    )
    return {"account_id": account.id, "onboarding_url": link.url, "account": account}


def create_template_payment_intent(*, license_id: str, amount_cents: int, currency: str, transfer_group: str) -> dict[str, Any]:
    stripe = _stripe()
    intent = stripe.PaymentIntent.create(
        amount=amount_cents,
        currency=currency,
        automatic_payment_methods={"enabled": True},
        transfer_group=transfer_group,
        metadata={"template_license_id": license_id, "kind": "marketplace_template"},
        idempotency_key=f"template-license:{license_id}:payment-intent",
    )
    return {"payment_intent_id": intent.id, "client_secret": intent.client_secret, "status": intent.status}


def create_creator_transfer(*, destination_account: str, amount_cents: int, currency: str, source_charge: str, transfer_group: str, license_id: str) -> str:
    stripe = _stripe()
    transfer = stripe.Transfer.create(
        amount=amount_cents,
        currency=currency,
        destination=destination_account,
        source_transaction=source_charge,
        transfer_group=transfer_group,
        metadata={"template_license_id": license_id, "kind": "marketplace_creator_share"},
        idempotency_key=f"template-license:{license_id}:creator-transfer",
    )
    return str(transfer.id)


def verify_webhook(payload: bytes, signature: str | None) -> Any:
    if not settings.stripe_webhook_secret:
        raise MarketplacePaymentError("STRIPE_WEBHOOK_SECRET is not configured")
    if not signature:
        raise MarketplacePaymentError("Missing Stripe-Signature")
    stripe = _stripe()
    return stripe.Webhook.construct_event(payload, signature, settings.stripe_webhook_secret)
