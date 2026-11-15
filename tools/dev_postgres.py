"""Manage an isolated, password-protected development PostgreSQL cluster."""

import argparse
import os
from pathlib import Path
import secrets
import subprocess

from app.core.paths import ROOT
DATA = ROOT / ".data/postgres-dev"
PASSWORD = ROOT / ".data/postgres-dev.password"
PORT = 55432


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "stop"])
    parser.add_argument("--bin", type=Path, default=Path("C:/Program Files/PostgreSQL/17/bin"))
    args = parser.parse_args()
    pgctl = args.bin / "pg_ctl.exe"
    if not pgctl.is_file():
        parser.error("Installed PostgreSQL binaries not found; pass --bin")
    if args.command == "stop":
        subprocess.run([str(pgctl), "-D", str(DATA), "-m", "fast", "stop"], check=True)
        return
    DATA.parent.mkdir(exist_ok=True)
    if not (DATA / "PG_VERSION").exists():
        if DATA.exists() and any(DATA.iterdir()):
            parser.error("Incomplete cluster exists; inspect .data/postgres-dev before retrying")
        if not PASSWORD.exists():
            PASSWORD.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        subprocess.run([str(args.bin / "initdb.exe"), "-D", str(DATA), "-U", "debatebot",
                        "--encoding=UTF8", "--locale=C", "--auth=scram-sha-256",
                        f"--pwfile={PASSWORD}"], check=True)
    status = subprocess.run([str(pgctl), "-D", str(DATA), "status"], capture_output=True)
    if status.returncode != 0:
        subprocess.run([str(pgctl), "-D", str(DATA), "-l", str(DATA.parent / "postgres-dev.log"),
                        "-o", f"-p {PORT} -h 127.0.0.1", "-w", "start"], check=True)
    if not PASSWORD.is_file():
        parser.error("Local password file is missing; do not change an existing cluster blindly")
    import psycopg
    password = PASSWORD.read_text(encoding="utf-8").strip()
    with psycopg.connect(host="127.0.0.1", port=PORT, user="debatebot", password=password,
                         dbname="postgres", autocommit=True, connect_timeout=5) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname='debatebot'").fetchone():
            conn.execute("CREATE DATABASE debatebot")
    from dotenv import dotenv_values
    env = ROOT / ".env"
    values = dotenv_values(env) if env.exists() else {}
    if not os.environ.get("DATABASE_URL") and not values.get("DATABASE_URL"):
        with env.open("a", encoding="utf-8") as handle:
            handle.write(f"\nDATABASE_URL=postgresql://debatebot:{password}@127.0.0.1:{PORT}/debatebot\n")
        print("Configured DATABASE_URL in ignored .env; credentials not displayed.")
    print(f"Isolated PostgreSQL ready on 127.0.0.1:{PORT}. Run python -m app.storage migrate next.")


if __name__ == "__main__":
    main()
