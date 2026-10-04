"""Real selective Brave import preserves the existing persona's other sites."""
import hashlib
import json
import os

from app.core import storage
from app.core.models import Checkpoint
from app.core.site_scope import matches


def test_x_import_preserves_unselected_sites():
    checkpoint = Checkpoint.query.get(digest=os.environ['ACCOUNT_CHECKER_IMPORT_CHECKPOINT'])
    before, after = storage.read_state(checkpoint.parent), storage.read_state(checkpoint.digest)

    def unselected_digest(state):
        values = {
            'cookies': sorted([c for c in state['cookies'] if not matches(c['domain'], ['x.com'])], key=storage.cookie_key),
            'origins': sorted([o for o in state['origins'] if o['origin'] != 'https://x.com'], key=lambda o: o['origin']),
        }
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    assert unselected_digest(before) == unselected_digest(after)
    assert any(c['name'] == 'auth_token' and matches(c['domain'], ['x.com']) for c in after['cookies'])
    assert any(o['origin'] == 'https://x.com' for o in after['origins'])
    assert {'news.ycombinator.com', 'linkedin.com', 'x.com'} <= set(after['settings']['siteScope'])
    assert {'userAgent', 'userAgentMetadata', 'viewport', 'screen', 'window', 'timezone',
            'locale', 'languages', 'colorScheme', 'reducedMotion'} <= after['settings'].keys()
    assert checkpoint.source['kind'] == 'cdp'
