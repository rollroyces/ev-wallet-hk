'use client';

/**
 * Client-side Topup button + dialog for the web dashboard.
 *
 * Flow:
 *   1. User picks an amount (HK$50-10000).
 *   2. On "Top up" -> POST /api/v1/wallet/topup/stripe/intent with the
 *      amount, receives a `client_secret`.
 *   3. (MVP) Just surface the PaymentIntent ID in the UI; embedding Stripe.js
 *      to collect card details in-browser is a follow-up.
 *      Once `@stripe/stripe-js` + `stripe.confirmCardPayment()` are wired
 *      up, this becomes a single confirmCardPayment() call.
 *
 * On 503 BackendUnavailableError, surface a friendly message ("Stripe not
 * configured — please contact support or try Apple Pay from the mobile app").
 */

import { useState } from 'react';

const PRESET_AMOUNTS = [100, 200, 500, 1000];

export function TopupButton(): React.JSX.Element {
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState<number>(200);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  async function handleTopup() {
    setSubmitting(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await fetch('/api/v1/wallet/topup/stripe/intent', {
        method: 'POST',
        credentials: 'include', // send the session cookie
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount_hkd: String(amount) }),
      });
      if (!res.ok) {
        // Try to extract the canonical IDPError envelope.
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          if (body?.detail?.message) detail = body.detail.message;
          else if (body?.detail) detail = JSON.stringify(body.detail);
          else if (body?.error?.message) detail = body.error.message;
        } catch {
          // body wasn't JSON; keep the status text.
        }
        throw new Error(detail);
      }
      const data = (await res.json()) as {
        payment_intent_id: string;
        client_secret: string;
        amount_hkd: string;
      };
      setSuccess(
        `PaymentIntent created (id=${data.payment_intent_id}). ` +
          'Card collection requires Stripe.js — wire that up next.',
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
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
          onClick={() => !submitting && setOpen(false)}
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
            {success ? (
              <div
                role="status"
                style={{
                  marginTop: '1rem',
                  padding: '0.75rem',
                  borderRadius: '0.5rem',
                  background: '#dcfce7',
                  color: '#14532d',
                  fontSize: '0.85rem',
                }}
              >
                {success}
              </div>
            ) : null}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginTop: '1.25rem' }}>
              <button
                type="button"
                onClick={() => setOpen(false)}
                disabled={submitting}
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
              <button
                type="button"
                onClick={handleTopup}
                disabled={submitting || amount < 50 || amount > 10000}
                style={{
                  padding: '0.5rem 1rem',
                  borderRadius: '0.5rem',
                  border: 'none',
                  background: '#0f172a',
                  color: '#fff',
                  fontWeight: 600,
                  cursor: submitting ? 'wait' : 'pointer',
                }}
              >
                {submitting ? 'Creating intent…' : 'Top up'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}