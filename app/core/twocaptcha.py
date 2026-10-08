"""Private 2Captcha settings and the unmodified vendor extension package."""

import fcntl
import hashlib
import json
import os
import re
import shutil
import zipfile
from pathlib import Path

import httpx
from abxpkg import Binary
from abxpkg.binprovider_chromewebstore import ChromeWebstoreProvider
from abxpkg.exceptions import BinaryLoadError

from . import storage
from .recovery import config

WEBSTORE_ID = 'ifibfemgeogfhoebkmokieepdoobkbpo'
DEFAULTS = {'retry_count': 0, 'retry_delay': 5, 'auto_submit': False}
NATIVE_SOLVERS = {'kernel', 'browserbase', 'anchor', 'browserless'}
SUPPORT = {
    'local': 'Automatic installation in owned host Chrome and Docker browsers.',
    'browserbase': 'Automatic upload and session extensionId configuration.',
    'kernel': 'Automatic upload. Uses non-stealth mode to prevent competing solvers; choose an explicit proxy if needed (the default stealth proxy is not used).',
    'anchor': 'Automatic upload and browser.extensions configuration.',
    'browserless': 'Chromium only. Automatic upload requires the account extension API; otherwise upload the ZIP in the provider dashboard and set twocaptcha_extension.',
    'cdp': 'Unavailable: borrowed isolated contexts cannot safely configure a browser-wide extension.',
}


def has_key(cfg):
    return bool(cfg.get('twocaptcha', {}).get('api_key_encrypted') or
                os.environ.get('TWOCAPTCHA_API_KEY') or os.environ.get('API_KEY_2CAPTCHA'))


def api_key(cfg):
    encrypted = cfg.get('twocaptcha', {}).get('api_key_encrypted')
    if encrypted:
        return storage.cipher().decrypt(encrypted.encode()).decode()
    return (os.environ.get('TWOCAPTCHA_API_KEY') or os.environ.get('API_KEY_2CAPTCHA') or '').strip()


def save_key(connection, value, *, clear=False):
    if clear:
        connection.pop('api_key_encrypted', None)
    elif value:
        if not re.fullmatch(r'[a-fA-F0-9]{32}', value):
            raise ValueError('The 2Captcha API key must contain 32 hexadecimal characters')
        connection['api_key_encrypted'] = storage.cipher().encrypt(value.encode()).decode()


def parse_option(field, value):
    if field == 'auto_submit':
        if value not in ('true', 'false'):
            raise ValueError('Auto-submit must be On or Off')
        return value == 'true'
    limit = {'retry_count': 10, 'retry_delay': 60}[field]
    if not value.isdecimal() or not 0 <= int(value) <= limit:
        raise ValueError(f'{field.replace("_", " ").capitalize()} must be an integer from 0 to {limit}')
    return int(value)


def default_options(cfg):
    return {**DEFAULTS, 'enabled': has_key(cfg)}


def provider_enabled(cfg, kind):
    return cfg.get('twocaptcha', {}).get('providers', {}).get(kind, kind not in NATIVE_SOLVERS)


def for_checkout(persona, provider, checks, scope):
    """Freeze non-secret settings; never put the key in run JSON or agent input."""
    from .models import Account

    cfg = config.load()
    if not provider_enabled(cfg, provider.kind):
        return {**DEFAULTS, 'enabled': False}
    accounts = ([c.account for c in checks] if checks else
                list(Account.query.filter(persona=persona, **({'site__domain': scope} if scope != '*' else {}))))
    pairs = list(zip(accounts, checks, strict=True)) if checks else [(a, None) for a in accounts]
    values = [config.effective_integration(cfg, 'twocaptcha',
              config.scope_chain(account, check), account)[0] for account, check in pairs]
    if not values:
        values = [config.effective_integration(cfg, 'twocaptcha', config.scope_chain(persona=persona))[0]]
    if any(value != values[0] for value in values):
        raise ValueError('Sites and checks sharing a browser must use the same 2Captcha settings; request a narrower scope')
    return values[0]


def enabled(run):
    return bool(run.runtime.get('twocaptcha', {}).get('enabled'))


def connection_status(cfg):
    key = api_key(cfg)
    if not key:
        return 'API key required'
    try:
        response = httpx.post('https://api.2captcha.com/getBalance', json={'clientKey': key}, timeout=20)
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError):
        return '2Captcha connection failed'
    if result.get('errorId') == 0 and isinstance(result.get('balance'), (int, float)):
        return 'Connected' if result['balance'] > 0 else 'Connected — balance is zero'
    code = result.get('errorCode', '')
    return code if re.fullmatch(r'ERROR_[A-Z_]{1,64}', str(code)) else '2Captcha rejected the API key'


