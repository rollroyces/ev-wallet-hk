import { redirect } from 'next/navigation';
import { getCurrentUser } from '@/lib/auth';

// Landing page: if logged in, go to dashboard; else go to login.
// Redirect runs server-side so JS-disabled clients also follow it.
export default async function HomePage() {
  const user = await getCurrentUser();
  if (user) {
    redirect('/dashboard');
  }
  redirect('/login');
}