"""Repository extensions for encrypted grants and leased, idempotent import jobs."""

from copy import deepcopy
from uuid import uuid4

from app.core.persistence import SessionConflict, jsonb


def new_job(identifier, owner, provider, record, now):
    return {"id": identifier, "owner_id": owner, "provider": provider, "conference_record": record,
            "status": "waiting", "payload": {}, "message": "Waiting to check the selected meeting",
            "attempts": 0, "google_requests": 0, "next_poll": now, "deadline": now + 1200,
            "lease_until": 0, "lease_token": None, "notebook_id": None, "created_at": now, "updated_at": now}


def clean_job(row):
    if row is None:
        return None
    result = deepcopy(row)
    result["id"] = str(result["id"])
    if result.get("notebook_id"):
        result["notebook_id"] = str(result["notebook_id"])
    return result


class MemoryIntegrationRepository:
    def save_google_connection(self, owner, encrypted, generation, scopes):
        with self.lock:
            self.google_connections[owner] = {"encrypted_tokens": encrypted, "generation": generation, "scopes": list(scopes)}

    def get_google_connection(self, owner):
        with self.lock:
            return deepcopy(self.google_connections.get(owner))

    def refresh_google_connection(self, owner, generation, encrypted):
        with self.lock:
            row = self.google_connections.get(owner)
            if not row or row["generation"] != generation:
                return False
            row["encrypted_tokens"] = encrypted
            return True

    def delete_google_connection(self, owner, generation):
        with self.lock:
            row = self.google_connections.get(owner)
            if row and row["generation"] == generation:
                del self.google_connections[owner]

    def create_meet_import(self, identifier, owner, provider, record, now):
        with self.lock:
            for row in self.meet_import_jobs.values():
                if (row["owner_id"], row["provider"], row["conference_record"]) == (owner, provider, record):
                    return clean_job(row)
            row = new_job(identifier, owner, provider, record, now)
            self.meet_import_jobs[identifier] = row
            return clean_job(row)

    def get_meet_import(self, identifier, owner):
        with self.lock:
            row = self.meet_import_jobs.get(identifier)
            return clean_job(row) if row and row["owner_id"] == owner else None

    def list_meet_imports(self, owner):
        with self.lock:
            return [clean_job(row) for row in sorted(self.meet_import_jobs.values(), key=lambda row: row["created_at"], reverse=True)
                    if row["owner_id"] == owner][:10]

    def claim_meet_import(self, now):
        with self.lock:
            for row in sorted(self.meet_import_jobs.values(), key=lambda row: row["next_poll"]):
                if row["status"] in {"waiting", "processing"} and row["next_poll"] <= now and row["lease_until"] <= now:
                    row.update(status="processing", lease_token=str(uuid4()), lease_until=now + 240,
                               attempts=row["attempts"] + 1, updated_at=now)
                    return clean_job(row)
            return None

    def finish_meet_import(self, identifier, lease, status, *, now, message, payload=None, reads=0, next_poll=None):
        with self.lock:
            row = self.meet_import_jobs.get(identifier)
            if not row or row["lease_token"] != lease or row["status"] != "processing":
                return False
            row.update(status=status, message=message, updated_at=now, lease_token=None, lease_until=0,
                       google_requests=row["google_requests"] + reads, next_poll=next_poll or now)
            if payload is not None:
                row["payload"] = deepcopy(payload)
            return True

    def action_meet_import(self, identifier, owner, action, now):
        with self.lock:
            row = self.meet_import_jobs.get(identifier)
            if not row or row["owner_id"] != owner:
                return None
            if row["status"] == "complete" or (action == "retry" and row["status"] not in {"failed", "expired", "cancelled"}):
                raise SessionConflict("This import cannot be changed in its current state")
            row.update(status="waiting" if action == "retry" else "cancelled", payload={}, lease_token=None,
                       lease_until=0, updated_at=now, message="Retry scheduled" if action == "retry" else "Import cancelled")
            if action == "retry":
                row.update(attempts=0, next_poll=now, deadline=now + 1200)
            return clean_job(row)

    def complete_meet_import(self, identifier, owner, document):
        with self.lock:
            row = self.meet_import_jobs.get(identifier)
            if not row or row["owner_id"] != owner:
                raise SessionConflict("Import not available")
            if row["notebook_id"]:
                return row["notebook_id"], True
            if row["status"] != "needs_speakers":
                raise SessionConflict("The transcript is not ready for speaker confirmation")
            notebook_id = str(uuid4())
            self.create_notebook(notebook_id, document, owner_id=owner)
            row.update(status="complete", notebook_id=notebook_id, payload={}, message="Notebook created")
            return notebook_id, False


