"""Site boundaries on real portable state files, including partitioned cookies."""

import json

import pytest

from app.core.site_scope import select_state


def test_selected_import_drops_unrelated_data(tmp_path):
    # This is a portable import document, not a simulated browser or provider.
    source = tmp_path / "state.json"
    source.write_text(json.dumps({
        "cookies": [
            {"domain": "news.ycombinator.com", "name": "hn", "value": "test"},
            {"domain": ".linkedin.com", "name": "li", "value": "test"},
            {"domain": "linkedin.com.attacker.invalid", "name": "other", "value": "test"},
            {"domain": ".linkedin.com", "name": "partitioned", "value": "test",
             "partitionKey": {"topLevelSite": "https://unrelated.invalid"}},
        ],
        "origins": [{"origin": "https://www.linkedin.com", "localStorage": {"test": "yes"}},
                    {"origin": "https://unrelated.invalid", "indexedDB": {"private": "no"}}],
        "settings": {"userAgent": "test-UA", "locale": "en-US", "viewport": {"width": 1234},
                     "urls": ["https://www.linkedin.com/feed/", "https://unrelated.invalid/"]},
        "unknown_site_payload": {"unrelated.invalid": "must not survive"},
    }))
    state = select_state(json.loads(source.read_text()), ["news.ycombinator.com", "linkedin.com"])
    assert [c["name"] for c in state["cookies"]] == ["hn", "li"]
    assert len(state["origins"]) == 1 and state["origins"][0]["localStorage"] == {"test": "yes"}
    assert state["settings"]["viewport"]["width"] == 1234
    assert state["settings"]["urls"] == ["https://www.linkedin.com/feed/"]
    assert "unrelated.invalid" not in json.dumps(state)


def test_empty_or_invalid_selection_never_means_import_everything():
    for selection in ([], ["*"], ["com"], ["https://linkedin.com/private"]):
        with pytest.raises(ValueError):
            select_state({"cookies": [], "origins": [], "settings": {}}, selection)
