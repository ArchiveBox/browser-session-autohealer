"""Small typed selection language over existing persona/provider/task evidence."""
import ipaddress
import re
from copy import deepcopy
from functools import lru_cache

from . import services, storage
from .models import Check, CheckRun, Persona, PersonaProviderConfig, Provider
from .providers import adapter
from .site_scope import matches, normalize_sites, select_state


def validate(document):
    if not isinstance(document, dict):
        raise TypeError('A JSON object is required')
    allowed = {'require_all', 'prefer', 'recheck', 'timeout', 'allow_unhealthy',
               'provider_options', 'lifetime', 'actor'}
    if set(document) - allowed:
        raise ValueError('Unknown request fields: ' + ', '.join(sorted(set(document) - allowed)))
    spec = {'require_all': [], 'prefer': [], 'recheck': False, 'timeout': 0,
            'allow_unhealthy': False, 'provider_options': {}, 'lifetime': 1800, 'actor': 'API', **deepcopy(document)}
    for key in ('recheck', 'allow_unhealthy'):
        if type(spec[key]) is not bool:
            raise ValueError(f'{key} must be a boolean')
    if spec['recheck'] and spec['allow_unhealthy']:
        raise ValueError('A recheck always requires passing results')
    for key, low, high in (('timeout', -1, 3600), ('lifetime', 30, 259200)):
        if type(spec[key]) is not int or not low <= spec[key] <= high:
            raise ValueError(f'{key} must be an integer between {low} and {high}')
    if not isinstance(spec['provider_options'], dict):
        raise TypeError('provider_options must be an object')
    if not isinstance(spec['actor'], str) or len(spec['actor']) > 160:
        raise ValueError('actor must be a string of at most 160 characters')
    count = 0

    def condition(item, depth=0):
        nonlocal count
        count += 1
        if count > 100 or depth > 5 or not isinstance(item, dict):
            raise ValueError('Conditions must be objects, at most 100 and five levels deep')
        groups = set(item) & {'require_all', 'require_any', 'not'}
        if groups:
            if len(item) != 1:
                raise ValueError('A Boolean group must contain exactly one operator')
            op = next(iter(groups))
            children = [item[op]] if op == 'not' else item[op]
            if not isinstance(children, list) or not children:
                raise ValueError('Boolean groups cannot be empty')
            for child in children:
                condition(child, depth + 1)
            if op == 'not' and any(v['type'] == 'task' for v in leaves(children)):
                raise ValueError('Task health cannot be negated; use allow_unhealthy for diagnostics')
            return
        kind = item.get('type')
        fields = {'persona': {'id', 'name'}, 'provider': {'id', 'kind', 'name'},
                  'task': {'site', 'tasks', 'status', 'max_age', 'min_passed'},
                  'ip': {'ip', 'country', 'state', 'city'}}
        if kind not in fields or set(item) - fields[kind] - {'type'}:
            raise ValueError('Unknown condition type or field')
        if len(item) < 2:
            raise ValueError('Empty condition')
        for key in ('id', 'name', 'kind', 'city', 'state'):
            if key in item and (not isinstance(item[key], (str, int)) or isinstance(item[key], bool) or not str(item[key]).strip()):
                raise ValueError(f'{key} must be a non-empty identifier')
        if 'site' in item:
            if not isinstance(item['site'], str):
                raise ValueError('site must be a domain string')
            item['site'] = normalize_sites([item['site']])[0]
        if 'max_age' in item and (type(item['max_age']) is not int or item['max_age'] < 0):
            raise ValueError('max_age must be nonnegative seconds')
        if kind == 'task':
            if 'site' not in item:
                raise ValueError('Task conditions require a site')
            item.setdefault('status', 'healthy')
            item.setdefault('tasks', '*')
            if item['status'] != 'healthy':
                raise ValueError('Task conditions require healthy results; use allow_unhealthy for diagnostics')
            if item['tasks'] != '*' and (not isinstance(item['tasks'], list) or not item['tasks'] or
                    any(not isinstance(t, (str, int)) or isinstance(t, bool) for t in item['tasks'])):
                raise ValueError('tasks must be * or a nonempty list of task IDs or exact names')
            if 'min_passed' in item:
                minimum = item['min_passed']
                if type(minimum) is not int or minimum < 1:
                    raise ValueError('min_passed must be a positive integer')
                if item['tasks'] != '*' and minimum > len({str(t) for t in item['tasks']}):
                    raise ValueError('min_passed exceeds the number of selected tasks')
        if kind == 'ip':
            if 'country' in item and not re.fullmatch('[A-Z]{2}', str(item['country'])):
                raise ValueError('country must be a two-letter uppercase ISO code')
            if 'ip' in item:
                item['ip'] = str(ipaddress.ip_address(item['ip']))

    for key in ('require_all', 'prefer'):
        if not isinstance(spec[key], list):
            raise TypeError(f'{key} must be a list')
        for item in spec[key]:
            condition(item)
    return spec


