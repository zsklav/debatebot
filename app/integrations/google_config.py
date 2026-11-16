"""Server-only Google OAuth configuration; no credentials in status responses."""

from dataclasses import dataclass, field
import os
import re
from urllib.parse import urlsplit

from cryptography.fernet import Fernet

SCOPE = "https://www.googleapis.com/auth/meetings.space.readonly"
CALLBACK = "/api/integrations/google/callback"


class GoogleError(RuntimeError):
    def __init__(self, code, message, *, retryable=False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


@dataclass(frozen=True)
class GoogleConfig:
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str
    encryption_key: str = field(repr=False)

    @classmethod
    def from_env(cls):
        names = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI", "GOOGLE_TOKEN_ENCRYPTION_KEY")
        values = [os.environ.get(name, "").strip() for name in names]
        if not all(values):
            raise GoogleError("setup", "Google connection needs local OAuth setup. See docs/GOOGLE_MEET_SETUP.md")
        result = cls(*values)
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.apps\.googleusercontent\.com", result.client_id):
            raise GoogleError("setup", "Configure a Google Web application OAuth client ID")
        url = urlsplit(result.redirect_uri)
        if (url.path != CALLBACK or url.query or url.fragment or url.username or url.password or
                not url.hostname or url.scheme not in {"http", "https"} or
                (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1"})):
            raise GoogleError("setup", "Use an exact HTTPS OAuth callback URI, or HTTP on localhost/127.0.0.1")
        try:
            Fernet(result.encryption_key.encode())
        except (ValueError, TypeError):
            raise GoogleError("setup", "Configure a stable Fernet Google token encryption key") from None
        return result

    @property
    def origin(self):
        parsed = urlsplit(self.redirect_uri)
        return f"{parsed.scheme}://{parsed.netloc}"


def configuration_status():
    try:
        config = GoogleConfig.from_env()
        return {"configured": True, "scope": SCOPE, "origin": config.origin}
    except GoogleError as error:
        return {"configured": False, "scope": SCOPE, "detail": str(error)}
