"""Single-organization PostgreSQL workspace for the hosted edition."""

import argparse
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import getpass
import hashlib
import os
from pathlib import PurePosixPath
import secrets
import sys
import threading
from uuid import uuid4

from .access import Access, password_hash, secure_origin


# Session scope allows the server to commit a used Gemini confirmation before the
# network call without allowing a second writer to modify the same deal mid-request.
WORKSPACE_LOCK = int.from_bytes(b"WFSTORE1", "big")
SCHEMA = """
CREATE TABLE IF NOT EXISTS wf_workspace (
    singleton boolean PRIMARY KEY CHECK (singleton),
    organization text NOT NULL,
    workspace_id text NOT NULL,
    schema_version integer NOT NULL
);
CREATE TABLE IF NOT EXISTS wf_documents (
    collection text NOT NULL,
    name text NOT NULL,
    content bytea NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (collection, name)
);
CREATE TABLE IF NOT EXISTS wf_accounts (
    username text PRIMARY KEY,
    role text NOT NULL CHECK (role IN ('operator','analyst','reviewer')),
    password_hash text NOT NULL,
    enabled boolean NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS wf_sessions (
    digest bytea PRIMARY KEY,
    username text NOT NULL REFERENCES wf_accounts(username) ON DELETE CASCADE,
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS wf_sessions_expiry ON wf_sessions (expires_at);
CREATE OR REPLACE VIEW wf_mcp_status AS
    SELECT w.organization, d.name, convert_from(d.content,'UTF8')::jsonb->>'status' AS status
    FROM wf_documents d CROSS JOIN wf_workspace w
    WHERE d.collection='runs' AND d.name=(convert_from(d.content,'UTF8')::jsonb->>'run_id') || '.json';
"""


class Database:
    """One customer per database. Artifact bytes are hashed by the existing verifier."""

    def __init__(self, dsn, organization):
        if not isinstance(organization, str) or not organization.strip() or len(organization) > 200:
            raise ValueError("Set WATERFALL_ORGANIZATION to the organization's name.")
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("Set DATABASE_URL to the PostgreSQL connection string.")
        self._dsn = dsn
        self.organization = organization
        self.workspace_id = None
        self.root = Document(self, ())
        self._session = ContextVar("workspace_transaction", default=None)

    def connect(self):
        import psycopg
        return psycopg.connect(self._dsn, connect_timeout=5, application_name="fin-waterfall")

    def initialize(self):
        with self.transaction(write=True) as connection:
            connection.execute(SCHEMA, prepare=False)
            connection.execute(
                "INSERT INTO wf_workspace VALUES (true, %s, %s, 1) ON CONFLICT DO NOTHING",
                (self.organization, uuid4().hex),
            )
            name, instance, version = connection.execute(
                "SELECT organization, workspace_id, schema_version FROM wf_workspace WHERE singleton"
            ).fetchone()
            if name != self.organization:
                raise ValueError("This database belongs to a different organization.")
            if version != 1:
                raise ValueError("This database requires a different workspace schema version.")
        self.workspace_id = instance

    def health(self):
        with self.connect() as connection:
            return connection.execute(
                "SELECT 1 FROM wf_workspace WHERE organization=%s AND schema_version=1",
                (self.organization,),
            ).fetchone() == (1,)

    @contextmanager
    def transaction(self, write=False):
        existing = self._session.get()
        if existing:
            if write and not existing[1]:
                raise RuntimeError("Cannot modify the workspace from a read transaction")
            yield existing[0]
            return
        # Each request gets its own connection. Closing the connection releases
        # the session lock even on cancellation or a rolled-back transaction.
        with self.connect() as connection:
            if write:
                connection.execute("SELECT pg_advisory_lock(%s)", (WORKSPACE_LOCK,))
            else:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            token = self._session.set((connection, write))
            try:
                yield connection
            finally:
                self._session.reset(token)

    def checkpoint(self):
        active = self._session.get()
        if not active or not active[1]:
            raise RuntimeError("A confirmation can only be consumed inside a write transaction")
        active[0].commit()

    def wrap(self, app):
        return TransactionalApp(self, app)

    def run_status(self, run_id):
        """Only the status column leaves PostgreSQL, including for the stdio MCP process."""
        from .engine import STATUSES
        from .local import ID_PATTERN
        if not isinstance(run_id, str) or not ID_PATTERN.fullmatch(run_id):
            return "HOLD"
        try:
            with self.transaction() as connection:
                result = connection.execute(
                    "SELECT status FROM wf_mcp_status WHERE organization=%s AND name=%s",
                    (self.organization, run_id + ".json"),
                ).fetchone()
            return result[0] if result and result[0] in STATUSES else "HOLD"
        except Exception:
            return "HOLD"


