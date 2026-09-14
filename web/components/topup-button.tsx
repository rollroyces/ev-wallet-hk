'use client';

/**
 * Client-side Topup button + dialog for the web dashboard.
 *
 * Flow (full):
 *   1. User picks an amount (HK$50-10000).
 *   2. On "Top up" -> POST /api/v1/wallet/topup/stripe/intent to create the
 *      PaymentIntent server-side, receiving a `client_secret`.
 *   3. Lazy-load Stripe.js via loadStripe(publishable_key) (fetched from
 *      the public /api/v1/config/stripe endpoint).
 *   4. Mount Stripe Elements (PaymentElement) for card collection.
 *   5. Call stripe.confirmPayment({ elements, confirmParams: { return_url } })
 *      — Stripe redirects to return_url on success, the webhook fires,
 *      credits the wallet via /wallet/topup?source=stripe.
 *
 * On 503 BackendUnavailableError, fall back to "use Stripe on mobile"
 * guidance.
 */

import { loadStripe, type Stripe } from '@stripe/stripe-js';
import { Elements, PaymentElement, useElements, useStripe } from '@stripe/react-stripe-js';
import { useEffect, useMemo, useState } from 'react';

const PRESET_AMOUNTS = [100, 200, 500, 1000];

// Strip query string + hash from window.location for the return_url so the
// user lands back on the same page after a 3DS challenge.
function returnUrl(): string {
  if (typeof window === 'undefined') return '';
  return `${window.location.origin}${window.location.pathname}`;
}

export function TopupButton(): React.JSX.Element {
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState<number>(200);
  const [error, setError] = useState<string | null>(null);
  const [pk, setPk] = useState<string | null | undefined>(undefined);

  // Fetch the publishable key on first render.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const r = await fetch('/api/v1/config/stripe');
        const data = (await r.json()) as { publishable_key: string | null };
        if (!cancelled) setPk(data.publishable_key);
      } catch (e) {
        if (!cancelled) setError(`Couldn't reach Stripe config endpoint: ${e}`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Lazily initialise Stripe.js — only when we have a publishable key AND
  // the dialog has been opened.
  const stripePromise = useMemo<Promise<Stripe | null> | null>(() => {
    if (!pk || !open) return null;
    return loadStripe(pk);
  }, [pk, open]);

  // Re-open reset: close any prior state.
  useEffect(() => {
    if (open) setError(null);
  }, [open]);

  if (pk === undefined) {
    return (
      <button type="button" disabled style={{ padding: '0.5rem 1rem', borderRadius: '0.5rem', background: '#94a3b8', color: '#fff', border: 'none' }}>
        Loading…
      </button>
    );
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        style={{
          background: '#0f172a',
          color: '#fff',
          padding: '0.5rem 1rem',
          borderRadius: '0.5rem',
          border: 'none',
          fontWeight: 600,
          cursor: 'pointer',
          marginTop: '0.5rem',
        }}
      >
        + Top up wallet
      </button>

      {open ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Top up wallet"
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.5)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '1rem',
            zIndex: 100,
          }}
          onClick={() => setOpen(false)}
        >
          <div
            className="card"
            style={{
              background: '#fff',
              padding: '1.5rem',
              borderRadius: '0.75rem',
              maxWidth: 480,
              width: '100%',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 style={{ marginTop: 0 }}>Top up wallet</h2>

            <label style={{ display: 'block', marginBottom: '0.5rem' }} htmlFor="topup-amount">
              Amount (HKD)
            </label>
            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              <input
                id="topup-amount"
                type="number"
                min={50}
                max={10000}
                value={amount}
                onChange={(e) => setAmount(Number(e.target.value))}
                style={{
                  flex: 1,
                  padding: '0.5rem',
                  fontSize: '1.25rem',
                  fontWeight: 600,
                  borderRadius: '0.375rem',
                  border: '1px solid var(--border)',
                }}
              />
              <span style={{ color: 'var(--muted-foreground)', fontWeight: 600 }}>HKD</span>
            </div>

            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginTop: '0.75rem' }}>
              {PRESET_AMOUNTS.map((preset) => (
                <button
                  key={preset}
                  type="button"
                  onClick={() => setAmount(preset)}
                  style={{
                    padding: '0.4rem 0.75rem',
                    borderRadius: '0.375rem',
                    border: '1px solid var(--border)',
                    background: amount === preset ? '#0f172a' : '#fff',
                    color: amount === preset ? '#fff' : '#0f172a',
                    cursor: 'pointer',
                    fontWeight: 600,
                  }}
                >
                  HK${preset}
                </button>
              ))}
            </div>

            {pk === null ? (
              <div
                role="alert"
                style={{
                  marginTop: '1rem',
                  padding: '0.75rem',
                  borderRadius: '0.5rem',
                  background: '#fef9c3',
                  color: '#854d0e',
                  fontSize: '0.85rem',
                }}
              >
                Stripe isn't configured on the server (no EVW_STRIPE_PUBLISHABLE_KEY). Use the
                mobile app's Apple Pay or Google Pay instead, or contact support.
              </div>
            ) : stripePromise ? (
              <Elements
                stripe={stripePromise}
                options={{
                  mode: 'payment',
                  amount: amount * 100, // cents
                  currency: 'hkd',
                  appearance: { theme: 'stripe' },
                }}
              >
                <TopupForm
                  amount={amount}
                  onError={setError}
                  onSuccess={() => setOpen(false)}
                  returnUrl={returnUrl()}
                />
              </Elements>
            ) : null}

            {error ? (
              <div
                role="alert"
                style={{
                  marginTop: '1rem',
                  padding: '0.75rem',
                  borderRadius: '0.5rem',
                  background: '#fee2e2',
                  color: '#7f1d1d',
                  fontSize: '0.85rem',
                }}
              >
                {error}
              </div>
            ) : null}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginTop: '1.25rem' }}>
              <button
                type="button"
                onClick={() => setOpen(false)}
                style={{
                  padding: '0.5rem 1rem',
                  borderRadius: '0.5rem',
                  border: '1px solid var(--border)',
                  background: '#fff',
                  cursor: 'pointer',
                }}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

