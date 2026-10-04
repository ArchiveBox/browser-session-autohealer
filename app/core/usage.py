"""OpenCode token totals, cached with Django; no usage tables or sync jobs."""
import json
from collections import defaultdict

import httpx
from django.core.cache.backends.filebased import FileBasedCache

from .models import CheckRun, Run
from .storage import data_root

cache = FileBasedCache(str(data_root() / 'cache/tokens'), {'TIMEOUT': 3600, 'OPTIONS': {'MAX_ENTRIES': 5000}})


def count(messages):
    return sum(m['info'].get('tokens', {}).get('total') or 0
               for m in messages if m.get('info', {}).get('role') == 'assistant')


def total(values):
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def for_run(run, check_id=None):
    directory = data_root() / 'runs' / str(run.id)
    files = [directory / f'transcript-{check_id}.json'] if check_id is not None else list(directory.glob('transcript-*.json'))
    values = []
    for path in files:
        if path.is_file():
            signature = path.stat()
            key = f'{run.id}:{path.name}:{signature.st_mtime_ns}:{signature.st_size}'
            values.append(cache.get_or_set(key, lambda path=path: count(json.loads(path.read_text()))))
    if not values and run.status in {'running', 'finishing'}:
        from .inference import client
        agents = [directory / f'agent-{check_id}.json'] if check_id is not None else list(directory.glob('agent-*.json'))
        for path in agents:
            if not path.is_file():
                continue
            agent = json.loads(path.read_text())
            def read_live(agent=agent):
                try:
                    with client(agent['directory']) as api:
                        response = api.get(f"/session/{agent['session_id']}/message", timeout=2)
                        response.raise_for_status()
                        return count(response.json())
                except httpx.HTTPError:
                    return None
            values.append(cache.get_or_set(agent['session_id'], read_live, timeout=10))
    return total(values)


def grouped(kind, identifiers):
    groups = defaultdict(list)
    if kind == 'check':
        for result in CheckRun.query.filter(check_id__in=list(identifiers)).join('run'):
            groups[result.check_id].append(for_run(result.run, result.check_id))
    else:
        query = Run.query.all()
        if kind == 'persona':
            query = query.filter(persona__id__in=list(identifiers))
        elif kind == 'site':
            query = query.filter(scope__in=list(identifiers))
        else:
            query = query.filter(persona__id__in=list({key[0] for key in identifiers}), scope__in=list({key[1] for key in identifiers}))
        for run in query.join('persona'):
            key = run.persona.id if kind == 'persona' else run.scope if kind == 'site' else (run.persona.id, run.scope)
            groups[key].append(for_run(run))
    return {key: total(values) for key, values in groups.items()}


def display(value):
    if value is None:
        return {'text': '—', 'total': '', 'title': 'No recorded usage'}
    compact = f'{value / 1_000_000:.2f}m' if value >= 1_000_000 else f'{value / 1000:.1f}k' if value >= 1000 else str(value)
    return {'text': compact, 'total': value, 'title': f'{value:,} OpenCode tokens'}