class TransactionalApp:
    def __init__(self, database, app):
        self.database = database
        self.app = app

    def __getattr__(self, name):
        return getattr(self.app, name)

    def get(self, path):
        with self.database.transaction():
            return self.app.get(path)

    def post(self, path, body, **kwargs):
        with self.database.transaction(write=True):
            return self.app.post(path, body, **kwargs)


@dataclass(frozen=True, order=True)
class Document:
    database: Database = field(repr=False, compare=False)
    parts: tuple[str, ...]

    def __truediv__(self, name):
        if not isinstance(name, str) or not name or "/" in name or name in (".", "..") or len(self.parts) >= 2:
            raise ValueError("Invalid workspace record name")
        return Document(self.database, (*self.parts, name))

    @property
    def name(self):
        return self.parts[-1]

    @property
    def stem(self):
        return PurePosixPath(self.name).stem

    def exists(self):
        with self.database.transaction() as connection:
            return connection.execute(
                "SELECT 1 FROM wf_documents WHERE collection=%s AND name=%s", self.parts
            ).fetchone() is not None

    def read_bytes(self):
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT content FROM wf_documents WHERE collection=%s AND name=%s", self.parts
            ).fetchone()
            if row is None:
                raise FileNotFoundError(self.name)
            return bytes(row[0])

    def modified_time(self):
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT updated_at FROM wf_documents WHERE collection=%s AND name=%s", self.parts
            ).fetchone()
            if row is None:
                raise FileNotFoundError(self.name)
            return row[0].timestamp()

    def glob(self, pattern):
        if len(self.parts) != 1 or pattern != "*.json":
            raise ValueError("Only listing workspace JSON records by collection is supported")
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT name FROM wf_documents WHERE collection=%s AND right(name, 5)='.json' ORDER BY name",
                self.parts,
            )
            return [self / name for name, in rows]

    def install(self, content):
        """Keep existing bundled rules and their original SHA-256 identities intact."""
        with self.database.transaction(write=True) as connection:
            added = connection.execute(
                "INSERT INTO wf_documents(collection,name,content) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (*self.parts, content),
            )
            if not added.rowcount and self.read_bytes() != content:
                raise ValueError(f"Existing workspace artifact differs: {self.name}")

    def write_bytes(self, content):
        if self.parts[0] in ("rules", "approvals", "approved-drafts"):
            self.install(content)
            return
        with self.database.transaction(write=True) as connection:
            connection.execute(
                """INSERT INTO wf_documents(collection,name,content) VALUES (%s,%s,%s)
                   ON CONFLICT (collection,name) DO UPDATE SET content=EXCLUDED.content, updated_at=now()""",
                (*self.parts, content),
            )


