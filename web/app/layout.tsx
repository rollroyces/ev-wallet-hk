import type { Metadata } from 'next';
import './globals.css';
import { Providers } from './providers';
import { APP_NAME } from '@/lib/config';

export const metadata: Metadata = {
  title: APP_NAME,
  description: 'EV charging wallet for Hong Kong — admin + desktop portal',
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