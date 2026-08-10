# Template Marketplace

## Settlement lifecycle

`checkout_pending` → verified Stripe `payment_intent.succeeded` → `payment_succeeded` → `applied` → `rendering` → `fulfilled`.

The webhook creates immutable pending ledger rows for gross sale, creator payable (70%), and platform commission (30%). Only a committed `RenderJob.COMPLETED` queues the creator transfer. The transfer uses the license ID as Stripe and local idempotency keys, so retries cannot create a second payout. Stripe processing fees are borne by the platform under the separate-charge-and-transfer model and should be added as a separate fee ledger event from Stripe balance transactions during reconciliation.

## Private template payload

`POST /api/v1/marketplace/templates` accepts `private_payload`, encrypts it with `MARKETPLACE_TEMPLATE_ENCRYPTION_KEY`, and never returns it. The expected worker-only convention is:

```json
{
  "render_settings": {
    "confirmed_timeline": { "source_asset_id": "buyer-owned-asset-id", "tracks": [] },
    "color_lut": { "lut_key": "private/luts/creator-look.cube", "intensity": 0.9 },
    "ai_prompt": "private creator prompt"
  }
}
```

The render worker decrypts this payload only in memory. The browser receives only a `marketplace_blackbox` license reference, never the Timeline, LUT location, or prompt. This makes the template a cloud-only black-box workflow for free users; do not add an endpoint that returns `encrypted_payload` or a decrypted render plan.

## Required Stripe endpoints

- `POST /api/v1/marketplace/connect/onboarding`
- `POST /api/v1/marketplace/templates/{slug}/checkout`
- `POST /api/v1/marketplace/stripe/webhook`
- `POST /api/v1/marketplace/licenses/{license_id}/apply`
- `GET /api/v1/marketplace/creators/{creator_id}/dashboard`

Configure the Stripe webhook to deliver `payment_intent.succeeded`, `payment_intent.payment_failed`, and `account.updated` to the webhook endpoint. TLS termination is mandatory in production.
