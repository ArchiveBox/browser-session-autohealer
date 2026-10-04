"""Canonical CSV host patterns for reusable check scripts."""
import re
from fnmatch import fnmatchcase

from plain.exceptions import ValidationError


def normalize(value):
    patterns = list(dict.fromkeys(part.strip().lower().rstrip('.') for part in value.split(',') if part.strip()))
    if not patterns:
        raise ValidationError('Enter at least one domain or wildcard')
    label = r'[a-z0-9*](?:[a-z0-9*\-]*[a-z0-9*])?'
    for pattern in patterns:
        if len(pattern) > 253 or not re.fullmatch(label + r'(?:\.' + label + ')*', pattern):
            raise ValidationError('Use domains separated by commas; * is the supported wildcard')
    return ', '.join(patterns)


def validate(value):
    normalize(value)


def matches(patterns, host):
    host = host.lower().rstrip('.')
    return any(fnmatchcase(host, pattern.strip()) for pattern in normalize(patterns).split(','))
