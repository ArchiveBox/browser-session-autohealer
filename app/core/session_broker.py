"""Provision direct provider connections; never relay application CDP traffic."""
import fcntl
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from plain.postgres import transaction
from plain.postgres.db import return_database_connection

from . import inference, network, services, storage
from .models import CheckRun, Run, SessionRequest
from .providers import adapter, browser_command, browser_invocation, watch_browser
from .session_conditions import candidates, evaluate, validate
from .site_scope import select_state

TERMINAL = {'closed', 'failed', 'cancelled'}


def update(request_id, **values):
    with transaction.atomic():
        row = SessionRequest.query.for_update().get(id=request_id)
        for key, value in values.items():
            setattr(row, key, value)
        row.update(fields=list(values))
    return row


def create(document, key=None):
    spec = validate(document)
    if key and len(key) > 200:
        raise ValueError('Idempotency-Key must be at most 200 characters')
    existing = SessionRequest.query.filter(key=key).first() if key else None
    if existing:
        if existing.spec != spec:
            raise ValueError('Idempotency-Key was already used with a different request')
        return existing
    ranked = candidates(spec)
    eligible = any(c['eligible'] for c in ranked)
    blocked = spec['timeout'] == 0 and (not eligible or spec['recheck'])
    deadline = services.now() + timedelta(seconds=spec['timeout']) if spec['timeout'] > 0 else None
    values = {'spec': spec, 'deadline': deadline, 'status': 'failed' if blocked else 'queued',
              'detail': {'code': 'recheck_requires_wait' if spec['recheck'] else 'conditions_unmet',
                         'candidates': diagnostics(ranked)} if blocked else {}}
    if key:
        row, _ = SessionRequest.query.get_or_create(key=key, defaults=values)
        if row.spec != spec:
            raise ValueError('Idempotency-Key was already used with a different request')
        return row
    return SessionRequest.query.create(**values)


def diagnostics(ranked):
    return [{'persona_id': str(c['persona'].uid), 'provider_id': str(c['provider'].uid),
             'unmet': c['unmet']} for c in ranked[:20]]


def state(request):
    run = request.run
    result = {'id': str(request.uid), 'status': request.status, 'results': [], 'ips': [],
              'url': f'/api/sessions/{request.uid}', 'detail': request.detail,
              'created_at': request.created_at.isoformat(),
              'expires_at': request.expires_at.isoformat() if request.expires_at else None}
    if run:
        result.update(session_id=run.id, persona_id=str(run.persona.uid), provider_id=str(run.provider.uid),
                      checkpoint=run.base.digest, started_at=run.started_at.isoformat() if run.started_at else None,
                      ended_at=run.finished_at.isoformat() if run.finished_at else None,
                      promoted=run.promoted, promotion_reason=run.promotion_reason,
                      results=[{'task_id': str(p['uid']), 'name': p['name'], 'status': o.status, 'ended_at': o.ended_at.isoformat() if o.ended_at else None,
                                'url': f'/runs/{run.id}/checks/{o.check_id}'}
                               for o in CheckRun.query.filter(run=run) for p in run.plan if p['id'] == o.check_id],
                      ips=run.runtime.get('ip_observations', []))
        if request.status == 'ready':
            result.update(adapter(run.provider).connection(run))
    return result


def release(request_id, *, success=None, message=''):
    if success is not None and type(success) is not bool:
        raise ValueError('success must be a boolean')
    if not isinstance(message, str) or len(message) > 400:
        raise ValueError('message must be a string of at most 400 characters')
    with transaction.atomic():
        row = SessionRequest.query.for_update().get(id=request_id)
        if row.status in TERMINAL | {'releasing', 'finalizing'}:
            return row
        row.detail = {**row.detail, 'client_success': success, 'client_message': message}
        row.status = 'releasing' if row.run else 'cancelled'
        row.update(fields=['detail', 'status'])
        return row


