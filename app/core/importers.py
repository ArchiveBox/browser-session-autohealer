"""Explicit local imports; credentials never leave this process as plain output."""

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
from contextlib import closing
from pathlib import Path

import browser_cookie3

from . import services, storage
from .site_scope import checkin_state, matches, normalize_sites, select_state


def import_portable(persona, state, actor, source, *, sites, coverage=None):
    state = select_state(state, sites)
    if sites is not None:
        base = storage.read_state(services.leader_for(persona).checkpoint.digest)
        previous_scope = persona.config.get('siteScope', None if base.get('cookies') or base.get('origins') else [])
        state = checkin_state({**base, 'settings': persona.config}, state, sites)
        scope = list(dict.fromkeys([*previous_scope, *sites])) if previous_scope is not None else None
        state['siteScope'] = state['settings']['siteScope'] = scope
    persona.config = state.get("settings", {})
    persona.update(fields=["config"])
    state["settings"] = persona.config
    return services.import_state(persona, state, actor, source, coverage=coverage or {
        "native": False, "cookies": "selected sites" if sites else "complete",
        "origins": "source-reported", "siteScope": sites,
    })


def capture_cdp(endpoint, sites):
    # Use the same provisioned Chrome helper, without creating a run or browser.
    root = Path(__file__).resolve().parents[3]
    env = {**os.environ, "ACCOUNT_CHECKER_CHROME_HELPER": str(root / "abx-plugins/abx_plugins/plugins/chrome/chrome_utils.js"),
           "NODE_MODULES_DIR": str(Path.home() / ".config/abx/lib/npm/node_modules")}
    result = subprocess.run(["node", str(Path(__file__).with_name("browser.cjs"))],
        input=json.dumps({"action": "capture", "cdp": endpoint, "sites": normalize_sites(sites)}),
        capture_output=True, text=True, timeout=120, env=env, check=False)
    if result.returncode:
        raise ValueError("Could not capture the source browser; confirm its debugging connection is available")
    return select_state(json.loads(result.stdout), sites)


def import_source(persona, source_kind, location, actor, *, sites):
    sites = normalize_sites(sites)
    if source_kind in {"brave", "chrome", "native"}:
        return import_browser(persona, location, source_kind, actor, sites=sites)
    if source_kind == "cdp":
        state = capture_cdp(location, sites)
        source = {"kind": "cdp", "hostname": socket.gethostname()}
    elif source_kind == "json":
        state = json.loads(Path(location).expanduser().read_text())
        source = {"kind": "portable file", "path": str(Path(location).expanduser()), "hostname": socket.gethostname()}
    else:
        raise ValueError("Choose a supported import source")
    return import_portable(persona, state, actor, source, sites=sites)


def discover():
    home = Path.home()
    roots = [
        home / "Library/Application Support/BraveSoftware/Brave-Browser",
        home / "Library/Application Support/Google/Chrome",
    ]
    results = []
    for root in roots:
        state = root / "Local State"
        if state.exists():
            info = json.loads(state.read_text()).get("profile", {}).get("info_cache", {})
            for directory, meta in info.items():
                results.append(
                    {
                        "name": meta.get("name", directory),
                        "path": str(root / directory),
                        "browser": "brave" if "Brave" in str(root) else "chrome",
                    }
                )
    for root in [
        home / "archivebox/data/personas",
        Path(__file__).resolve().parents[3] / "archivebox/data/personas",
    ]:
        if root.exists():
            for path in root.glob("*/chrome_profile"):
                results.append({"name": path.parent.name, "path": str(path), "browser": "native"})
    return results


