"""Authentication and authorization primitives.

Public surface:
    * :func:`encode_jwt` / :func:`decode_jwt` — symmetric HS256 token ops
    * :func:`current_user` / :func:`current_admin` — FastAPI dependencies
    * :func:`verify_apple_identity_token` — Apple Sign-In verifier
    * :func:`verify_google_id_token` — Google OAuth verifier
    * :class:`AuthRouter` — FastAPI ``APIRouter`` for ``/api/v1/auth/*``

Apple/Google verifiers always perform real cryptographic verification
against the provider's published JWKS / OAuth keys — there is no stub
mode. If the audience (bundle id / client id) is not configured the
verifier raises :class:`evwallet.errors.ConfigurationError` so a
forgotten ``.env`` rewrite fails loudly at startup instead of silently
accepting every identity.
"""

from __future__ import annotations

from evwallet.auth.jwt import decode_jwt, encode_jwt

__all__ = ["decode_jwt", "encode_jwt"]