def start_watch(run, saved):
    command, document, env, work = browser_invocation('watch_session', run, state=saved)
    for name in ('session-watch.ready', 'session-watch.stop', 'session-watch.ended'):
        (work / name).unlink(missing_ok=True)
    with (work / 'session-watch.log').open('a') as log:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=log, text=True,
                                   env=env, start_new_session=True)
        process.stdin.write(json.dumps(document))
        process.stdin.close()
    runtime = {**run.runtime, 'session_watch_pid': process.pid}
    run.runtime = runtime
    run.update(fields=['runtime'])
    deadline = time.monotonic() + 15
    while not (work / 'session-watch.ready').exists():
        if process.poll() is not None or time.monotonic() >= deadline:
            (work / 'session-watch.stop').touch()
            raise RuntimeError('Persona settings observer did not start')
        time.sleep(.05)


def run_checks(run, saved, request_id):
    for plan in run.plan:
        request = SessionRequest.query.get(id=request_id)
        if request.status in TERMINAL | {'releasing'} or request.deadline and services.now() >= request.deadline:
            raise RuntimeError('Readiness deadline expired or request cancelled')
        target = next((t for t in run.runtime['tabs'] if t['check_id'] == plan['id']), None)
        if not target:
            raise RuntimeError('Prepared task tab is missing')
        execution = services.start_check(run.id, plan)
        try:
            with watch_browser(run, target, saved):
                result, classification = inference.check_access(run, plan, target)
            services.observe(run.id, plan, result, classification, check_run=execution)
            for issue in result.get('issues', []):
                services.record_issue(run.id, issue)
        except Exception:
            services.fail_check(execution, plan, 'Task execution did not complete')
            raise


def prepare(request_id):
    request = SessionRequest.query.get(id=request_id)
    if request.status != 'preparing':
        return
    run = None
    stage = 'selection'
    try:
        ranked = candidates(request.spec)
        candidate = next((c for c in ranked if c['eligible']), None)
        if candidate is None:
            update(request_id, status='failed' if request.spec['timeout'] == 0 else 'queued',
                   detail={'code': 'conditions_unmet', 'candidates': diagnostics(ranked)})
            return
        persona, provider, checks = candidate['persona'], candidate['provider'], candidate['checks']
        scope = checks[0].account.site.domain if checks and len({c.account.site.domain for c in checks}) == 1 else '*'
        # The Run is the checkout. An external consumer owns use, Autohealer owns lifecycle.
        run = services.checkout(persona.id, provider.id, scope, request.spec['actor'],
                                check_ids=[c.id for c in checks], external=True)
        run.runtime.update(managed_request=str(request.uid), provider_config=candidate['config'])
        run.update(fields=['runtime'])
        request = update(request_id, run=run)
        runtime = adapter(provider)
        required = run.runtime['settings'].get('requiredCapabilities', ['cookies', 'localStorage', 'sessionStorage'])
        if any(not runtime.capabilities.get(key) for key in required):
            raise RuntimeError('Provider cannot preserve the required persona state')
        saved = select_state(storage.read_state(run.base.digest), run.runtime['settings'].get('siteScope'))
        return_database_connection()
        stage = 'launch'
        launched = runtime.launch(run)
        run.runtime = {**run.runtime, **launched}
        run.update(fields=['runtime'])
        stage = 'restore'
        if run.base.coverage.get('cookies') != 'native-only':
            browser_command('seed', run, state=saved)
        tabs = [{**browser_command('prepare', run, state=saved), 'check_id': p['id'], 'name': p['name']} for p in run.plan]
        if not tabs:
            tabs = [browser_command('prepare', run, state=saved)]
        run.runtime = {**run.runtime, 'tabs': tabs}
        run.update(fields=['runtime'])
        start_watch(run, saved)
        stage = 'network'
        network.observe(run)
        # Resolve reachability before handing out a dead/internal Docker address.
        runtime.connection(run)
        if request.spec['recheck']:
            stage = 'recheck'
            if request.spec['timeout'] == 0:
                raise RuntimeError('Fresh checks require a positive timeout or -1')
            run_checks(run, saved, request_id)
        run = Run.query.get(id=run.id)
        stage = 'conditions'
        ok, _, unmet, _, _ = evaluate(request.spec, persona, provider, run=run)
        current = SessionRequest.query.get(id=request_id)
        if (storage.data_root() / 'runs' / str(run.id) / 'session-settings-error').exists():
            raise RuntimeError('Persona settings could not be applied')
        if not ok:
            update(request_id, detail={'code': 'conditions_unmet', 'unmet': unmet})
            raise RuntimeError('Required conditions were not satisfied')
        if current.status in TERMINAL | {'releasing'} or current.deadline and services.now() >= current.deadline:
            raise RuntimeError('Readiness deadline expired or request cancelled')
        # Lock only the handoff decision, never a provider call or task execution.
        with transaction.atomic():
            current = SessionRequest.query.for_update().get(id=request_id)
            if current.status != 'preparing':
                raise RuntimeError('Request was cancelled')
            current.status = 'ready'
            current.expires_at = min(services.now() + timedelta(seconds=request.spec['lifetime']),
                run.started_at + timedelta(seconds=runtime.session_lifetime(candidate['config'])))
            if current.expires_at <= services.now():
                raise RuntimeError('Provider lifetime elapsed before readiness')
            current.detail = {'verified': not request.spec['allow_unhealthy'], 'fresh': request.spec['recheck']}
            current.update(fields=['status', 'expires_at', 'detail'])
    except Exception as exc:  # noqa: BLE001 - every preparation failure must release the owned browser
        # Provider/driver exceptions can include bearer URLs. API errors remain structured and private.
        current = SessionRequest.query.get(id=request_id)
        detail = {**current.detail, 'code': current.detail.get('code', 'preparation_failed'),
                  'stage': stage, 'error_type': type(exc).__name__}
        if current.deadline and services.now() >= current.deadline:
            detail['code'] = 'deadline_exceeded'
        if run:
            services.record_issue(run.id, 'Session preparation did not satisfy the request')
            update(request_id, detail=detail, status='releasing')
            finalize(request_id, failed=True)
        else:
            update(request_id, detail=detail, status='failed')
    finally:
        return_database_connection()


