"""Aligned provider results using the live configured tasks and retained evidence."""

from html.parser import HTMLParser
from urllib.parse import urlsplit

from app.core import health
from app.core.models import Check
from app.trees import root_table


def test_every_provider_has_one_slot_per_site():
    checks = list(Check.query.filter(mode='check').join('account__site', 'provider'))
    domains = sorted({c.account.site.domain for c in checks})
    table = root_table('providers', {})
    assert domains and table['tree_rows']
    assert [c['key'] for c in table['tree_result_columns']] == domains
    for row in table['tree_rows']:
        shots = row['cells'][-1]['images']
        assert [s['domain'] for s in shots] == domains
        for shot in shots:
            applicable = [c for c in checks if c.provider.id == row['id']
                          and c.account.site.domain == shot['domain'] and c.enabled and c.provider.enabled]
            tiles = health.check_rows(applicable)
            assert (shot['tone'] == 'success') == bool(tiles and all(t['tone'] == 'success' for t in tiles))
            if tiles:
                candidates = [t for t in tiles if t['tone'] != 'success'] or tiles
                assert shot['check_id'] in [t['check']['id'] for t in candidates]
                if shot['url']:
                    assert shot['url'] in [t['image'] for t in candidates]
            else:
                assert shot['tone'] == 'neutral' and not shot['url']


def test_failure_takes_priority_over_passing_evidence():
    from app.trees import site_result
    tiles = health.check_rows(Check.query.join('account', 'provider'))
    failed = next(t for t in tiles if t['tone'] == 'failed' and t['image'])
    domain = urlsplit(failed['check']['url']).hostname
    passed = next(t for t in tiles if t['tone'] == 'success' and t['image']
                  and urlsplit(t['check']['url']).hostname == domain)
    shot = site_result(domain, [passed, failed])
    assert shot['tone'] == 'failed'
    assert shot['url'] == failed['image']
    assert shot['href'] == failed['url']
    missing = next(t for t in tiles if not t['image'] and t['tone'] != 'success')
    shot = site_result(domain, [passed, missing])
    assert shot['tone'] == 'failed' and not shot['url']
    assert shot['missing'] == 'expected'
    irrelevant = site_result(domain, [])
    assert irrelevant['missing'] == 'irrelevant' and irrelevant['symbol'] == '—'


def test_provider_html_renders_symbol_placeholders(app_session):
    response = app_session.get('/?view=providers')
    assert response.status_code == 200

    class Results(HTMLParser):
        def __init__(self):
            super().__init__()
            self.placeholders = []
            self.images = []
            self.favicons = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if 'result-placeholder' in attrs.get('class', ''):
                assert attrs.get('role') == 'img'
                assert attrs.get('aria-label')
                self.placeholders.append(attrs)
            if tag == 'img' and 'google.com/s2/favicons' in attrs.get('src', ''):
                self.favicons.append(attrs)
            if tag == 'img' and 'data-result-image' in attrs:
                assert attrs.get('src')
                self.images.append(attrs)

    parsed = Results()
    parsed.feed(response.text)
    assert parsed.images
    assert len(parsed.favicons) == len(parsed.placeholders)
    table = root_table('providers', {})
    assert len(parsed.placeholders) == len(table['tree_rows']) * len(table['tree_result_columns'])
