"""Session snapshots and completed/partial run history, with optimistic updates."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import os
from app.core.paths import MIGRATIONS, ROOT
from threading import RLock

from app.debate.engine import Debate
from app.core.persistence import SessionConflict, StorageUnavailable, jsonb
from app.integrations.persistence import MemoryIntegrationRepository, PostgresIntegrationRepository


def snapshot(debate):
    return {"persona_key": debate.persona_key, "model": debate.model,
            "messages": deepcopy(debate.messages), "total_tokens": debate.total_tokens}


def restore(data, client):
    debate = Debate(client, data["persona_key"], model=data["model"])
    debate.messages = deepcopy(data["messages"])
    debate.total_tokens = data["total_tokens"]
    return debate


def notebook_record(row, mode):
    if row is None:
        return None
    result = deepcopy(row)
    result.pop("owner_id", None)
    result["id"] = str(result["id"])
    for key in ("created_at", "updated_at"):
        if isinstance(result.get(key), datetime):
            result[key] = result[key].isoformat()
    result["storage"] = mode
    return result


class MemoryRepository(MemoryIntegrationRepository):
    mode = "memory"

    def __init__(self):
        self.sessions, self.runs = {}, {}
        self.users, self.tokens = {}, {}
        self.notebooks, self.notebook_edits = {}, {}
        self.google_connections, self.meet_import_jobs = {}, {}
        self.lock = RLock()

    def create_session(self, sid, debate, owner_id=None):
        with self.lock:
            if sid in self.sessions:
                raise SessionConflict("Session already exists")
            self.sessions[sid] = {**snapshot(debate), "revision": 0, "owner_id": owner_id}

    def load_session(self, sid, client, owner_id=None):
        with self.lock:
            data = self.sessions.get(sid)
            return (restore(data, client), data["revision"]) if data and data["owner_id"] == owner_id else None

    def update_session(self, sid, debate, revision, owner_id=None):
        with self.lock:
            if sid not in self.sessions or self.sessions[sid]["revision"] != revision or self.sessions[sid]["owner_id"] != owner_id:
                raise SessionConflict("Session changed; reload before retrying")
            self.sessions[sid] = {**snapshot(debate), "revision": revision + 1, "owner_id": owner_id}

    def session_count(self, owner_id=None):
        with self.lock:
            return sum(row["owner_id"] == owner_id for row in self.sessions.values())

    def save_run(self, run_id, kind, input_text, result, owner_id=None):
        with self.lock:
            self.runs[run_id] = deepcopy({"id": run_id, "kind": kind, "input_text": input_text,
                "result": result, "created_at": datetime.now(timezone.utc).isoformat(), "owner_id": owner_id})

    def get_run(self, run_id, owner_id=None):
        with self.lock:
            row = self.runs.get(run_id)
            return deepcopy(row) if row and row["owner_id"] == owner_id else None

    def create_user(self, user_id, username, password_hash):
        with self.lock:
            if username in self.users:
                return False
            self.users[username] = {"id": user_id, "username": username, "password_hash": password_hash}
            return True

    def create_notebook(self, identifier, document, owner_id=None):
        with self.lock:
            if identifier in self.notebooks:
                raise SessionConflict("Notebook already exists")
            now = datetime.now(timezone.utc).isoformat()
            self.notebooks[identifier] = deepcopy({"id": identifier, "owner_id": owner_id,
                "document": document, "revision": 0, "created_at": now, "updated_at": now})
            self.notebook_edits[identifier] = [{"revision": 0, "created_at": now,
                "changes": [{"field": "created", "source_hash": document["source_hash"]}]}]

    def get_notebook(self, identifier, owner_id=None):
        with self.lock:
            row = self.notebooks.get(identifier)
            return notebook_record(row, self.mode) if row and row["owner_id"] == owner_id else None

    def list_notebooks(self, owner_id=None):
        with self.lock:
            rows = sorted((row for row in self.notebooks.values() if row["owner_id"] == owner_id),
                          key=lambda row: row["updated_at"], reverse=True)[:20]
            return [{"id": row["id"], "title": row["document"]["title"],
                     "revision": row["revision"], "updated_at": row["updated_at"]} for row in rows]

    def update_notebook(self, identifier, document, revision, changes, owner_id=None):
        with self.lock:
            row = self.notebooks.get(identifier)
            if not row or row["owner_id"] != owner_id or row["revision"] != revision:
                raise SessionConflict("Notebook changed")
            now = datetime.now(timezone.utc).isoformat()
            row.update(document=deepcopy(document), revision=revision + 1, updated_at=now)
            self.notebook_edits[identifier].append({"revision": revision + 1, "created_at": now,
                                                   "changes": deepcopy(changes)})

    def notebook_history(self, identifier, owner_id=None):
        with self.lock:
            if self.get_notebook(identifier, owner_id) is None:
                return []
            return deepcopy(self.notebook_edits[identifier][-20:][::-1])

    def user_by_name(self, username):
        with self.lock:
            return deepcopy(self.users.get(username))

    def save_auth(self, token_hash, user_id, expires_at):
        with self.lock:
            self.tokens[token_hash] = {"user_id": user_id, "expires_at": expires_at}

    def auth_user(self, token_hash, now):
        with self.lock:
            token = self.tokens.get(token_hash)
            if token and token["expires_at"] > now:
                return next(({"id": u["id"], "username": u["username"]} for u in self.users.values() if u["id"] == token["user_id"]), None)
            return None

    def revoke_auth(self, token_hash):
        with self.lock:
            self.tokens.pop(token_hash, None)


class PostgresRepository(PostgresIntegrationRepository):
    mode = "postgresql"

    def __init__(self, dsn):
        self.dsn = dsn

    def _execute(self, operation):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as error:
            raise StorageUnavailable("Install requirements/storage.txt") from error
        try:
            with psycopg.connect(self.dsn, connect_timeout=5, row_factory=dict_row,
                                 options="-c statement_timeout=10000") as connection:
                return operation(connection)
        except psycopg.Error as error:
            # Never put a credential-bearing DSN or provider exception into an API response.
            raise StorageUnavailable("PostgreSQL unavailable or schema not migrated") from error

    def migrate(self):
        def apply(connection):
            connection.execute("SELECT pg_advisory_xact_lock(81234716)")
            for path in sorted(MIGRATIONS.glob("[0-9]*.sql")):
                connection.execute(path.read_text(encoding="utf-8"), prepare=False)
        self._execute(apply)

    def create_session(self, sid, debate, owner_id=None):
        data = snapshot(debate)
        self._execute(lambda conn: conn.execute(
            "INSERT INTO debate_sessions(id,persona_key,model,messages,total_tokens,owner_id) VALUES (%s,%s,%s,%s,%s,%s)",
            (sid, data["persona_key"], data["model"], jsonb(data["messages"]), data["total_tokens"], owner_id)))

    def load_session(self, sid, client, owner_id=None):
        data = self._execute(lambda conn: conn.execute("SELECT * FROM debate_sessions WHERE id=%s AND owner_id IS NOT DISTINCT FROM %s", (sid, owner_id)).fetchone())
        return (restore(data, client), data["revision"]) if data else None

    def update_session(self, sid, debate, revision, owner_id=None):
        data = snapshot(debate)
        count = self._execute(lambda conn: conn.execute(
            "UPDATE debate_sessions SET messages=%s,total_tokens=%s,revision=revision+1,updated_at=now() "
            "WHERE id=%s AND revision=%s AND owner_id IS NOT DISTINCT FROM %s", (jsonb(data["messages"]), data["total_tokens"], sid, revision, owner_id)).rowcount)
        if count != 1:
            raise SessionConflict("Session changed; reload before retrying")

    def session_count(self, owner_id=None):
        return self._execute(lambda conn: conn.execute("SELECT count(*) AS n FROM debate_sessions WHERE owner_id IS NOT DISTINCT FROM %s", (owner_id,)).fetchone()["n"])

    def save_run(self, run_id, kind, input_text, result, owner_id=None):
        self._execute(lambda conn: conn.execute(
            "INSERT INTO analysis_runs(id,kind,input_text,result,owner_id) VALUES (%s,%s,%s,%s,%s)",
            (run_id, kind, input_text, jsonb(result), owner_id)))

    def get_run(self, run_id, owner_id=None):
        row = self._execute(lambda conn: conn.execute("SELECT * FROM analysis_runs WHERE id=%s AND owner_id IS NOT DISTINCT FROM %s", (run_id, owner_id)).fetchone())
        if row:
            row["created_at"] = row["created_at"].isoformat()
        return row

    def create_user(self, user_id, username, password_hash):
        return self._execute(lambda conn: conn.execute(
            "INSERT INTO app_users(id,username,password_hash) VALUES (%s,%s,%s) ON CONFLICT(username) DO NOTHING RETURNING id",
            (user_id, username, password_hash)).fetchone()) is not None

    def create_notebook(self, identifier, document, owner_id=None):
        def create(connection):
            connection.execute("INSERT INTO interview_notebooks(id,owner_id,document) VALUES (%s,%s,%s)",
                               (identifier, owner_id, jsonb(document)))
            connection.execute("INSERT INTO notebook_revisions(notebook_id,revision,changes) VALUES (%s,0,%s)",
                               (identifier, jsonb([{"field": "created", "source_hash": document["source_hash"]}])))
        self._execute(create)

    def get_notebook(self, identifier, owner_id=None):
        row = self._execute(lambda conn: conn.execute(
            "SELECT * FROM interview_notebooks WHERE id=%s AND owner_id IS NOT DISTINCT FROM %s",
            (identifier, owner_id)).fetchone())
        return notebook_record(row, self.mode)

    def list_notebooks(self, owner_id=None):
        rows = self._execute(lambda conn: conn.execute(
            "SELECT id,document->>'title' AS title,revision,updated_at FROM interview_notebooks "
            "WHERE owner_id IS NOT DISTINCT FROM %s ORDER BY updated_at DESC LIMIT 20", (owner_id,)).fetchall())
        return [{**row, "id": str(row["id"]), "updated_at": row["updated_at"].isoformat()} for row in rows]

    def update_notebook(self, identifier, document, revision, changes, owner_id=None):
        def update(connection):
            row = connection.execute(
                "UPDATE interview_notebooks SET document=%s,revision=revision+1,updated_at=now() "
                "WHERE id=%s AND owner_id IS NOT DISTINCT FROM %s AND revision=%s RETURNING revision",
                (jsonb(document), identifier, owner_id, revision)).fetchone()
            if row is None:
                raise SessionConflict("Notebook changed")
            connection.execute("INSERT INTO notebook_revisions(notebook_id,revision,changes) VALUES (%s,%s,%s)",
                               (identifier, row["revision"], jsonb(changes)))
        self._execute(update)

    def notebook_history(self, identifier, owner_id=None):
        rows = self._execute(lambda conn: conn.execute(
            "SELECT r.revision,r.changes,r.created_at FROM notebook_revisions r "
            "JOIN interview_notebooks n ON n.id=r.notebook_id "
            "WHERE n.id=%s AND n.owner_id IS NOT DISTINCT FROM %s ORDER BY r.revision DESC LIMIT 20",
            (identifier, owner_id)).fetchall())
        return [{**row, "created_at": row["created_at"].isoformat()} for row in rows]

    def user_by_name(self, username):
        return self._execute(lambda conn: conn.execute("SELECT * FROM app_users WHERE username=%s", (username,)).fetchone())

    def save_auth(self, token_hash, user_id, expires_at):
        self._execute(lambda conn: conn.execute("INSERT INTO auth_sessions(token_hash,user_id,expires_at) VALUES (%s,%s,%s)", (token_hash, user_id, expires_at)))

    def auth_user(self, token_hash, now):
        return self._execute(lambda conn: conn.execute(
            "SELECT u.id,u.username FROM auth_sessions s JOIN app_users u ON u.id=s.user_id WHERE s.token_hash=%s AND s.expires_at>%s",
            (token_hash, now)).fetchone())

    def revoke_auth(self, token_hash):
        self._execute(lambda conn: conn.execute("DELETE FROM auth_sessions WHERE token_hash=%s", (token_hash,)))


def repository_from_env():
    dsn = os.environ.get("DATABASE_URL")
    return PostgresRepository(dsn) if dsn else MemoryRepository()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["migrate"])
    parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    repository = repository_from_env()
    if not isinstance(repository, PostgresRepository):
        parser.error("Set DATABASE_URL locally before migration")
    repository.migrate()
    print("PostgreSQL migration applied successfully.")


if __name__ == "__main__":
    main()
