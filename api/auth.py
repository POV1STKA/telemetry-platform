import time
import logging
from typing import Annotated

import httpx
from jose import jwt, JWTError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

import config

log = logging.getLogger(__name__)

_jwks: dict = {"keys": [], "expires_at": 0.0}
_JWKS_TTL = 3600.0  # Cache TTL in seconds

security = HTTPBearer(
    auto_error=False,
    description="Auth0 JWT Access Token. Format: Bearer <token>",
)


async def _fetch_jwks() -> list[dict]:
    url = f"https://{config.AUTH0_DOMAIN}/.well-known/jwks.json"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    keys = resp.json().get("keys", [])
    _jwks["keys"] = keys
    _jwks["expires_at"] = time.monotonic() + _JWKS_TTL
    return keys


async def _get_jwks() -> list[dict]:
    if _jwks["keys"] and time.monotonic() < _jwks["expires_at"]:
        return _jwks["keys"]
    return await _fetch_jwks()


async def _find_rsa_key(kid: str) -> dict | None:
    keys = await _get_jwks()
    key = next((k for k in keys if k.get("kid") == kid), None)
    if key:
        return key
    _jwks["expires_at"] = 0.0
    keys = await _fetch_jwks()
    return next((k for k in keys if k.get("kid") == kid), None)


async def verify_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> dict:
    if not config.AUTH0_DOMAIN or not config.AUTH0_AUDIENCE:
        return {"sub": "dev-user", "dev_mode": True}

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization token is missing",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    try:
        header = jwt.get_unverified_header(token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token format",
            headers={"WWW-Authenticate": "Bearer"},
        )

    kid = header.get("kid")
    if not kid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token header is missing key identifier",
            headers={"WWW-Authenticate": "Bearer"},
        )

    rsa_key = await _find_rsa_key(kid)
    if not rsa_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Signing key '{kid}' not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=["RS256"],
            audience=config.AUTH0_AUDIENCE,
            issuer=f"https://{config.AUTH0_DOMAIN}/",
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token validation failed: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload


CurrentUser = Annotated[dict, Depends(verify_token)]