def import_browser(persona, profile_path, browser, actor, *, sites=None):
    sites = normalize_sites(sites)
    profile = Path(profile_path).expanduser().resolve(strict=True)
    if browser not in {"brave", "chrome", "native"}:
        raise ValueError("Unsupported local browser")
    stage = Path(
        tempfile.mkdtemp(prefix="import-", dir=storage.private_dir(storage.data_root() / "imports"))
    )
    try:
        target = stage / "profile"
        if sites is not None:
            # Never copy unrelated native databases for a selected-site import.
            default = profile / "Default" if (profile / "Local State").exists() else profile
            cookie_file = next((p for p in [default / "Network/Cookies", default / "Cookies"] if p.exists()), None)
            if not cookie_file or browser == "native":
                raise ValueError("Use a live CDP connection to selectively import this native profile")
            (target / "Default").mkdir(parents=True)
        elif (profile / "Local State").exists():
            storage.copy_profile(profile, target)
        else:
            target.mkdir()
            storage.copy_profile(profile, target / "Default")
            if (profile.parent / "Local State").exists():
                shutil.copy2(profile.parent / "Local State", target / "Local State")
        # Chrome holds exclusive locks on some live databases (e.g. Favicons).
        # Copy each database with its WAL, require unchanged source metadata
        # across that copy, then recover/backup the isolated copy without SHM.
        # A moving database fails the import, rather than accepting torn state.
        originals = [cookie_file] if sites is not None else profile.rglob("*")
        for original in originals:
            if (
                not original.is_file()
                or original.is_symlink()
                or any(
                    p in storage.profile_ignore(original.parent, [original.name])
                    for p in [original.name]
                )
            ):
                continue
            try:
                with original.open("rb") as f:
                    sqlite = f.read(16) == b"SQLite format 3\x00"
            except OSError:
                continue
            if not sqlite:
                continue
            destination = target / "Default/Cookies" if sites is not None else (
                target if (profile / "Local State").exists() else target / "Default"
            ) / original.relative_to(profile)
            if not destination.parent.exists():
                continue
            files = [original, Path(str(original) + "-wal")]

            def signatures(files=files):
                return [
                    (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns)
                    if p.exists()
                    else None
                    for p in files
                ]

            before = signatures()
            scratch = stage / "sqlite-snapshot"
            scratch.mkdir(exist_ok=True)
            snapshot = scratch / "database"
            shutil.copy2(original, snapshot)
            if files[1].exists():
                shutil.copy2(files[1], Path(str(snapshot) + "-wal"))
            if before != signatures():
                raise RuntimeError(
                    f"Database changed during import: {original.name}; import again when the browser is idle"
                )
            # Never open a copied WAL/SHM as the destination of SQLite backup.
            # Its stale locking metadata can make backup wait indefinitely.
            destination.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm"):
                Path(str(destination) + suffix).unlink(missing_ok=True)
            deadline = time.monotonic() + 10

            def progress(status, remaining, total, deadline=deadline, original=original):
                if time.monotonic() > deadline:
                    raise RuntimeError(f"SQLite snapshot did not settle: {original.name}")

            with (
                closing(sqlite3.connect(snapshot)) as source,
                closing(sqlite3.connect(destination)) as dest,
            ):
                source.backup(dest, pages=512, progress=progress)
            shutil.rmtree(scratch)
        default = target / "Default"
        cookies_path = next(
            (p for p in [default / "Network/Cookies", default / "Cookies"] if p.exists()), None
        )
        cookies = []
        if cookies_path and browser != "native":
            decoder = (browser_cookie3.Brave if browser == "brave" else browser_cookie3.Chrome)(
                cookie_file=str(cookies_path)
            )
            with sqlite3.connect(cookies_path) as connection:
                connection.row_factory = sqlite3.Row
                version = int(
                    connection.execute("select value from meta where key='version'").fetchone()[0]
                )
                columns = [
                    r[1]
                    for r in connection.execute("pragma table_info(cookies)")
                    if r[1] != "encrypted_value"
                ]
                projection = ",".join('"' + c.replace('"', '""') + '"' for c in columns)
                for row in connection.execute(
                    f"SELECT {projection}, CAST(encrypted_value AS BLOB) AS encrypted_value FROM cookies"
                ):
                    row = dict(row)
                    if not matches(row["host_key"], sites):
                        continue
                    value = decoder._decrypt(row["value"], row["encrypted_value"], version >= 24)
                    c = {
                        "name": row["name"],
                        "value": value,
                        "domain": row["host_key"],
                        "path": row["path"],
                        "hostOnly": not row["host_key"].startswith("."),
                        "secure": bool(row["is_secure"]),
                        "httpOnly": bool(row["is_httponly"]),
                        "priority": ["Low", "Medium", "High"][row.get("priority", 1)],
                    }
                    if row.get("has_expires"):
                        c["expires"] = row["expires_utc"] / 1_000_000 - 11644473600
                    if row.get("samesite") in (0, 1, 2):
                        c["sameSite"] = {0: "None", 1: "Lax", 2: "Strict"}[row["samesite"]]
                    if row.get("top_frame_site_key"):
                        c["partitionKey"] = {
                            "topLevelSite": row["top_frame_site_key"],
                            "hasCrossSiteAncestor": bool(row.get("has_cross_site_ancestor", False)),
                        }
                    cookies.append(c)
        state = {"cookies": cookies, "settings": persona.config, "origins": []}
        if sites is not None:
            return import_portable(persona, state, actor,
                {"path": str(profile), "hostname": socket.gethostname(), "browser": browser},
                sites=sites, coverage={"native": False, "cookies": "selected sites", "origins": "not captured from file; use live CDP", "siteScope": sites})
        return services.import_state(
            persona,
            state,
            actor,
            {"path": str(profile), "hostname": socket.gethostname(), "browser": browser},
            native=target,
            coverage={
                "native": True,
                "native_consistency": "SQLite backed up; live LevelDB copy unverified",
                "cookies": "complete" if browser != "native" else "native-only",
                "origins": "native-only",
                "sessionStorage": "not yet exported",
            },
        )
    finally:
        shutil.rmtree(stage)
