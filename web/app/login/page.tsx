import { LoginForm } from './login-form';
import { APP_NAME } from '@/lib/config';

export const metadata = {
  title: `Sign in — ${APP_NAME}`,
};

export default function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  return (
    <main style={{ maxWidth: 420, margin: '4rem auto', padding: '0 1rem' }}>
      <div className="card" style={{ padding: '1.5rem' }}>
        <h1 style={{ marginBottom: '0.25rem' }}>Sign in</h1>
        <p className="muted" style={{ marginBottom: '1.25rem' }}>
          EV Wallet HK — admin & desktop portal
        </p>
        <LoginForm searchParams={searchParams} />
      </div>
    </main>
  );
}