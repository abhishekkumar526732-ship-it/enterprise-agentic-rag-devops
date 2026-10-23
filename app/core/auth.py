import logging
from typing import List, Optional
from pydantic import BaseModel
from fastapi import Depends, HTTPException, Header, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

security_bearer = HTTPBearer(auto_error=False)


class User(BaseModel):
    id: str
    username: str
    email: Optional[str] = None
    roles: List[str] = []

    def has_role(self, role: str) -> bool:
        return role in self.roles or "admin" in self.roles or "hr_admin" in self.roles


# In-memory JWKS cache for Keycloak
_jwks_cache = None


def get_keycloak_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache is None:
        try:
            import httpx
            jwks_url = f"{settings.keycloak_server_url}/realms/{settings.keycloak_realm}/protocol/openid-connect/certs"
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(jwks_url)
                resp.raise_for_status()
                _jwks_cache = resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch Keycloak JWKS: {e}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Identity provider authentication service unavailable."
            )
    return _jwks_cache


async def get_current_user(
    auth: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
    x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_user_id: Optional[str] = Header(default="emp-1001", alias="X-User-Id"),
) -> User:
    """
    Validates identity from Keycloak Bearer token in production,
    or falls back to role/key headers in development mode.
    """
    # 1. Dev Mode Bypass (when Keycloak is disabled)
    if not settings.keycloak_enabled:
        roles = ["employee"]
        if x_admin_key == settings.admin_api_key or x_user_role in {"admin", "hr_admin"}:
            roles.extend(["admin", "hr_admin"])
        elif x_user_role:
            roles.append(x_user_role)
            
        return User(
            id=x_user_id,
            username=f"user_{x_user_id}",
            email=f"{x_user_id}@company.internal",
            roles=list(set(roles)),
        )

    # 2. Production Keycloak OIDC JWT Validation
    if not auth or not auth.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth.credentials
    try:
        from jose import jwt
        jwks = get_keycloak_jwks()
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        
        rsa_key = {}
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                rsa_key = {
                    "kty": key.get("kty"),
                    "kid": key.get("kid"),
                    "use": key.get("use"),
                    "n": key.get("n"),
                    "e": key.get("e")
                }
                break

        if not rsa_key:
            raise HTTPException(status_code=401, detail="Invalid token key identifier (kid)")

        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=["RS256"],
            issuer=f"{settings.keycloak_server_url}/realms/{settings.keycloak_realm}",
            options={"verify_aud": False},
        )

        user_id = payload.get("sub", "unknown")
        username = payload.get("preferred_username", user_id)
        email = payload.get("email")

        # Extract realm & client roles
        roles = []
        realm_access = payload.get("realm_access", {})
        roles.extend(realm_access.get("roles", []))
        resource_access = payload.get("resource_access", {}).get(settings.keycloak_client_id, {})
        roles.extend(resource_access.get("roles", []))

        return User(id=user_id, username=username, email=email, roles=roles)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"JWT Validation failed: {exc}")
        raise HTTPException(status_code=401, detail=f"Invalid Keycloak token: {exc}")


def require_role(required_role: str):
    """FastAPI Dependency for Role-Based Access Control."""
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if not current_user.has_role(required_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: Requires '{required_role}' role permissions.",
            )
        return current_user
    return role_checker
