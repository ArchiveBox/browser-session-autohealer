"""Aligned provider results using the live configured tasks and retained evidence."""

from html.parser import HTMLParser

from app.core import health
from app.core.models import Check
from app.trees import root_table


def test_every_provider_has_the_same_result_slots():
    groups = health.grouped_checks(health.check_rows(Check.query.join('account', 'provider')))
    table = root_table('providers', {})
    assert groups and table['tree_rows']
    for row in table['tree_rows']:
        assert len(row['cells'][-1]['images']) == len(groups), row['id']
    assert len(table['tree_result_columns']) == len(groups)
    expected_order = [column['key'] for column in table['tree_result_columns']]
    for row in table['tree_rows']:
        assert [shot['key'] for shot in row['cells'][-1]['images']] == expected_order


def test_latest_evidence_and_distinct_missing_states():
    tiles = health.check_rows(Check.query.join('account', 'provider'))
    by_assignment = {tile['check']['id']: tile for tile in tiles}
    table = root_table('providers', {})
    seen = set()
    for row in table['tree_rows']:
        for shot in row['cells'][-1]['images']:
            if shot['check_id'] is None:
                assert shot['missing'] == 'irrelevant'
                assert shot['symbol'] == '—'
            else:
                latest = by_assignment[shot['check_id']]
                assert shot['url'] == latest['image']
                assert shot['href'] == latest['url']
                if latest['result'] and not latest['image']:
                    assert shot['missing'] == 'expected'
                    assert shot['tone'] == latest['tone']
                    assert shot['symbol'] in {'!', '◷'}
            if not shot['url']:
                seen.add(shot['missing'])
    assert seen == {'expected', 'irrelevant'}, 'Both cases need real retained data'


def test_provider_html_renders_symbol_placeholders(app_session):
    response = app_session.get('/?view=providers')
    assert response.status_code == 200

    class Results(HTMLParser):
        def __init__(self):
            super().__init__()
            self.placeholders = []
            self.images = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if 'result-placeholder' in attrs.get('class', ''):
                assert attrs.get('role') == 'img'
                assert attrs.get('aria-label')
                self.placeholders.append(attrs)
            if tag == 'img' and 'data-result-image' in attrs:
                assert attrs.get('src')
                self.images.append(attrs)

    parsed = Results()
    parsed.feed(response.text)
    assert parsed.images
    assert any(p['data-missing'] == 'expected' and 'hidden' not in p for p in parsed.placeholders)
    assert any(p['data-missing'] == 'irrelevant' and 'hidden' not in p for p in parsed.placeholders)