interface TopupFormProps {
  amount: number;
  onError: (msg: string) => void;
  onSuccess: () => void;
  returnUrl: string;
}

function TopupForm({ amount, onError, onSuccess, returnUrl }: TopupFormProps): React.JSX.Element {
  const stripe = useStripe();
  const elements = useElements();
  const [submitting, setSubmitting] = useState(false);
  const [intentId, setIntentId] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!stripe || !elements) return;
    setSubmitting(true);
    onError('');

    try {
      // 1. Create the PaymentIntent on the server
      const r = await fetch('/api/v1/wallet/topup/stripe/intent', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount_hkd: String(amount) }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => null);
        const msg = body?.detail?.message ?? `HTTP ${r.status}`;
        throw new Error(msg);
      }
      const { client_secret, payment_intent_id } = (await r.json()) as {
        client_secret: string;
        payment_intent_id: string;
      };
      setIntentId(payment_intent_id);

      // 2. Confirm with Stripe Elements. The `return_url` receives the user
      // back after any 3DS challenge; the webhook fires on success.
      const { error } = await stripe.confirmPayment({
        elements,
        clientSecret: client_secret,
        confirmParams: {
          return_url: `${returnUrl}?topup=${payment_intent_id}`,
        },
        redirect: 'if_required',
      });
      if (error) {
        onError(error.message ?? 'Payment failed');
        return;
      }
      // No redirect needed (no 3DS) — payment confirmed, webhook will credit
      // the wallet shortly. Reload the page so the new balance shows.
      onSuccess();
      if (typeof window !== 'undefined') window.location.reload();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} style={{ marginTop: '1rem' }}>
      <PaymentElement />
      <button
        type="submit"
        disabled={!stripe || submitting || amount < 50 || amount > 10000}
        style={{
          marginTop: '1rem',
          width: '100%',
          padding: '0.6rem 1rem',
          borderRadius: '0.5rem',
          border: 'none',
          background: '#0f172a',
          color: '#fff',
          fontWeight: 600,
          cursor: submitting ? 'wait' : 'pointer',
        }}
      >
        {submitting ? 'Processing…' : `Pay HK$${amount}`}
      </button>
      {intentId ? (
        <p className="muted" style={{ fontSize: '0.7rem', marginTop: '0.5rem', wordBreak: 'break-all' }}>
          PaymentIntent: {intentId}
        </p>
      ) : null}
    </form>
  );
}