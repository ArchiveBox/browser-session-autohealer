"""Private, immutable checkpoint objects. Database records contain no cookie values."""

import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet
from plain.runtime import settings


def data_root():
    root = Path(settings.APP_DATA_DIR)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    return root


def private_dir(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def cipher():
    path = Path(settings.APP_CONFIG_DIR) / "snapshot-key"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as f:
            f.write(Fernet.generate_key())
    return Fernet(path.read_bytes())


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def fingerprint(value):
    # Keyed fingerprints do not expose low-entropy cookie values to offline guessing.
    return hmac.new(
        Path(settings.APP_CONFIG_DIR, "snapshot-key").read_bytes(), canonical(value), hashlib.sha256
    ).hexdigest()


def write_object(manifest, state, native=None):
    crypto = cipher()
    manifest = {**manifest, "state_digest": fingerprint(state)}
    digest = hashlib.sha256(canonical(manifest)).hexdigest()
    objects = private_dir(data_root() / "objects")
    destination = objects / digest
    if destination.exists():
        return digest
    staging = Path(tempfile.mkdtemp(prefix=".checkpoint-", dir=objects))
    try:
        (staging / "manifest.json").write_bytes(canonical(manifest))
        (staging / "state.enc").write_bytes(crypto.encrypt(canonical(state)))
        if native:
            # Native Chrome data stays on a private, volume-encrypted filesystem.
            # Keeping directories supports CoW snapshots without an in-memory tar.
            copy_profile(native, staging / "profile")
        os.rename(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return digest


def read_state(digest):
    return json.loads(cipher().decrypt((object_path(digest) / "state.enc").read_bytes()))


def object_path(digest):
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Invalid checkpoint ID")
    return data_root() / "objects" / digest


def profile_ignore(directory, names):
    transient = {
        "SingletonLock",
        "SingletonCookie",
        "SingletonSocket",
        "DevToolsActivePort",
        "Cache",
        "Code Cache",
        "GPUCache",
        "DawnCache",
        "ShaderCache",
        "GrShaderCache",
        "Crashpad",
        "BrowserMetrics",
        "component_crx_cache",
        "extensions_crx_cache",
    }
    return [n for n in names if n in transient or Path(directory, n).is_symlink()]


def copy_profile(source, target):
    if sys.platform == "darwin":
        subprocess.run(
            ["/bin/cp", "-cR", str(source), str(target)], check=True, capture_output=True
        )
        for directory, dirs, files in os.walk(target, topdown=True, followlinks=False):
            for name in profile_ignore(directory, dirs + files):
                path = Path(directory, name)
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                    dirs.remove(name)
                else:
                    path.unlink()
                    if name in dirs:
                        dirs.remove(name)
    else:
        shutil.copytree(source, target, ignore=profile_ignore)


def fork_profile(digest, run_id, *, portable_only=False):
    work = private_dir(data_root() / "runs" / str(run_id))
    target = work / "personas" / "working" / "chrome_profile"
    if target.exists():
        raise ValueError("Run already has a working profile")
    source = object_path(digest) / "profile"
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.exists() and not portable_only:
        copy_profile(source, target)
    else:
        target.mkdir(mode=0o700)
    return work, target


def cookie_key(cookie):
    return canonical([cookie.get(k) for k in ("name", "domain", "path", "partitionKey")]).decode()


def compare(base, head):
    a, b = read_state(base), read_state(head)
    result = []
    for category in ("cookies", "settings", "origins"):

        def index(state, category=category):
            values = state.get(category, {} if category == "settings" else [])
            if category == "cookies":
                return {cookie_key(c): c for c in values}
            if category == "origins":
                return {c["origin"]: c for c in values}
            return values

        left, right = index(a), index(b)
        for key in sorted(left.keys() | right.keys()):
            if left.get(key) == right.get(key) and (key in left) == (key in right):
                continue
            kind = "added" if key not in left else "removed" if key not in right else "updated"
            row = {"category": category, "key": key, "kind": kind}
            if category == "settings":
                row.update(before=left.get(key), after=right.get(key))
            else:
                row.update(
                    before="••••" if key in left else "—", after="••••" if key in right else "—"
                )
            if category == "cookies" and kind == "updated":
                row["changed_fields"] = sorted(
                    field for field in left[key].keys() | right[key].keys()
                    if left[key].get(field) != right[key].get(field)
                )
            result.append(row)
    return result