def finalize(request_id, *, failed=False):
    request = SessionRequest.query.get(id=request_id)
    run = request.run
    if not run:
        return update(request_id, status='cancelled')
    if run.checked_in_at:
        return update(request_id, status='failed' if failed else 'closed')
    runtime = adapter(run.provider)
    work = storage.data_root() / 'runs' / str(run.id)
    saved = select_state(storage.read_state(run.base.digest), run.runtime['settings'].get('siteScope'))
    exported = None
    stopped = False
    try:
        if request.detail.get('client_message'):
            services.record_issue(run.id, request.detail['client_message'])
        if run.runtime.get('cdp'):
            # A successful client report is verified against the resulting browser state.
            if not failed and request.detail.get('client_success') is True:
                # Check-in is asynchronous and has no checkout-readiness deadline.
                update(request_id, status='finalizing', deadline=None)
                run_checks(run, saved, request_id)
            network.observe(run)
            exported = browser_command('export', run, state=saved)
            from .worker import verify_cookie_preservation
            verify_cookie_preservation(run, saved, exported)
    except Exception:  # noqa: BLE001 - export failure must not bypass browser termination
        services.record_issue(run.id, 'Final browser state could not be verified or exported')
    finally:
        (work / 'session-watch.stop').touch()
        try:
            if run.runtime.get('cdp'):
                runtime.stop(run)
            stopped = True
        except Exception:  # noqa: BLE001 - failed termination must prevent promotion
            services.record_issue(run.id, 'Provider termination could not be confirmed')
        run = Run.query.get(id=run.id)
        run.finished_at, run.status = services.now(), 'finishing'
        run.update(fields=['finished_at', 'status'])
    if exported and stopped:
        try:
            services.checkpoint_run(run.id, exported, native=runtime.native_path(run),
                coverage={'native': runtime.capabilities['native'], 'cookies': 'complete',
                          'origins': 'visited origins', 'settings': 'complete'})
        except Exception:  # noqa: BLE001 - storage failure is a discarded session, never a leader
            exported = None
            services.record_issue(run.id, 'Final browser state could not be saved')
    services.finish(run.id, success=not failed and request.detail.get('client_success') is True,
                    export_complete=bool(exported and stopped))
    if failed:
        from .tasks import dispatch
        dispatch(Run.query.get(id=run.id))  # Existing asynchronous recovery rules, never inline fixes.
    update(request_id, status='failed' if failed else 'closed')
    return_database_connection()


