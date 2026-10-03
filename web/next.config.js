/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // Static export so the app can be hosted on GitHub Pages (no Node runtime).
  // Every page becomes a static HTML file; data fetching moves to the client.
  output: 'export',
  trailingSlash: true,

  // GitHub Pages serves /ev-wallet-hk/* — set the base path so all generated
  // asset URLs include the repo name. Override with NEXT_PUBLIC_BASE_PATH=''.
  basePath: process.env.NEXT_PUBLIC_BASE_PATH || '/ev-wallet-hk',

  images: {
    // next/image optimizer requires a server; on static export we just emit
    // raw <img> tags. Provider station logos are still allowed via remotePatterns.
    unoptimized: true,
    remotePatterns: [
      { protocol: 'https', hostname: '**.hkev.com.hk' },
      { protocol: 'https', hostname: '**.clp.com.hk' },
      { protocol: 'https', hostname: '**.shell.com' },
      { protocol: 'https', hostname: '**.tesla.com' },
      { protocol: 'https', hostname: 'logo.clearbit.com' },
    ],
  },

  async redirects() {
    return [];
  },
};

module.exports = nextConfig;