"""Single-organization accounts and process-local browser sessions."""
from contextlib import contextmanager
import getpass
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import json
import os
import re
from pathlib import Path
import secrets
import tempfile
import threading
import time
from urllib.parse import urlsplit


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return f"scrypt1:{salt}:{digest.hex()}"


def options(parser):
    parser.add_argument("--mode", choices=("local", "private", "demo"), default="local")
    parser.add_argument("--users", type=Path, help="Private-mode organization account file")
    parser.add_argument("--origin", help="Canonical browser origin served by the reverse proxy")
    parser.add_argument("--bind", default="127.0.0.1", help="Loopback HTTP listener behind the HTTPS proxy")


def secure_origin(origin):
    if not origin:
        return False
    url = urlsplit(origin)
    if (url.scheme not in ("https", "http") or not url.hostname or url.username or url.password
            or url.path or url.query or url.fragment):
        raise ValueError("Supply the exact HTTP(S) origin without a trailing slash")
    if url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1"):
        raise ValueError("Deployment origins require HTTPS")
    return url.scheme == "https"


class Access:
    def __init__(self, mode="local", users=None, origin=None, bind="127.0.0.1"):
        self.mode = mode
        self.origin = origin
        self.secure = secure_origin(origin)
        self.organization = "Sample workspace" if mode == "demo" else "Local workspace"
        self.accounts = {}
        self.sessions = {}
        self.lock = threading.Lock()
        if mode not in ("private", "local", "demo"):
            raise ValueError("Unknown workspace mode")
        if mode == "local" and origin:
            raise ValueError("Unsigned local mode requires the default loopback listener")
        if bind != "127.0.0.1":
            raise ValueError("Bind the workspace to 127.0.0.1 and expose the HTTPS proxy")
        if mode == "private":
            if not users:
                raise ValueError("Private mode requires --users")
            config = json.loads(Path(users).read_text())
            if not isinstance(config, dict) or not isinstance(config.get("users"), dict):
                raise ValueError("Supply an organization with a users object")
            self.organization = config.get("organization")
            self.accounts = config["users"]
            if not isinstance(self.organization, str) or not self.organization.strip() or not self.accounts:
                raise ValueError("Configure one organization and its named accounts")
            for name, account in self.accounts.items():
                if (not name.strip() or len(name) > 160 or not isinstance(account, dict)
                        or account.get("role") not in ("operator", "reviewer", "analyst")
                        or not isinstance(account.get("password_hash"), str)
                        or not re.fullmatch(r"scrypt1:[0-9a-f]{32}:[0-9a-f]{128}", account["password_hash"])):
                    raise ValueError("Invalid account name, role or password hash")
            self.verifiers = threading.BoundedSemaphore(2)
            self.dummy_hash = next(iter(self.accounts.values()))["password_hash"]
        elif users:
            raise ValueError("Accounts apply only to private mode")

    def check_store(self, root):
        marker = Path(root) / "organization.json"
        try:
            saved = json.loads(marker.read_bytes())
        except FileNotFoundError:
            return False
        if self.mode != "private":
            raise ValueError("This organization-bound store requires authenticated private mode")
        if saved != {"schema": 1, "organization": self.organization}:
            raise ValueError("This store belongs to a different organization or its identity is invalid")
        return True

    @contextmanager
    def open_store(self, root):
        """Hold the writer lock through shutdown, including the first organization binding."""
        import fcntl
        from .local import save
        root = Path(root)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with os.fdopen(os.open(root / ".workspace.lock", os.O_CREAT | os.O_WRONLY, 0o600), "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("This store is already served by another workspace process") from error
            bound = self.check_store(root)
            if self.mode == "private" and not bound:
                save(root / "organization.json", {"schema": 1, "organization": self.organization})
            yield

    def identity(self, cookie):
        if self.mode != "private":
            return {"username": "", "role": "local" if self.mode == "local" else "guest"}
        with self.lock:
            session = self.sessions.get(self.token(cookie))
            if session and session[1] > time.monotonic():
                return {"username": session[0], "role": self.accounts[session[0]]["role"]}

    @staticmethod
    def token(cookie):
        jar = SimpleCookie()
        try:
            jar.load(cookie or "")
        except CookieError:
            return ""
        return jar["workspace_session"].value if "workspace_session" in jar else ""

    def password_matches(self, expected, password):
        if not self.verifiers.acquire(blocking=False):
            return False
        try:
            return hmac.compare_digest(expected, password_hash(password, expected.split(":")[1]))
        finally:
            self.verifiers.release()

    def login(self, username, password):
        if not isinstance(username, str) or not isinstance(password, str) or len(password) > 1024:
            return None
        account = self.accounts.get(username)
        expected = account["password_hash"] if account else self.dummy_hash
        if not self.password_matches(expected, password) or not account:
            return None
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self.lock:
            self.sessions = {k: v for k, v in self.sessions.items() if v[1] > now}
            self.sessions[token] = (username, now + 8 * 3600)
        return token

    def logout(self, cookie):
        with self.lock:
            self.sessions.pop(self.token(cookie), None)

    def cookie(self, token):
        age = 28800 if token else 0
        return (f"workspace_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={age}"
                + ("; Secure" if self.secure else ""))


def add_user(path, organization, username, role, password):
    """Serialize read/modify/replace so concurrent provisioning preserves both accounts."""
    import fcntl
    path = Path(path)
    if not organization.strip() or not username.strip() or len(username) > 160:
        raise ValueError("Supply an organization and a named user")
    if role not in ("analyst", "reviewer", "operator") or not password or len(password) > 1024:
        raise ValueError("Choose a supported role and a password up to 1024 characters")
    hashed = password_hash(password)
    lock_path = path.with_name(path.name + ".lock")
    with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o600), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            previous = Access("private", path)
            if previous.organization != organization:
                raise ValueError("Existing account file belongs to a different organization")
            if username in previous.accounts:
                raise ValueError("That username already exists")
            accounts = previous.accounts
        else:
            accounts = {}
        accounts[username] = {"role": role, "password_hash": hashed}
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".accounts-", delete=False) as out:
                tmp = Path(out.name)
                json.dump({"organization": organization, "users": accounts}, out, indent=2)
                out.write("\n")
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, path)
        finally:
            if tmp:
                tmp.unlink(missing_ok=True)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Manage workspace account credentials")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("hash-password")
    create = commands.add_parser("add-user")
    create.add_argument("--file", required=True, type=Path)
    create.add_argument("--organization", required=True)
    create.add_argument("--username", required=True)
    create.add_argument("--role", choices=("analyst", "reviewer", "operator"), required=True)
    args = parser.parse_args()
    password = getpass.getpass("Account password: ")
    if not password or password != getpass.getpass("Confirm password: "):
        parser.error("Enter matching nonempty passwords")
    if args.command == "hash-password":
        print(password_hash(password))
    else:
        try:
            add_user(args.file, args.organization, args.username, args.role, password)
        except (ValueError, OSError) as error:
            parser.error(str(error))
        print(f"Added {args.username}. Restart the private workspace to load the account file.")


if __name__ == "__main__":
    main()