class PostgresIntegrationRepository:
    def save_google_connection(self, owner, encrypted, generation, scopes):
        self._execute(lambda conn: conn.execute(
            "INSERT INTO google_connections(owner_id,encrypted_tokens,generation,scopes) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT(owner_id) DO UPDATE SET encrypted_tokens=excluded.encrypted_tokens,generation=excluded.generation,"
            "scopes=excluded.scopes,updated_at=now()", (owner, encrypted, generation, jsonb(scopes))))

    def get_google_connection(self, owner):
        return self._execute(lambda conn: conn.execute("SELECT encrypted_tokens,generation,scopes FROM google_connections WHERE owner_id=%s", (owner,)).fetchone())

    def refresh_google_connection(self, owner, generation, encrypted):
        return self._execute(lambda conn: conn.execute(
            "UPDATE google_connections SET encrypted_tokens=%s,updated_at=now() WHERE owner_id=%s AND generation=%s",
            (encrypted, owner, generation)).rowcount == 1)

    def delete_google_connection(self, owner, generation):
        self._execute(lambda conn: conn.execute("DELETE FROM google_connections WHERE owner_id=%s AND generation=%s", (owner, generation)))

    def create_meet_import(self, identifier, owner, provider, record, now):
        def create(conn):
            conn.execute("INSERT INTO meet_import_jobs(id,owner_id,provider,conference_record,status,next_poll,deadline,created_at,updated_at) "
                         "VALUES (%s,%s,%s,%s,'waiting',%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                         (identifier, owner, provider, record, now, now + 1200, now, now))
            return clean_job(conn.execute("SELECT * FROM meet_import_jobs WHERE owner_id IS NOT DISTINCT FROM %s AND provider=%s AND conference_record=%s",
                                          (owner, provider, record)).fetchone())
        return self._execute(create)

    def get_meet_import(self, identifier, owner):
        return clean_job(self._execute(lambda conn: conn.execute(
            "SELECT * FROM meet_import_jobs WHERE id=%s AND owner_id IS NOT DISTINCT FROM %s", (identifier, owner)).fetchone()))

    def list_meet_imports(self, owner):
        return [clean_job(row) for row in self._execute(lambda conn: conn.execute(
            "SELECT * FROM meet_import_jobs WHERE owner_id IS NOT DISTINCT FROM %s ORDER BY created_at DESC LIMIT 10", (owner,)).fetchall())]

    def claim_meet_import(self, now):
        def claim(conn):
            row = conn.execute("SELECT id FROM meet_import_jobs WHERE status IN ('waiting','processing') AND next_poll<=%s "
                               "AND lease_until<=%s ORDER BY next_poll LIMIT 1 FOR UPDATE SKIP LOCKED", (now, now)).fetchone()
            if row is None:
                return None
            return clean_job(conn.execute("UPDATE meet_import_jobs SET status='processing',lease_token=%s,lease_until=%s,"
                "attempts=attempts+1,updated_at=%s WHERE id=%s RETURNING *", (str(uuid4()), now + 240, now, row["id"])).fetchone())
        return self._execute(claim)

    def finish_meet_import(self, identifier, lease, status, *, now, message, payload=None, reads=0, next_poll=None):
        return self._execute(lambda conn: conn.execute(
            "UPDATE meet_import_jobs SET status=%s,message=%s,payload=COALESCE(%s,payload),google_requests=google_requests+%s,"
            "next_poll=%s,updated_at=%s,lease_token=NULL,lease_until=0 WHERE id=%s AND lease_token=%s AND status='processing'",
            (status, message, jsonb(payload) if payload is not None else None, reads, next_poll or now, now, identifier, lease)).rowcount == 1)

    def action_meet_import(self, identifier, owner, action, now):
        def update(conn):
            row = conn.execute("SELECT * FROM meet_import_jobs WHERE id=%s AND owner_id IS NOT DISTINCT FROM %s FOR UPDATE", (identifier, owner)).fetchone()
            if row is None:
                return None
            if row["status"] == "complete" or (action == "retry" and row["status"] not in {"failed", "expired", "cancelled"}):
                raise SessionConflict("This import cannot be changed in its current state")
            retry = action == "retry"
            return clean_job(conn.execute(
                "UPDATE meet_import_jobs SET status=%s,payload='{}'::jsonb,lease_token=NULL,lease_until=0,updated_at=%s,message=%s,"
                "attempts=%s,next_poll=%s,deadline=%s WHERE id=%s RETURNING *",
                ("waiting" if retry else "cancelled", now, "Retry scheduled" if retry else "Import cancelled",
                 0 if retry else row["attempts"], now if retry else row["next_poll"], now + 1200 if retry else row["deadline"], identifier)).fetchone())
        return self._execute(update)

    def complete_meet_import(self, identifier, owner, document):
        def complete(conn):
            row = conn.execute("SELECT * FROM meet_import_jobs WHERE id=%s AND owner_id IS NOT DISTINCT FROM %s FOR UPDATE", (identifier, owner)).fetchone()
            if row is None:
                raise SessionConflict("Import not available")
            if row["notebook_id"]:
                return str(row["notebook_id"]), True
            if row["status"] != "needs_speakers":
                raise SessionConflict("The transcript is not ready for speaker confirmation")
            notebook_id = str(uuid4())
            conn.execute("INSERT INTO interview_notebooks(id,owner_id,document) VALUES (%s,%s,%s)", (notebook_id, owner, jsonb(document)))
            conn.execute("INSERT INTO notebook_revisions(notebook_id,revision,changes) VALUES (%s,0,%s)",
                         (notebook_id, jsonb([{"field":"created", "source_hash":document["source_hash"]}])))
            conn.execute("UPDATE meet_import_jobs SET status='complete',notebook_id=%s,payload='{}'::jsonb,message='Notebook created' WHERE id=%s",
                         (notebook_id, identifier))
            return notebook_id, False
        return self._execute(complete)