class PostgresAccess(Access):
    """Use the same roles, scrypt checks and cookie flags as a private installation."""

    mode = "private"
    edition = "hosted"

    def __init__(self, database, origin):
        if not origin:
            raise ValueError("Set the canonical browser origin for the hosted workspace.")
        self.database = database
        self.organization = database.organization
        self.origin = origin
        self.secure = secure_origin(origin)
        self.verifiers = threading.BoundedSemaphore(2)
        self.dummy_hash = "scrypt1:" + "0" * 32 + ":" + "0" * 128

    @staticmethod
    def _digest(token):
        return hashlib.sha256(token.encode("utf-8")).digest()

    def identity(self, cookie):
        token = self.token(cookie)
        if not token or len(token) != 43:
            return None
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT a.username, a.role FROM wf_sessions s JOIN wf_accounts a USING (username)
                   WHERE s.digest=%s AND s.expires_at > now() AND a.enabled""",
                (self._digest(token),),
            ).fetchone()
        return {"username": row[0], "role": row[1]} if row else None

    def login(self, username, password):
        if (not isinstance(username, str) or len(username) > 160
                or not isinstance(password, str) or len(password) > 1024):
            return None
        with self.database.connect() as connection:
            row = connection.execute("SELECT password_hash FROM wf_accounts WHERE username=%s AND enabled FOR SHARE",
                                     (username,)).fetchone()
            if not self.password_matches(row[0] if row else self.dummy_hash, password) or not row:
                return None
            token = secrets.token_urlsafe(32)
            connection.execute("DELETE FROM wf_sessions WHERE expires_at <= now()")
            connection.execute("INSERT INTO wf_sessions VALUES (%s, %s, now() + interval '8 hours')",
                               (self._digest(token), username))
        return token

    def logout(self, cookie):
        with self.database.connect() as connection:
            connection.execute("DELETE FROM wf_sessions WHERE digest=%s", (self._digest(self.token(cookie)),))

    def manage_user(self, command, username, *, role=None, password=None):
        if not isinstance(username, str) or not username.strip() or len(username) > 160:
            raise ValueError("Supply a named user of up to 160 characters.")
        if command in ("add-user", "reset-password"):
            if not isinstance(password, str) or not password or len(password) > 1024:
                raise ValueError("Supply a nonempty password of up to 1024 characters.")
            hashed = password_hash(password)
        with self.database.connect() as connection:
            if command == "add-user":
                if role not in ("analyst", "reviewer", "operator"):
                    raise ValueError("Choose a supported workspace role.")
                added = connection.execute(
                    "INSERT INTO wf_accounts(username,role,password_hash) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                    (username, role, hashed),
                )
                if not added.rowcount:
                    raise ValueError("That username already exists.")
                return
            if command == "disable-user":
                updated = connection.execute("UPDATE wf_accounts SET enabled=false WHERE username=%s", (username,))
            elif command == "reset-password":
                updated = connection.execute("UPDATE wf_accounts SET password_hash=%s WHERE username=%s",
                                             (hashed, username))
            else:
                raise ValueError("Unknown account action.")
            if not updated.rowcount:
                raise ValueError("No account has that username.")
            connection.execute("DELETE FROM wf_sessions WHERE username=%s", (username,))


def main():
    parser = argparse.ArgumentParser(description="Manage one hosted organization's PostgreSQL accounts")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("add-user", "reset-password", "disable-user"):
        command = commands.add_parser(name)
        command.add_argument("--username", required=True)
        if name == "add-user":
            command.add_argument("--role", choices=("analyst", "reviewer", "operator"), required=True)
        if name != "disable-user":
            command.add_argument("--password-stdin", action="store_true", help="Read one password line from stdin")
    args = parser.parse_args()
    password = None
    if args.command != "disable-user":
        if args.password_stdin:
            password = sys.stdin.readline(1026).rstrip("\r\n")
        else:
            password = getpass.getpass("Account password: ")
            if password != getpass.getpass("Confirm password: "):
                parser.error("Enter matching passwords.")
    try:
        database = Database(os.environ.get("DATABASE_URL"), os.environ.get("WATERFALL_ORGANIZATION"))
        database.initialize()
        PostgresAccess(database, "http://localhost").manage_user(args.command, args.username,
            role=getattr(args, "role", None), password=password)
    except ValueError as error:
        parser.error(str(error))
    print("Account updated.")


if __name__ == "__main__":
    main()
