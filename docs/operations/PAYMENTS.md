# Payment setup

Topups work end-to-end across web (Stripe.js) and mobile (Apple Pay,
Google Pay, Stripe). To enable real charges:

## Stripe (web + Android)

1. Sign up at https://dashboard.stripe.com (HK entity if applicable).
2. Dashboard → Developers → API keys → copy the test-mode pair:
   - `pk_test_…` (publishable, ship to client)
   - `sk_test_…` (secret, server only)
3. For live mode, repeat with `pk_live_…` / `sk_live_…` after activating
   your account (HK business docs + bank account).
4. Set in `.env` (or compose env):
   ```
   EVW_STRIPE_SECRET_KEY=sk_test_…
   EVW_STRIPE_PUBLISHABLE_KEY=pk_test_…
   EVW_STRIPE_WEBHOOK_SECRET=whsec_…  # see step 5
   ```
5. Webhook setup for local dev:
   ```
   stripe listen --forward-to localhost:8001/api/v1/payments/stripe/webhook
   ```
   Copy the printed `whsec_…` into `EVW_STRIPE_WEBHOOK_SECRET`. In
   production, register the webhook in the Stripe dashboard pointing at
   `https://api.evwallet.hk/api/v1/payments/stripe/webhook` and copy the
   generated signing secret.

## Apple Pay (iOS only)

1. Apple Developer account (HK$99/yr). Create an App ID
   `com.evwallet.hk` with the **Apple Pay** capability enabled.
2. Create a **Merchant Identifier** (e.g. `merchant.com.evwallet.hk`) in
   the Certificates, Identifiers & Profiles section.
3. Create a **Payment Processing Certificate** for that merchant id.
4. Set in `.env`:
   ```
   EVW_APPLE_PAY_MERCHANT_ID=merchant.com.evwallet.hk
   ```
5. The mobile `app/topup.tsx` calls `POST /api/v1/wallet/topup/apple/intent`
   first to get the merchant id, then `presentApplePayAsync()` with it.
   `expo-apple-pay` (installed in v0.4.0) needs EAS Build to compile
   — won't work in Expo Go.

## Google Pay (Android)

1. Google Pay business console: https://pay.google.com/business/console
2. Enable Google Pay API, set gateway = Stripe (recommended) or your
   processor of choice. Note your `gatewayMerchantId`.
3. Set in `.env`:
   ```
   EVW_GOOGLE_PAY_MERCHANT_ID=…    # future field; not required for Stripe gateway
   ```
4. `expo-google-pay` (installed in v0.4.0) needs EAS Build to compile.

## Verifying the integration locally

After Stripe CLI is listening, the roundtrip is:

1. Web: dashboard → "+ Top up wallet" → enter HK$200 → fill in Stripe
   Elements with test card `4242 4242 4242 4242` (any future date, any CVC).
2. Stripe Elements confirms → 3DS challenge (use `4242 4242 4242 4242` again
   to skip) → returns to dashboard with new balance.
3. `stripe listen` shows the `payment_intent.succeeded` event.
4. Backend `/api/v1/payments/stripe/webhook` validates signature, posts
   a `topup_stripe` ledger entry, increments `wallets.available_credits`.
5. Reload the dashboard — balance reflects the topup.

If the wallet doesn't credit:
- Check uvicorn logs for `stripe webhook error` lines.
- Verify the webhook signing secret matches what `stripe listen` printed.
- Verify the PaymentIntent `metadata.credit_amount_hkd` and `wallet_user_id`
  are set (server-side bug otherwise).