"""Browser-egress observations and immutable local MaxMind enrichment."""
import ipaddress
import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import maxminddb
from plain.postgres import transaction

from . import services
from .models import IPUsage, Run


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
    return Path(os.environ.get('MAXMIND_CITY_DB', '~/.local/share/GeoIP/GeoLite2-City.mmdb')).expanduser()


def database_error():
    path = database_path()
    if not path.is_file():
        return 'database_missing'
    try:
        with maxminddb.open_database(path) as reader:
            reader.metadata()
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
    except (RuntimeError, ValueError, KeyError):
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