def leaves(items):
    for item in items:
        if 'type' in item:
            yield item
        else:
            op = next(iter(item))
            yield from leaves([item[op]] if op == 'not' else item[op])


def same_id(obj, value):
    return str(value) in {str(obj.id), str(obj.uid)}


def configured_checks(persona, provider, spec):
    checks = list(Check.query.filter(account__persona=persona, provider=provider, enabled=True, mode='check')
                  .join('account__site'))
    selectors = [c for c in leaves(spec['require_all']) if c['type'] == 'task']
    if selectors:
        selectors += [c for c in leaves(spec['prefer']) if c['type'] == 'task']
        checks = [c for c in checks if any(task_matches(c, s) for s in selectors)]
    return [c for c in checks if matches(c.account.site.domain, provider.site_scope)
            and matches(c.account.site.domain, persona.config.get('siteScope'))]


def task_matches(check, condition):
    keys = condition.get('tasks', '*')
    return check.account.site.domain == condition['site'] and (keys == '*' or
        any(str(key) in {str(check.id), str(check.uid), check.name} for key in keys))


def effective_config(persona, provider, overrides=None):
    binding = PersonaProviderConfig.query.filter(persona=persona, provider=provider).first()
    config = {**provider.config, **(binding.config if binding else {}), **(overrides or {})}
    config = {key: value for key, value in config.items() if value is not None}
    adapter(provider).validate_config(config)
    return config


def persona_settings(persona, provider):
    settings = dict(persona.config)
    if provider.site_scope is not None:
        allowed = normalize_sites(provider.site_scope)
        configured = normalize_sites(settings.get('siteScope'))
        settings['siteScope'] = normalize_sites([s for s in dict.fromkeys([*(configured or []), *allowed])
            if matches(s, configured) and matches(s, allowed)])
    return settings


@lru_cache(maxsize=64)
def site_state(digest, site):
    # Checkpoints are immutable, so this cache cannot hide changed cookie values.
    state = select_state(storage.read_state(digest), [site])
    return {'cookies': sorted(state['cookies'], key=storage.cookie_key), 'origins': state['origins']}


def task_health(check, config, *, run=None, fresh=False, max_age=None):
    query = CheckRun.query.filter(check_id=check.id)
    if fresh:
        query = query.filter(run=run)
    result = query.order_by('-created_at', '-id').first()
    if not result or result.status == 'running':
        return 'pending', result
    if result.status != 'success' or not result.passed:
        return 'failed', result
    if not result.ended_at or (services.now() - result.ended_at).total_seconds() > (max_age if max_age is not None else check.interval_seconds):
        return 'stale', result
    if result.run.runtime.get('provider_config') != config or result.run.runtime.get('settings') != (run.runtime['settings'] if run else persona_settings(check.account.persona, check.provider)):
        return 'changed', result
    plan = next((p for p in result.run.plan if p['id'] == check.id), {})
    if plan.get('instruction') != check.instruction or plan.get('url') != check.url:
        return 'changed', result
    if not fresh:
        digest = run.base.digest if run else services.leader_for(check.account.persona).checkpoint.digest
        observed = result.run.tip or result.run.base.digest
        if site_state(digest, check.account.site.domain) != site_state(observed, check.account.site.domain):
            return 'changed', result
    return 'healthy', result


