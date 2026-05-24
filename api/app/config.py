"""Application settings, loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="REFMAN_", extra="ignore")

    app_base_url: str = "http://localhost:8000"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # Database
    database_url: str = "postgresql+asyncpg://refman:refman@localhost:5432/refman"

    # Object storage (S3 / SeaweedFS)
    s3_endpoint_url: str = "http://localhost:8333"
    # Endpoint the *browser* uses for presigned URLs (may differ from the in-cluster one)
    s3_public_endpoint_url: str = "http://localhost:8333"
    s3_access_key: str = "refman"
    s3_secret_key: str = "refman-secret"
    s3_region: str = "us-east-1"
    s3_bucket: str = "refman-pdfs"
    presign_expiry_seconds: int = 900  # 15 minutes

    # Sessions
    session_secret: str = "dev-only-change-me"

    # Auth (Authentik OIDC). When dev_auth is true, OIDC is bypassed with a fixed user.
    dev_auth: bool = True
    dev_user_email: str = "dev@e4e.local"
    dev_user_name: str = "Dev User"

    oidc_issuer: str = ""  # e.g. https://authentik.example.org/application/o/refman/
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    # Group sync: claim that carries the user's group names, and the group whose
    # members are treated as org admins.
    oidc_groups_claim: str = "groups"
    oidc_admin_group: str = ""
    # Where Authentik redirects back to (this API's /auth/callback)
    oidc_redirect_uri: str = "http://localhost:8000/auth/callback"
    # Where to send the user after a successful login (the SPA)
    post_login_redirect: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