def extension_package():
    """Use abxpkg's Chrome Web Store installer, as the ArchiveBox plugin does."""
    root = storage.private_dir(storage.data_root() / 'extensions')
    with (root / 'twocaptcha.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        provider = ChromeWebstoreProvider(install_root=root / 'chromewebstore')
        binary = Binary(name='twocaptcha', binproviders=[provider], overrides={
            'chromewebstore': {'install_args': [WEBSTORE_ID, '--name=twocaptcha']},
        })
        try:
            loaded = binary.load()
        except BinaryLoadError:
            loaded = binary.install()
        if not loaded.loaded_abspath:
            raise RuntimeError('2Captcha extension installation failed')
        installed = Path(loaded.loaded_abspath)
        source = (Path(json.loads(installed.read_text())['unpacked_path'])
                  if installed.name.endswith('.extension.json') else installed.parent)
        manifest = json.loads((source / 'manifest.json').read_text())
        if manifest.get('manifest_version') != 3 or not (source / 'options/options.html').is_file():
            raise RuntimeError('The installed 2Captcha extension has an unsupported layout')
        # Deterministic, key-free archive: the same package is shared across providers.
        archive = root / 'twocaptcha.zip'
        temporary = archive.with_suffix('.tmp')
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as output:
            for path in sorted(source.rglob('*')):
                relative = path.relative_to(source)
                if path.is_file() and not path.is_symlink() and '_metadata' not in relative.parts:
                    info = zipfile.ZipInfo(relative.as_posix())
                    info.compress_type = zipfile.ZIP_DEFLATED
                    output.writestr(info, path.read_bytes())
        os.replace(temporary, archive)
        return {'source': source, 'archive': archive, 'version': manifest['version'],
                'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}


def prepare_local(run, work):
    if not enabled(run):
        return
    package = extension_package()
    directory = storage.private_dir(work / 'extensions')
    docker = run.runtime.get('provider_config', run.provider.config).get('runtime') == 'docker'
    if docker:
        shutil.copytree(package['source'], directory / 'twocaptcha', dirs_exist_ok=True)
    # A stable unpacked path keeps the extension ID stable across profile forks.
    # The package contains no key; settings live in each isolated Chrome profile.
    metadata = {'name': 'twocaptcha', 'version': package['version'], 'webstore_id': WEBSTORE_ID,
                'unpacked_path': '/run/extensions/twocaptcha' if docker else str(package['source'])}
    (directory / 'twocaptcha.extension.json').write_text(json.dumps(metadata))


def uploaded_extension(kind, identity, upload):
    """One key-free vendor upload per provider account and package version."""
    package = extension_package()
    cache_key = hashlib.sha256(f'{kind}:{identity}:{package["sha256"]}'.encode()).hexdigest()
    root = storage.private_dir(storage.data_root() / 'extensions/uploads')
    record = root / f'{cache_key}.json'
    with (root / f'{cache_key}.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if record.exists():
            return json.loads(record.read_text())['id']
        identifier = upload(package)
        if not isinstance(identifier, str) or not identifier:
            raise RuntimeError('The provider did not return a 2Captcha extension ID')
        temporary = record.with_suffix('.tmp')
        temporary.write_text(json.dumps({'id': identifier}))
        os.replace(temporary, record)
        return identifier


def configure_browser(run):
    from .providers import browser_command

    options = run.runtime.get('twocaptcha', {'enabled': False})
    kind = run.runtime.get('provider_kind', run.provider.kind)
    if not options.get('enabled') and kind != 'local':
        result = {'status': 'disabled', 'detail': '2Captcha is off; provider-native CAPTCHA settings are unchanged.'}
    elif kind == 'cdp':
        result = {'status': 'unsupported', 'detail': SUPPORT[kind]}
    else:
        try:
            key = api_key(config.load()) if options.get('enabled') else ''
            if options.get('enabled') and not key:
                raise ValueError('2Captcha is enabled but no API key is configured in Integrations')
            result = browser_command('twocaptcha', run, options=options, apiKey=key)
        except Exception:
            run.runtime['twocaptcha_status'] = {'status': 'failed',
                'detail': '2Captcha setup failed. Check the key, balance and provider extension settings in Integrations.'}
            run.update(fields=['runtime'])
            raise
    run.runtime['twocaptcha_status'] = result
    if result.get('status') == 'ready' and kind in ('kernel', 'browserless'):
        result['detail'] = SUPPORT[kind]
    run.update(fields=['runtime'])
    return result
