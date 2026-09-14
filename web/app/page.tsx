import { redirect } from 'next/navigation';
import { getCurrentUser } from '@/lib/auth';

// Landing page: if logged in, go to dashboard; else go to signup.
// New visitors land on signup first — the more common path for a
// first-time visitor. Returning users can click "Sign in" from there.
export default async function HomePage() {
  const user = await getCurrentUser();
  if (user) {
    redirect('/dashboard');
  }
  redirect('/signup');
}