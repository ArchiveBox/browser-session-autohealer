"""Read-only acceptance against the explicitly linked real 1Password item."""
import json
import os
from pathlib import Path

import pytest

from app.core.models import Check, CheckRun, Run
from app.core.recovery import config, sources
from app.core.recovery.broker import Broker
from app.core.recovery.redaction import read
from app.core.storage import data_root


def test_linkedin_binding_and_real_secret_delivery(tmp_path):
    cfg = config.load()
    assert cfg['onepassword']['vault'] == os.environ['ACCOUNT_CHECKER_TEST_VAULT']
    assert sources.status(cfg, 'onepassword') == 'Connected'
    for check_id in (9, 10):
        check = Check.query.get(id=check_id)
        assert config.effective_binding(cfg, check.account, check)['fields'] == {}
    for check_id in (11, 12):
        check = Check.query.get(id=check_id)
        binding = config.effective_binding(cfg, check.account, check)
        assert binding['origins'] == ['https://www.linkedin.com']
        assert set(binding['fields']) == {'username', 'password'}
        for purpose in ('username', 'password'):
            assert binding['fields'][purpose]['reference'] == (
                f"op://{os.environ['ACCOUNT_CHECKER_TEST_VAULT']}/{os.environ['ACCOUNT_CHECKER_TEST_ITEM']}/{purpose}"
            )
    broker = Broker(cfg, binding, tmp_path)
    for purpose in ('username', 'password'):
        response = broker.request(purpose)
        placeholder = response['placeholder']
        assert placeholder.startswith('{{secret:')
        with pytest.raises(config.Unavailable, match='outside the approved'):
            broker.consume(placeholder, 'https://news.ycombinator.com')
        value = broker.consume(placeholder, 'https://www.linkedin.com/login')
        if not value:
            pytest.fail('The configured credential field is empty', pytrace=False)
        public_output = json.dumps(response) + (tmp_path / 'secret-events.jsonl').read_text()
        if value in public_output:
            pytest.fail('A raw credential reached broker output', pytrace=False)
        del value
        with pytest.raises(config.Unavailable, match='expired or already used'):
            broker.consume(placeholder, 'https://www.linkedin.com')


def test_linkedin_recovery_uses_real_mcp_and_fresh_verification():
    run_id = int(os.environ['ACCOUNT_CHECKER_ONEPASSWORD_RUN'])
    run = Run.query.get(id=run_id)
    assert run.scope == 'linkedin.com' and run.runtime['recovery']['check_id'] == 11
    assert run.tip and not run.issues and not run.promoted
    assert run.runtime['inference']['model'] == 'openai/gpt-6.1-sol'
    verified = Run.query.get(runtime__recovery_verification=run.id)
    assert verified.base.digest == run.tip
    assert verified.status == 'success' and verified.promoted and not verified.issues
    assert verified.runtime['container'] != run.runtime['container']
    result = CheckRun.query.get(run=verified, check_id=11)
    assert result.passed and result.state == 'accessible'
    assert Path(result.screenshot).stat().st_size > 1000
    directory = data_root() / 'runs' / str(run.id)
    events = [json.loads(line) for line in (directory / 'secret-events.jsonl').read_text().splitlines()]
    assert sum(e['action'] == 'secret_delivered_locally' and e['source'] == 'onepassword' for e in events) == 2
    actions = [json.loads(line) for line in (directory / 'recovery-actions.jsonl').read_text().splitlines()]
    fills = [a['result'] for a in actions if a['tool'] == 'act']
    assert len(fills) == 2 and all(f['status'] == 'completed' for f in fills)
    assert all(f['privacy']['inferenceRequests'] > 0 for f in fills)
    assert all(f['privacy']['knownSecretMatchesAfterRedaction'] == 0 for f in fills)
    transcript = (directory / 'transcript-recovery.json').read_text()
    for tool in ('private_login_act', 'browser_harness_execute_python', 'browser_harness_view_screenshot'):
        assert tool in transcript
    values = read(directory)
    assert len(values) == 2
    for path in directory.rglob('*'):
        if path.is_file() and 'personas' not in path.relative_to(directory).parts:
            contents = path.read_bytes()
            if any(v.encode() in contents for v in values.values()):
                pytest.fail(f'Raw credential in {path.relative_to(directory)}', pytrace=False)
