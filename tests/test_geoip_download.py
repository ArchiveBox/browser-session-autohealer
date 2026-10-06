"""Real startup/download acceptance; requires access to the public GeoLite mirror."""
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from hashlib import file_digest

import maxminddb


def test_startup_downloads_and_reuses_city_database(tmp_path):
    env = {**os.environ, 'ACCOUNT_CHECKER_DATA': str(tmp_path), 'MAXMIND_CITY_DB': ''}
    def start():
        result = subprocess.run(['uv', 'run', 'plain', 'preflight'], env=env,
                                text=True, capture_output=True, timeout=180, check=False)
        assert result.returncode == 0, result.stderr
    # Two real app processes share one cache, including on the first installation.
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: start(), range(2)))
    path = tmp_path / 'geoip' / 'GeoLite2-City.mmdb'
    assert path.is_file(), 'App startup must provision GeoLite2 without manual configuration'
    with maxminddb.open_database(path) as reader:
        assert reader.metadata().database_type == 'GeoLite2-City'
        assert reader.get('8.8.8.8')['country']['iso_code'] == 'US'
    with path.open('rb') as source:
        digest = file_digest(source, 'sha256').hexdigest()
    stamp = path.stat().st_mtime_ns
    start()
    assert path.stat().st_mtime_ns == stamp  # No repeated download on warm startup.
    assert not path.with_suffix('.download').exists()

    # A real failed network connection must preserve the installed database.
    offline = {**env, 'HTTPS_PROXY': 'http://127.0.0.1:1', 'NO_PROXY': ''}
    refresh = subprocess.run(['uv', 'run', 'plain', 'accounts', 'geoip', '--force'],
                             env=offline, text=True, capture_output=True, timeout=30, check=False)
    assert refresh.returncode == 1 and 'GeoIP update failed' in refresh.stderr
    with path.open('rb') as source:
        assert file_digest(source, 'sha256').hexdigest() == digest
    assert path.stat().st_mtime_ns == stamp
    assert not path.with_suffix('.download').exists()

    # A supplied database is read-only, including when an update is requested.
    supplied = {**offline, 'MAXMIND_CITY_DB': str(path)}
    refresh = subprocess.run(['uv', 'run', 'plain', 'accounts', 'geoip', '--force'],
                             env=supplied, text=True, capture_output=True, timeout=30, check=False)
    assert refresh.returncode == 0 and 'GeoIP update failed' not in refresh.stderr
    assert path.stat().st_mtime_ns == stamp
