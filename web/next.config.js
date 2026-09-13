/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Server actions are needed for form submissions (login, admin actions).
  // Auth uses HTTP-only cookies; CSRF tokens handled server-side.
  experimental: {
    serverActions: {
      bodySizeLimit: '1mb',
    },
  },
  images: {
    remotePatterns: [
      // Allow Next/Image to load provider station logos from common sources.
      {
        protocol: 'https',
        hostname: '**.hkev.com.hk',
      },
      {
        protocol: 'https',
        hostname: '**.clp.com.hk',
      },
      {
        protocol: 'https',
        hostname: '**.shell.com',
      },
      {
        protocol: 'https',
        hostname: '**.tesla.com',
      },
      {
        protocol: 'https',
        hostname: 'logo.clearbit.com',
      },
    ],
  },
  async redirects() {
    return [];
  },
};

module.exports = nextConfig;