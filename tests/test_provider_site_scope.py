"""Selective provider round-trips preserve other sites from real checkpoints."""
from app.core import storage
from app.core.models import Leader, Persona
from app.core.site_scope import checkin_state, matches, select_state


def test_selected_provider_checkin_preserves_unshared_sites():
    persona = Persona.query.get(id=4)
    original = storage.read_state(Leader.query.get(persona=persona).checkpoint.digest)
    sites = ['news.ycombinator.com']
    returned = select_state(original, sites)
    assert returned['cookies']
    assert all(matches(c['domain'], sites) for c in returned['cookies'])
    merged = checkin_state(original, returned, sites)
    assert sorted(merged['cookies'], key=storage.cookie_key) == sorted(original['cookies'], key=storage.cookie_key)
    assert merged['settings'] == original['settings']
    # A provider can explicitly remove all of its own cookies, without touching others.
    returned['cookies'] = []
    merged = checkin_state(original, returned, sites)
    assert merged['cookies'] == [c for c in original['cookies'] if not matches(c['domain'], sites)]
    assert merged['cookies']
