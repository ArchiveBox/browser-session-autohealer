"""Shared tasks and deterministic outcome rules. Only browser work needs inference."""
from plain.postgres import transaction

from . import services
from .models import Check, CheckRun, Run, TaskRule
from .recovery.config import effective_binding, load, origin

PATTERNS = {
    'login_required': ('Restore sign-in', 'Sign into the account using configured credential placeholders. If already signed in, preserve the session.'),
    'email_code_required': ('Verify email', 'Complete the email verification using a recent code or link from the configured integration.'),
    'sms_code_required': ('Verify text message', 'Complete verification using a recent code from the configured messaging integration.'),
    'otp_required': ('Verify authenticator', 'Complete verification using an authenticator-code placeholder from the configured integration.'),
    'cookie_consent': ('Dismiss cookie notice', 'Dismiss the blocking cookie notice using the minimum necessary cookies.'),
    'promo_blocked': ('Dismiss popup', 'Close the promotional, newsletter, or tutorial overlay blocking the requested content. Do not subscribe or purchase.'),
}


def fix_binding(task):
    if task.pattern.startswith('signup:'):
        from .onboarding import binding
        return binding(task)
    cfg = load()
    binding = effective_binding(cfg, task.account, task)
    if not binding.get('fields') and task.source_task:
        binding = effective_binding(cfg, task.account, task.source_task)
    from .onboarding import saved_login
    saved = saved_login(task.account)
    if saved:
        binding['fields'] = {**saved['fields'], **binding.get('fields', {})}
        binding.setdefault('origins', saved['origins'])
        binding['authentication'] = saved['authentication']
    binding.setdefault('start_url', task.url)
    binding.setdefault('origins', [origin(binding['start_url'])])
    return binding


def ensure_fix(task, state):
    """Instantiate a common pattern for this exact account/provider, once."""
    if task.mode != 'check' or state not in PATTERNS:
        return None
    with transaction.atomic():
        Check.query.for_update().get(id=task.id)
        fix = Check.query.filter(source_task=task, pattern=state, mode='fix').first()
        if not fix:
            label, instruction = PATTERNS[state]
            fix = Check.query.create(account=task.account, provider=task.provider,
                source_task=task, pattern=state, mode='fix', name=f'{label} · {task.account.site.domain}',
                instruction=instruction + ' Use browser-harness to inspect the page. '
                    'Save a final screenshot. Stop on CAPTCHA, missing credentials, or a rejected secret. '
                    'Do not reset passwords, change account settings, or interact with feed content.',
                url=task.url, enabled=True, interval_seconds=task.interval_seconds)
            services.record_definition(fix, 'account-checker:pattern/' + state)
        TaskRule.query.get_or_create(source=task, target=fix, status='failure', state=state)
        TaskRule.query.get_or_create(source=fix, target=task, status='success', state='')
        return fix


def dispatch(run):
    """Replay-safe rule evaluation; each edge may fire once per causal chain."""
    if not run.checked_in_at:
        return []
    observations = list(CheckRun.query.filter(run=run))
    for result in observations:
        task = Check.query.get(id=result.check_id)
        if task.mode == 'check' and not result.passed:
            ensure_fix(task, result.state)
    queued = []
    with transaction.atomic():
        Run.query.for_update().get(id=run.id)
        ancestors, cursor = set(run.runtime.get('completed_rules', [])), run
        while cursor:
            if cursor.rule:
                ancestors.add(cursor.rule.id)
            cursor = cursor.triggered_by
        if len(ancestors) >= 8:
            return []
        for result in observations:
            for rule in TaskRule.query.filter(source__id=result.check_id, enabled=True, status=result.status).join('target__account__persona', 'target__account__site', 'target__provider'):
                if rule.state and rule.state != result.state or rule.id in ancestors:
                    continue
                if Run.query.filter(triggered_by=run, rule=rule).exists():
                    continue
                target = rule.target
                if not target.enabled or not target.provider.enabled:
                    continue
                # A rule never crosses an account or provider without an explicit new run.
                if target.account.persona.id != run.persona.id or target.provider.id != run.provider.id or target.account.site.domain != run.scope:
                    continue
                if run.runtime.get('recovery') and (run.issues or not run.tip):
                    continue
                child = services.checkout(run.persona.id, run.provider.id, run.scope,
                    f'Rule #{rule.id}', check_ids=[target.id])
                child.triggered_by, child.rule = run, rule
                child.update(fields=['triggered_by', 'rule', 'runtime'])
                queued.append(child)
    return queued


def verify_fix(run, fix, state):
    """Verify a fix inside the same checkout before allowing it onto mainline."""
    from . import inference
    from .providers import browser_command, watch_browser

    rules = list(TaskRule.query.filter(source__id=fix['id'], enabled=True, status='success', state='')
        .join('target__account__site', 'target__provider'))
    for rule in rules:
        task = rule.target
        signup_verification = task.pattern.startswith('signup-verify:') and run.runtime.get('recovery', {}).get('binding', {}).get('signup')
        if task.mode != 'check' or (not task.enabled and not signup_verification) or task.provider.id != run.provider.id or task.account.persona.id != run.persona.id or task.account.site.domain != run.scope:
            continue
        plan = services.check_plan(task)
        run.plan = [*run.plan, plan]
        run.runtime = {**run.runtime, 'completed_rules': [*run.runtime.get('completed_rules', []), rule.id]}
        run.update(fields=['plan', 'runtime'])
        result = services.start_check(run.id, plan)
        try:
            target = browser_command('prepare', run, state=state)
            with watch_browser(run, target, state):
                evidence, classification = inference.check_access(run, plan, target)
            for issue in evidence.get('issues', []):
                services.record_issue(run.id, issue)
            services.observe(run.id, plan, evidence, classification, check_run=result)
        except Exception as error:
            services.fail_check(result, plan, str(error)[:400])
            raise
