import { NextResponse, type NextRequest } from 'next/server';
import { jwtVerify, type JWTPayload } from 'jose';
import { AUTH_COOKIE_NAME } from './lib/config';

function secret(): string {
  return process.env.EVW_JWT_SECRET || 'dev-only-secret-do-not-use-in-prod-32+';
}

function isAdminClaim(payload: JWTPayload): boolean {
  // Backend encodes is_admin on the JWT. Accept boolean or string "true".
  const v = (payload as Record<string, unknown>).is_admin;
  return v === true || v === 'true' || v === 1 || v === '1';
}

export async function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;

  // Gate /admin/* — both /admin and any sub-path.
  if (pathname.startsWith('/admin')) {
    const token = req.cookies.get(AUTH_COOKIE_NAME)?.value;
    if (!token) {
      const url = req.nextUrl.clone();
      url.pathname = '/login';
      url.searchParams.set('next', pathname + search);
      return NextResponse.redirect(url);
    }
    try {
      const { payload } = await jwtVerify(
        token,
        new TextEncoder().encode(secret()),
        { algorithms: ['HS256'] },
      );
      if (!isAdminClaim(payload)) {
        // Authenticated but not admin → 403.
        const url = req.nextUrl.clone();
        url.pathname = '/dashboard';
        url.searchParams.set('error', 'forbidden');
        return NextResponse.redirect(url);
      }
      return NextResponse.next();
    } catch {
      const url = req.nextUrl.clone();
      url.pathname = '/login';
      url.searchParams.set('next', pathname + search);
      return NextResponse.redirect(url);
    }
  }

  // Gate /dashboard, /stations, /sessions — any authenticated route other
  // than /login. Anonymous users get redirected to /login.
  const protectedPrefixes = ['/dashboard', '/stations', '/sessions'];
  if (protectedPrefixes.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
    const token = req.cookies.get(AUTH_COOKIE_NAME)?.value;
    if (!token) {
      const url = req.nextUrl.clone();
      url.pathname = '/login';
      url.searchParams.set('next', pathname + search);
      return NextResponse.redirect(url);
    }
    // We don't re-verify here — pages render a friendly "session expired"
    // state if the API rejects the token. Verifying on every navigation
    // adds a JWT verify to each page load.
    return NextResponse.next();
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/admin/:path*', '/dashboard/:path*', '/stations/:path*', '/sessions/:path*'],
};