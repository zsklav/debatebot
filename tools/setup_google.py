"""Prepare local OAuth settings without printing keys. Never uploads the client file."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from dotenv import dotenv_values, set_key

from app.core.paths import ROOT
from app.integrations.google_config import CALLBACK


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, help="Google Cloud Web application OAuth client JSON file")
    parser.add_argument("--origin", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)
    origin = args.origin.rstrip("/")
    url = urlsplit(origin)
    if (url.scheme not in {"http","https"} or not url.hostname or url.path or url.query or url.fragment or url.username or url.password or
            (url.scheme == "http" and url.hostname not in {"localhost","127.0.0.1"})):
        parser.error("Use an HTTPS origin or HTTP localhost/127.0.0.1 without a path")
    values = {}
    if args.credentials:
        try:
            source = json.loads(args.credentials.read_text(encoding="utf-8-sig"))
            web = source["web"]
            values = {"GOOGLE_CLIENT_ID":web["client_id"], "GOOGLE_CLIENT_SECRET":web["client_secret"]}
            if origin + CALLBACK not in web.get("redirect_uris", []):
                parser.error("Register the exact callback in the OAuth Web client before importing its JSON")
        except (OSError, ValueError, KeyError, TypeError):
            parser.error("Use the downloaded Web application OAuth client JSON")
    path = ROOT / ".env"
    existing = dotenv_values(path) if path.exists() else {}
    if not existing.get("GOOGLE_TOKEN_ENCRYPTION_KEY"):
        values["GOOGLE_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    values["GOOGLE_REDIRECT_URI"] = origin + CALLBACK
    path.touch(exist_ok=True)
    for name, value in values.items():
        set_key(str(path), name, value)
    print("Local .env updated; no secret values displayed.")
    print("Required callback: " + origin + CALLBACK)
    if not args.credentials and not existing.get("GOOGLE_CLIENT_ID"):
        print("OAuth client credentials are still missing. Follow docs/GOOGLE_MEET_SETUP.md, then use --credentials with its JSON path.")
    print("Restart the API before selecting Connect Google.")


if __name__ == "__main__":
    main()
