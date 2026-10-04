"""Real provider form persists presentation without changing launch configuration."""
import json

from app.core.models import Provider


def test_provider_color_form_and_lineage(app_session):
    provider = Provider.query.get(id=1)
    original = provider.config
    response = app_session.get(f'/edit/provider?id={provider.id}')
    assert response.status_code == 200
    assert 'type="color"' in response.text
    values = {'name': provider.name, 'provider_kind': provider.kind,
              'config': json.dumps(provider.config), 'enabled': 'on', 'color': '#3973cf'}
    response = app_session.post(f'/edit/provider?id={provider.id}', data=values,
                               headers={'Origin': 'http://127.0.0.1:8421'})
    assert response.status_code == 303
    current = Provider.query.get(id=provider.id)
    assert current.color == '#3973cf'
    assert current.config == original
    html = app_session.get('/personas/4/lineage').text
    assert 'style="stroke:#3973cf"' in html
    assert 'class="graph-dot success"' in html
    response = app_session.post(f'/edit/provider?id={provider.id}',
                               data={**values, 'color': 'red;display:none'},
                               headers={'Origin': 'http://127.0.0.1:8421'})
    assert 'Choose a valid color' in response.text
    assert Provider.query.get(id=provider.id).color == '#3973cf'
