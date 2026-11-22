"""Offline backup and restore of users.json and store/. Stop all writers first.

The ZIP archive is unencrypted. Keep it on encrypted storage and protect transfers.
The SHA-256 manifest detects corruption. Restore only from a trusted archive.
"""
import argparse
from contextlib import contextmanager
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import zipfile


MANIFEST = "manifest.json"


class WorkspaceBusy(ValueError):
    """The workspace server or another backup holds the store lock."""


def publish(source, destination):
    """Atomically publish a completed file or directory only if the name is free."""
    libc = ctypes.CDLL(None, use_errno=True)
    paths = os.fsencode(source), os.fsencode(destination)
    if sys.platform == "darwin":
        result = libc.renamex_np(*paths, 4)  # RENAME_EXCL
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        result = libc.renameat2(-100, paths[0], -100, paths[1], 1)  # RENAME_NOREPLACE
    else:
        raise ValueError("Atomic no-overwrite publication requires macOS or Linux with renameat2")
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def copy_digest(source, destination):
    digest = hashlib.sha256()
    while block := source.read(1024 * 1024):
        digest.update(block)
        destination.write(block)
    return digest.hexdigest()


def safe_name(name):
    if (not isinstance(name, str) or any(c in name for c in ("\\", "\0", ":"))
            or any(part in ("", ".", "..") for part in name.split("/"))
            or any(ord(c) < 32 for c in name)
            or not (name in ("users.json", "store") or name.startswith("store/"))):
        raise ValueError("Archive contains an unsupported path")
    return name


@contextmanager
def offline_store(store):
    # Create the same persistent lock as the server for legacy stores that lack it.
    flags = os.O_CREAT | os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(store / ".workspace.lock", flags, 0o600), "rb") as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise ValueError("Workspace lock must be a regular file")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise WorkspaceBusy("Stop the workspace server before creating a backup") from error
        yield


def create(instance, archive):
    instance, archive = Path(instance), Path(archive)
    if instance.is_symlink() or not instance.is_dir():
        raise ValueError("Choose an instance directory")
    if archive.resolve().is_relative_to(instance.resolve()):
        raise ValueError("Write the backup outside the instance directory")
    if os.path.lexists(archive):
        raise FileExistsError("Destination already exists")
    if ((instance / "users.json").is_symlink() or not (instance / "users.json").is_file()
            or (instance / "store").is_symlink() or not (instance / "store").is_dir()):
        raise ValueError("Instance requires a regular users.json and store directory")
    with offline_store(instance / "store"), tempfile.TemporaryDirectory(dir=archive.parent) as staging:
        partial = Path(staging) / "backup.zip"
        files, directories = {}, []

        def add(path):
            name = safe_name(path.relative_to(instance).as_posix())
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                directories.append(name)
                for child in sorted(path.iterdir()):
                    add(child)
            elif stat.S_ISREG(mode):
                with path.open("rb") as source, writer.open(name, "w", force_zip64=True) as target:
                    files[name] = copy_digest(source, target)
            else:
                raise ValueError("Instance contains a symbolic link or special file")

        with zipfile.ZipFile(partial, mode="x", compression=zipfile.ZIP_DEFLATED) as writer:
            add(instance / "users.json")
            add(instance / "store")
            writer.writestr(MANIFEST, json.dumps({"version": 1, "files": files, "directories": directories}))
        partial.chmod(0o600)
        publish(partial, archive)


def restore(archive, instance):
    archive, instance = Path(archive), Path(instance)
    if archive.is_symlink() or not archive.is_file():
        raise ValueError("Choose a regular backup archive")
    if os.path.lexists(instance):
        raise FileExistsError("Destination already exists")
    with zipfile.ZipFile(archive) as reader, tempfile.TemporaryDirectory(dir=instance.parent) as staging:
        members = reader.infolist()
        for entry in members:
            file_type = stat.S_IFMT(entry.external_attr >> 16)
            if (entry.orig_filename != entry.filename or entry.is_dir()
                    or file_type not in (0, stat.S_IFREG) or entry.flag_bits & 1):
                raise ValueError("Archive contains a special or encrypted entry")
            if entry.filename != MANIFEST:
                safe_name(entry.filename)
        names = {entry.filename for entry in members}
        if len(names) != len(members) or MANIFEST not in names:
            raise ValueError("Archive has duplicate entries or lacks its manifest")
        manifest = json.loads(reader.read(MANIFEST))
        if not isinstance(manifest, dict) or manifest.get("version") != 1:
            raise ValueError("Unsupported backup manifest")
        files, directories = manifest.get("files"), manifest.get("directories")
        if (not isinstance(files, dict) or not isinstance(directories, list)
                or set(files) != names - {MANIFEST} or "users.json" not in files
                or not all(isinstance(d, str) for d in directories)
                or "store" not in directories or "users.json" in directories
                or len(set(directories)) != len(directories) or set(directories).intersection(files)
                or any(not isinstance(h, str) or not re.fullmatch("[0-9a-f]{64}", h) for h in files.values())):
            raise ValueError("Backup inventory differs from its manifest")
        root = Path(staging) / "instance"
        root.mkdir(mode=0o700)
        for name in sorted(directories, key=lambda n: n.count("/")):
            (root / safe_name(name)).mkdir(mode=0o700)
        for name, digest in files.items():
            target = root / safe_name(name)
            with reader.open(name) as source, target.open("xb") as destination:
                os.fchmod(destination.fileno(), 0o600)
                if copy_digest(source, destination) != digest:
                    raise ValueError("File digest differs from backup manifest")
        publish(root, instance)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("create")
    backup.add_argument("instance", type=Path)
    backup.add_argument("archive", type=Path)
    recovery = commands.add_parser("restore")
    recovery.add_argument("archive", type=Path)
    recovery.add_argument("instance", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "create":
            create(args.instance, args.archive)
        else:
            restore(args.archive, args.instance)
    except WorkspaceBusy as error:
        parser.exit(1, f"{error}. No destination was published.\n")
    except (ValueError, OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        parser.exit(1, f"Backup operation failed ({type(error).__name__}). No destination was published.\n")


if __name__ == "__main__":
    main()
