"""Borrow a CDP browser, owning only a fresh isolated context per checkout."""

import os
import re
from urllib.parse import urlsplit

from ..providers import CDPAdapter, browser_command, provider_config


class GenericCDP(CDPAdapter):
    label = 'Generic CDP'
    description = 'An existing Chrome CDP endpoint; each session owns a separate browser context.'
    config_help = 'Set endpoint_env to an environment variable containing an HTTP(S) or WS(S) browser CDP endpoint. The browser must support persistent isolated contexts and concurrent CDP connections. Credentials belong in .env.'

    def validate_config(self, config):
        if set(config) != {'endpoint_env'}:
            raise ValueError('Generic CDP requires only endpoint_env')
        if not isinstance(config['endpoint_env'], str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*', config['endpoint_env']):
            raise ValueError('endpoint_env must name an environment variable')

    def launch(self, run):
        cfg = provider_config(run)
        self.validate_config(cfg)
        endpoint = os.environ.get(cfg['endpoint_env'], '')
        if urlsplit(endpoint).scheme not in {'http', 'https', 'ws', 'wss'} or not urlsplit(endpoint).hostname:
            raise ValueError('The configured CDP environment variable must contain a browser endpoint')
        previous = run.runtime
        try:
            run.runtime = {**previous, 'cdp': endpoint}
            return browser_command('create_context', run)
        finally:
            run.runtime = previous

    def stop(self, run):
        # Never close a borrowed browser or clear another client's profile.
        browser_command('dispose_context', run)
