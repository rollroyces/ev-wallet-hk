// Cloudflare Worker: CORS proxy for the EV Wallet HK static web portal.
//
// Browser at https://rollroyces.github.io/ev-wallet-hk/*  →
//  Worker at https://evw-api-web.evwallet.com.hk/*  →
//  Backend at BACKEND_ORIGIN (e.g. https://api.evwallet.com.hk via Cloudflare Tunnel).
//
// The Worker adds CORS headers and forwards the request verbatim. It does
// NOT verify the JWT, rate limit, or cache — the backend enforces auth on
// every protected route, and you don't want a third party caching JWT-
// bearing responses.

const ALLOWED_ORIGINS = [
  // GitHub Pages under the user's account. Update if the repo owner changes.
  'https://rollroyces.github.io',
  // Add custom-domain origins here once you set one up, e.g.
  // 'https://portal.evwallet.com.hk',
];

const ALLOW_METHODS = 'GET, POST, PUT, PATCH, DELETE, OPTIONS';
const ALLOW_HEADERS = 'Content-Type, Authorization, Accept, X-Requested-With';
const MAX_AGE = '86400'; // 24h preflight cache

function corsHeaders(origin) {
  const allowOrigin = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  return {
    'Access-Control-Allow-Origin': allowOrigin,
    'Access-Control-Allow-Methods': ALLOW_METHODS,
    'Access-Control-Allow-Headers': ALLOW_HEADERS,
    'Access-Control-Allow-Credentials': 'true',
    'Access-Control-Max-Age': MAX_AGE,
    Vary: 'Origin',
  };
}

function backendOrigin(env) {
  const o = env.BACKEND_ORIGIN;
  if (!o) {
    throw new Error(
      'BACKEND_ORIGIN is not set. Run: wrangler secret put BACKEND_ORIGIN',
    );
  }
  return o.replace(/\/+$/, '');
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get('Origin') || '';
    const isPreflight = request.method === 'OPTIONS';

    if (isPreflight) {
      // Short-circuit preflights — we don't need to talk to the backend.
      return new Response(null, {
        status: 204,
        headers: corsHeaders(origin),
      });
    }

    // Reconstruct the upstream URL. Forward path + query verbatim.
    const url = new URL(request.url);
    const target = backendOrigin(env) + url.pathname + url.search;

    // Build the upstream request. We pass the original headers (including
    // Authorization: Bearer …) but strip Host — Cloudflare sets the right
    // one for the upstream connection.
    const upstreamHeaders = new Headers(request.headers);
    upstreamHeaders.delete('Host');

    const init = {
      method: request.method,
      headers: upstreamHeaders,
      // request.body is a ReadableStream; pass it through. Workers handle
      // streaming bodies natively.
      body: request.method === 'GET' || request.method === 'HEAD' ? undefined : request.body,
      redirect: 'manual',
    };

    let upstream;
    try {
      upstream = await fetch(target, init);
    } catch (e) {
      return new Response(
        JSON.stringify({
          error: {
            code: 'UPSTREAM_UNREACHABLE',
            message: `Cannot reach backend at ${backendOrigin(env)}: ${e instanceof Error ? e.message : String(e)}`,
          },
        }),
        {
          status: 502,
          headers: {
            'Content-Type': 'application/json',
            ...corsHeaders(origin),
          },
        },
      );
    }

    // Build the response with CORS headers merged in. Stream the body.
    const responseHeaders = new Headers(upstream.headers);
    for (const [k, v] of Object.entries(corsHeaders(origin))) {
      responseHeaders.set(k, v);
    }

    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  },
};