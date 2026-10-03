import './globals.css';
import { Providers } from './providers';
import { APP_NAME } from '@/lib/config';

// Static export: per-page metadata is set via <title> in each page component
// (or the default head from this layout). Server-rendered metadata() is
// unavailable without a server runtime.
export const metadata = {
  title: APP_NAME,
  description: 'EV charging wallet for Hong Kong — public web portal',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}