def expire(request):
    if request.status == 'ready' and request.expires_at and services.now() >= request.expires_at:
        return release(request.id, message='Session expired')
    if request.deadline and services.now() >= request.deadline and request.status in {'queued', 'preparing'}:
        with transaction.atomic():
            request = SessionRequest.query.for_update().get(id=request.id)
            if request.status in {'queued', 'preparing'}:
                request.detail = {**request.detail, 'code': 'deadline_exceeded'}
                request.status = 'failed'
                request.update(fields=['status', 'detail'])
    return request


def supervise(request_id):
    request = SessionRequest.query.get(id=request_id)
    if request.status == 'failed':
        return finalize(request_id, failed=True)
    if request.status in {'releasing', 'finalizing'}:
        return finalize(request_id)
    if request.status != 'ready':
        return
    run = request.run
    work = storage.data_root() / 'runs' / str(run.id)
    ended = (work / 'session-watch.ended').exists()
    try:
        os.kill(run.runtime['session_watch_pid'], 0)
    except (ProcessLookupError, KeyError):
        ended = True
    expired = request.expires_at and services.now() >= request.expires_at
    if ended or expired or (work / 'session-settings-error').exists():
        release(request.id, message='Browser ended' if ended else 'Session expired' if expired else 'Persona settings failed')
        finalize(request.id)


def job(fn, identifier):
    try:
        fn(identifier)
    finally:
        return_database_connection()


def loop(once=False):
    # One lifecycle coordinator per collection; independent task workers still run normally.
    with (storage.data_root() / 'session-broker.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('A session broker is already running for this collection') from None
        for request in list(SessionRequest.query.filter(status='preparing')):
            if request.run:
                services.record_issue(request.run.id, 'Session preparation was interrupted')
                finalize(request.id, failed=True)
            else:
                update(request.id, status='queued')
        _loop(once)


def _loop(once):
    """Bounded parallel preparation; ready browsers consume no worker thread."""
    jobs = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        while True:
            for key, future in list(jobs.items()):
                if future.done():
                    future.result()
                    del jobs[key]
            pending = list(SessionRequest.query.exclude(status__in=list(TERMINAL)).order_by('created_at'))
            pending += list(SessionRequest.query.filter(status='failed', run__isnull=False, run__checked_in_at__isnull=True))
            for request in pending:
                if request.id in jobs:
                    continue
                if request.deadline and services.now() >= request.deadline and request.status == 'queued':
                    update(request.id, status='failed', detail={**request.detail, 'code': 'deadline_exceeded'})
                elif request.status == 'queued' and len(jobs) < 4:
                    with transaction.atomic():
                        locked = SessionRequest.query.for_update().get(id=request.id)
                        if locked.status != 'queued':
                            continue
                        locked.status = 'preparing'
                        locked.detail = {**locked.detail, 'owner_pid': os.getpid()}
                        locked.update(fields=['status', 'detail'])
                    jobs[request.id] = pool.submit(job, prepare, request.id)
                elif request.status in {'ready', 'releasing', 'finalizing', 'failed'} and len(jobs) < 4:
                    jobs[request.id] = pool.submit(job, supervise, request.id)
            return_database_connection()
            if once:
                for future in jobs.values():
                    future.result()
                return
            time.sleep(2)
