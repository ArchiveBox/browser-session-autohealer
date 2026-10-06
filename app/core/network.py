"""Browser-egress observations and immutable local MaxMind enrichment."""
import fcntl
import hashlib
import ipaddress
import logging
import os
import subprocess
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import httpx
import maxminddb
import pycountry
from plain.postgres import transaction
from plain.runtime import settings

from . import services
from .models import IPUsage, Run


def normalize_location(location):
    location = dict(location)
    country, state = location.get('country'), location.get('state')
    if country and state:
        divisions = pycountry.subdivisions.get(country_code=country) or []
        for division in divisions:
            code = division.code.removeprefix(country + '-')
            if str(state).casefold() in {code.casefold(), division.code.casefold(), division.name.casefold()}:
                location.update(state=code, state_name=division.name)
                break
    return location


@lru_cache(maxsize=1024)
def lookup(ip, path, version):
    with maxminddb.open_database(path) as reader:
        record = reader.get(ip) or {}
        subdivision = (record.get('subdivisions') or [{}])[0]
        return {'country': record.get('country', {}).get('iso_code', ''),
                'country_name': record.get('country', {}).get('names', {}).get('en', ''),
                'state': subdivision.get('iso_code', ''),
                'state_name': subdivision.get('names', {}).get('en', ''),
                'city': record.get('city', {}).get('names', {}).get('en', ''),
                'database': reader.metadata().database_type,
                'database_build': reader.metadata().build_epoch}


def database_path():
    return Path(os.environ.get('MAXMIND_CITY_DB') or settings.APP_DATA_DIR / 'geoip' / 'GeoLite2-City.mmdb').expanduser()


def ensure_database(*, force=False):
    """Provision at startup; workers refresh daily, outside database transactions.

    Downloads are public datasets, never per-IP requests. An explicitly supplied
    database belongs to its operator and must not be replaced by the updater.
    """
    if os.environ.get('MAXMIND_CITY_DB'):
        return not database_error()
    path = database_path()
    lock_path = path.with_suffix('.lock')
    download = path.with_suffix('.download')

    def fresh():
        return path.is_file() and time.time() - path.stat().st_mtime < 86400 and not database_error()

    try:
        if not force and fresh():
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open('a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not force and fresh():
                return True
            # Persist failed-attempt backoff across processes and restarts.
            lock.seek(0)
            attempted = lock.read()
            if not force and attempted and time.time() - float(attempted) < 300:
                return not database_error()
            lock.seek(0)
            lock.truncate()
            lock.write(str(time.time()).encode())
            lock.flush()
            try:
                with httpx.Client(follow_redirects=True, timeout=60) as client:
                    response = client.get('https://api.github.com/repos/P3TERX/GeoLite.mmdb/releases/latest')
                    response.raise_for_status()
                    asset = next(a for a in response.json()['assets'] if a['name'] == path.name)
                    expected = str(asset.get('digest') or '').removeprefix('sha256:')
                    if len(expected) != 64 or not 0 < asset['size'] <= 256 * 1024 * 1024:
                        raise ValueError('Invalid GeoLite release metadata')
                    if not database_error():
                        with path.open('rb') as source:
                            if hashlib.file_digest(source, 'sha256').hexdigest() == expected:
                                path.touch()
                                return True
                    digest, size = hashlib.sha256(), 0
                    with client.stream('GET', asset['browser_download_url']) as response, download.open('wb') as out:
                        response.raise_for_status()
                        for chunk in response.iter_bytes(1024 * 1024):
                            size += len(chunk)
                            if size > asset['size']:
                                raise ValueError('GeoLite download exceeds release size')
                            digest.update(chunk)
                            out.write(chunk)
                    if size != asset['size'] or digest.hexdigest() != expected:
                        raise ValueError('GeoLite checksum mismatch')
                    with maxminddb.open_database(download) as reader:
                        if reader.metadata().database_type != 'GeoLite2-City':
                            raise ValueError('Expected a GeoLite2 City database')
                    download.replace(path)
                    return True
            finally:
                download.unlink(missing_ok=True)
    except (httpx.HTTPError, OSError, ValueError, TypeError, KeyError, StopIteration, maxminddb.InvalidDatabaseError) as exc:
        logging.getLogger(__name__).warning('GeoIP update failed (%s); keeping any installed database', type(exc).__name__)
        return False


def database_error():
    path = database_path()
    if not path.is_file():
        return 'database_missing'
    try:
        with maxminddb.open_database(path) as reader:
            if reader.metadata().database_type not in {'GeoLite2-City', 'GeoIP2-City'}:
                return 'database_invalid'
    except (ValueError, OSError, maxminddb.InvalidDatabaseError):
        return 'database_invalid'
    return ''


def geolocate(ip):
    path = database_path()
    if not path.is_file():
        return {'unavailable': 'database_missing'}
    try:
        return lookup(ip, str(path), path.stat().st_mtime_ns)
    except (ValueError, OSError, maxminddb.InvalidDatabaseError):
        return {'unavailable': 'database_invalid'}


def label(ip, geo):
    country = geo.get('country', '')
    flag = ''.join(chr(127397 + ord(c)) for c in country) if len(country) == 2 else '◎'
    location = ', '.join(v for v in (geo.get('city'), geo.get('state_name') or geo.get('state'), geo.get('country_name') or country) if v)
    return {'text': f'{flag} {ip}', 'sub': location or '—'}


def record(run, ip, *, source='browser'):
    ip = str(ipaddress.ip_address(ip))
    observation = {'ip': ip, 'at': services.now().isoformat(), 'source': source,
                   'geo': geolocate(ip)}
    with transaction.atomic():
        locked = Run.query.for_update().get(id=run.id)
        if locked.checked_in_at:
            raise ValueError('Session is already checked in')
        observations = locked.runtime.get('ip_observations', [])
        locked.runtime = {**locked.runtime, 'ip_observations': [*observations, observation]}
        locked.update(fields=['runtime'])
    run.runtime = locked.runtime
    return observation


def observe(run):
    from .providers import browser_command
    try:
        result = browser_command('egress', run, url=os.environ.get('SESSION_IP_PROBE_URL', 'https://api64.ipify.org?format=json'))
        return record(run, result['ip'])
    except (RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired):
        # An unavailable audit endpoint must not change an otherwise healthy login.
        # A requested IP condition still fails explicitly with unknown_ip.
        return None


def finalize(run):
    """Called inside the check-in transaction; idempotency is the session lock."""
    observations = run.runtime.get('ip_observations', [])
    groups = {}
    for observation in observations:
        groups.setdefault(observation['ip'], []).append(observation)
    for ip, samples in groups.items():
        IPUsage.query.create(run=run, ip=ip, source=samples[-1]['source'],
            started_at=datetime.fromisoformat(samples[0]['at']),
            ended_at=datetime.fromisoformat(samples[-1]['at']), geo=samples[-1]['geo'])
