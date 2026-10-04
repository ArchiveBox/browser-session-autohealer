"""Checkpoint ancestry and leader-reference history are separate causal relations."""
from itertools import pairwise

from .core import health
from .core.models import Checkpoint, CheckRun, Leader, LeaderChange, Persona, Run
from .views import Base


def graph(persona):
    runs = list(Run.query.filter(persona=persona).join('provider', 'base').order_by('created_at'))
    checkpoints = list(Checkpoint.query.filter(persona=persona).order_by('created_at'))
    changes = list(LeaderChange.query.filter(persona=persona).join('checkpoint', 'run__provider').order_by('created_at', 'id'))
    leader = Leader.query.get(persona=persona).checkpoint
    by_run = {r.id: r for r in runs}
    events, edges = [], []

    def tone(run):
        return 'running' if run.status in health.ACTIVE else 'success' if run.status == 'success' else 'failed'

    def event(key, at, column, label, color='neutral', **data):
        item = {'key': key, 'at': at, 'lane': column, 'label': label, 'tone': color,
                'run': None, 'cp': None, 'image': '', 'url': '', 'detail': '', 'digest': '', 'changes': [], **data}
        events.append(item)
        return item

    def diff_counts(cp):
        return [{'kind': kind, 'count': cp.summary.get('changes', {}).get(category + '_' + kind, 0),
                 'label': category, 'symbol': '+' if kind == 'added' else '−' if kind == 'removed' else '~'}
                for category in ('cookies', 'settings', 'origins') for kind in ('added', 'removed', 'updated')
                if cp.summary.get('changes', {}).get(category + '_' + kind)]

    # Leader events alone form the mainline. Unadopted imports are not on it.
    for change in changes:
        cp, run = change.checkpoint, change.run
        label = 'Persona leader' if change == changes[-1] else {'initial': 'Initial profile', 'import': 'Imported', 'restore': 'Restored', 'adopt': 'Adopted'}[change.kind]
        event(f'leader-{change.id}', change.created_at, 0, label, 'success',
              run=run, cp=cp, digest=cp.digest, url=f'/runs/{run.id}' if run else f'/personas/{persona.id}',
              detail=run.scope if run else change.actor)
    for result in CheckRun.query.filter(run__id__in=list(by_run)).join('run').order_by('created_at'):
        run = by_run[result.run.id]
        event(f'check-{result.id}', result.ended_at or result.created_at, 1,
              'Checking' if result.status == 'running' else 'Passed' if result.passed else 'Failed',
              'running' if result.status == 'running' else 'success' if result.passed else 'failed',
              run=run, image=health.result_image(result)[0], url=f'/runs/{run.id}/checks/{result.check_id}',
              detail=next((p['name'] for p in run.plan if p['id'] == result.check_id), run.scope))

    # Only canonical reference changes and actual session work belong here.
    # An unadopted import is a saved snapshot, not a running browser branch.
    events = [e for e in events if e['run'] or e['lane'] == 0]
    events.sort(key=lambda e: (e['at'], e['key']))
    canonical = [e for e in events if e['lane'] == 0]
    task_events = [e for e in events if e['key'].startswith('check-')]
    times = sorted({e['at'] for e in canonical + task_events} | {r.created_at for r in runs} | {r.checked_in_at for r in runs if r.checked_in_at and not r.promoted})
    positions = {at: 100 + index * 125 for index, at in enumerate(times)}
    # Each side is reused only after that session has closed. Alternating sides
    # keeps parallel sessions legible without pretending they share one lineage.
    tracks, occupied = {}, {}
    for index, run in enumerate(runs):
        preference = 1 if index % 2 == 0 else -1
        for distance in range(1, len(runs) + 2):
            track = preference * distance
            if track not in occupied or (occupied[track] and occupied[track] < run.created_at):
                occupied[track] = run.checked_in_at
                tracks[run.id] = track
                break
    above = max([0, *[t for t in tracks.values() if t > 0]])
    below = max([0, *[-t for t in tracks.values() if t < 0]])
    center = 80 + above * 155
    height = center + below * 155 + 120
    width = max(1050, 230 + len(times) * 125)
    points = {}
    for e in canonical + task_events:
        e['x'] = positions[e['at']]
        e['y'] = center if e['lane'] == 0 else center - tracks[e['run'].id] * 155
        points[e['key']] = e

    def add_edge(a, b, color, kind, **data):
        p, q = points[a], points[b]
        middle = (p['x'] + q['x']) / 2
        edges.append({'from': a, 'to': b, 'path': f"M{p['x']} {p['y']} C{middle} {p['y']} {middle} {q['y']} {q['x']} {q['y']}",
                      'tone': color, 'kind': kind, 'provider_color': by_run[data['run']].provider.display_color if data.get('run') else '', **data})

    for a, b in pairwise(canonical):
        add_edge(a['key'], b['key'], 'success', 'main')
    sessions = []
    for run in runs:
        y, start_x = center - tracks[run.id] * 155, positions[run.created_at]
        adopted = [e for e in canonical if e['digest'] == run.base.digest and e['at'] <= run.created_at]
        # Checkouts freeze their exact canonical revision at creation. The
        # branch source remains that revision if another session wins later.
        source = adopted[-1] if adopted else None
        tasks = [e for e in task_events if e['run'].id == run.id]
        finish = next((e for e in canonical if e['run'] and e['run'].id == run.id), None)
        end_x = finish['x'] if finish else positions[run.checked_in_at] if run.checked_in_at else width - 60
        start_key, end_key = f'start-{run.id}', f'end-{run.id}'
        points[start_key] = {'key': start_key, 'x': start_x, 'y': y, 'at': run.created_at, 'lane': tracks[run.id]}
        points[end_key] = {'key': end_key, 'x': end_x, 'y': y, 'at': run.checked_in_at, 'lane': tracks[run.id]}
        if source:
            add_edge(source['key'], start_key, tone(run), 'fork', run=run.id, base=run.base.digest)
        chain = [start_key, *[e['key'] for e in tasks], finish['key'] if finish else end_key]
        for a, b in pairwise(chain):
            add_edge(a, b, tone(run), 'session', run=run.id, base=run.base.digest)
        cp = next((c for c in checkpoints if c.digest == run.tip), None)
        sessions.append({'run': run, 'y': y, 'x': start_x, 'end_x': end_x, 'tone': tone(run),
                         'tasks': tasks, 'adopted': bool(finish), 'active': run.status in health.ACTIVE,
                         'changes': diff_counts(cp) if cp else [], 'base': run.base.digest,
                         'source': source['key'] if source else '', 'tip': run.tip})
    return {'events': list(points.values()), 'edges': edges, 'canonical': canonical,
            'sessions': sessions, 'graph_width': width, 'graph_height': height, 'center_y': center,
            'leader': leader, 'checkpoints': list(reversed(checkpoints)),
            'providers': list({r.provider.id: r.provider for r in runs}.values()),
            'active': any(r.status in health.ACTIVE for r in runs)}


class LineageView(Base):
    template_name = 'lineage.html'

    def get_template_context(self):
        persona = Persona.query.get(id=self.url_kwargs['id'])
        return {**super().get_template_context(), 'nav': 'personas', 'persona': persona, **graph(persona)}
