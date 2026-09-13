"""Authentication and authorization primitives.

Public surface:
    * :func:`encode_jwt` / :func:`decode_jwt` — symmetric HS256 token ops
    * :func:`current_user` / :func:`current_admin` — FastAPI dependencies
    * :func:`verify_apple_identity_token` — Apple Sign-In verifier
    * :func:`verify_google_id_token` — Google OAuth verifier
    * :class:`AuthRouter` — FastAPI ``APIRouter`` for ``/api/v1/auth/*``

Apple/Google verifiers support a "stub" mode when their respective keys
are not configured — they raise a :class:`ConfigurationError`-flavoured
error so dev environments fail loudly instead of silently accepting any
identity.
"""

from __future__ import annotations

from evwallet.auth.jwt import decode_jwt, encode_jwt

__all__ = ["decode_jwt", "encode_jwt"]