def evaluate(spec, persona, provider, *, run=None, preparing=False):
    config = effective_config(persona, provider, spec['provider_options']) if run is None else run.runtime['provider_config']
    checks = configured_checks(persona, provider, spec)
    results = []
    fresh = bool(spec['recheck'] and run)

    def test(c, path, negate=False):
        if 'type' not in c:
            op = next(iter(c))
            if op == 'not':
                value = test(c[op], path + '/not', not negate)
                return value if value in (None, Ellipsis) else not value
            children = [test(v, f'{path}/{op}/{i}', negate) for i, v in enumerate(c[op])]
            if op == 'require_all':
                return False if False in children else None if None in children else Ellipsis if Ellipsis in children else True
            return True if True in children else Ellipsis if Ellipsis in children else None if None in children else False
        kind, reason, counts = c['type'], '', {}
        ok = True
        if kind in {'persona', 'provider'}:
            obj = persona if kind == 'persona' else provider
            ok = all(same_id(obj, value) if key == 'id' else getattr(obj, key) == value
                     for key, value in c.items() if key != 'type')
            reason = 'not_matched'
        elif kind == 'task':
            selected = [t for t in checks if task_matches(t, c)]
            keys = c.get('tasks', '*')
            complete = bool(selected) and (keys == '*' or all(any(str(k) in {str(t.id), str(t.uid), t.name} for t in selected) for k in keys))
            minimum = c.get('min_passed', len(selected))
            if not selected or ('min_passed' not in c and not complete):
                ok, reason = False, 'no_checks'
            elif path.startswith('/require_all') and ((preparing and spec['recheck']) or spec['allow_unhealthy']):
                ok, reason = len(selected) >= minimum, 'not_enough_checks'
            else:
                states = [task_health(t, config, run=run, fresh=fresh, max_age=c.get('max_age'))[0] for t in selected]
                passed = states.count('healthy')
                counts = {'passed': passed, 'required': minimum, 'total': len(selected)}
                ok = passed >= minimum
                reason = 'not_enough_passing_checks' if 'min_passed' in c else next((s for s in states if s != 'healthy'), '')
        else:
            if run is None:
                return Ellipsis  # Deferred until allocation, distinct from missing evidence.
            observations = run.runtime.get('ip_observations', [])
            record = observations[-1] if observations else None
            ok, reason = True if record else None, 'unknown_ip'
            if record:
                for key in ('ip', 'country', 'state', 'city'):
                    if ok is None:
                        break
                    if key in c:
                        value = record['ip'] if key == 'ip' else record['geo'].get(key)
                        if not value:
                            ok, reason = None, 'unknown_location'
                            break
                        if str(value).casefold() != str(c[key]).casefold():
                            ok, reason = False, 'not_matched'
        matched = ok is not None and (not ok if negate else ok)
        results.append({'pointer': path, 'matched': matched, 'reason': '' if matched else reason, **counts})
        return ok

    matched = [test(c, f'/require_all/{i}') for i, c in enumerate(spec['require_all'])]
    if not spec['allow_unhealthy']:
        if not checks:
            matched.append(False)
            results.append({'pointer': '/require_all', 'matched': False, 'reason': 'no_checks'})
        elif not (preparing and spec['recheck']) and not any(c['type'] == 'task' for c in leaves(spec['require_all'])):
            # Explicit task clauses own their age limits and Boolean grouping.
            for check in checks:
                state, _ = task_health(check, config, run=run, fresh=fresh)
                if state != 'healthy':
                    matched.append(False)
                    results.append({'pointer': '/require_all', 'task_id': str(check.uid), 'matched': False, 'reason': state})
    score = tuple(test(c, f'/prefer/{i}') is True for i, c in enumerate(spec['prefer']))
    return (False not in matched and None not in matched and (preparing or Ellipsis not in matched)), score, [r for r in results if not r['matched'] and r['pointer'].startswith('/require_all')], checks, config


def candidates(spec):
    rows = []
    for persona in Persona.query.order_by('id'):
        for provider in Provider.query.filter(enabled=True).order_by('id'):
            try:
                ok, score, unmet, checks, config = evaluate(spec, persona, provider, preparing=True)
                adapter(provider).validate_handoff(config)
            except (ValueError, OSError) as exc:
                ok, score, unmet, checks, config = False, (), [{'pointer': '/provider_options', 'reason': str(exc)}], [], {}
            rows.append({'persona': persona, 'provider': provider, 'eligible': ok, 'score': score,
                         'unmet': unmet, 'checks': checks, 'config': config})
    return sorted(rows, key=lambda r: (r['eligible'], r['score']), reverse=True)
