"""Best-effort reflected-value protection, shared by both browser tool surfaces."""

import html
import json
import os
from pathlib import Path
from urllib.parse import parse_qsl, quote, quote_plus, urlsplit

from cryptography.fernet import Fernet


def cipher():
    root = Path(os.environ.get("ACCOUNT_CHECKER_CONFIG", "~/.config/account-checker")).expanduser()
    return Fernet((root / "snapshot-key").read_bytes())


def read(directory):
    path = directory / "recovery-values.enc"
    return json.loads(cipher().decrypt(path.read_bytes())) if path.exists() else {}


def remember(directory, values):
    current = {**read(directory), **values}
    target = directory / "recovery-values.enc"
    temporary = target.with_suffix(".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
        f.write(cipher().encrypt(json.dumps(current).encode()))
    os.replace(temporary, target)


def variants(values):
    result = set()
    for value in values.values():
        if not value:
            continue
        result.update(
            [
                value,
                html.escape(value),
                quote(value, safe=""),
                quote_plus(value),
                json.dumps(value)[1:-1],
            ]
        )
        if value.startswith("https://"):
            result.update(v for _, v in parse_qsl(urlsplit(value).query) if len(v) >= 4)
    return sorted(result, key=len, reverse=True)


def redact(text, values):
    for value in variants(values):
        text = text.replace(value, "[secret redacted]")
    return text


# This is a screenshot mask, not a check implementation. It does not classify or
# navigate any site. Canvas, QR images, cross-origin frames, and unknown PII can
# still be visible; do not claim comprehensive screenshot anonymization.
MASK_JS = r"""(values) => {
  document.getElementById('__account_checker_masks')?.remove();
  const layer = document.createElement('div'); layer.id = '__account_checker_masks';
  layer.setAttribute('aria-hidden','true');
  layer.style.cssText = 'position:absolute;left:0;top:0;pointer-events:none;z-index:2147483647';
  function mask(r) {
    if (!r.width || !r.height) return;
    const box = document.createElement('div');
    box.style.cssText = `position:absolute;left:${r.left+scrollX}px;top:${r.top+scrollY}px;width:${r.width}px;height:${r.height}px;background:#172b4d;border-radius:3px`;
    layer.append(box);
  }
  for (const el of document.querySelectorAll('input,textarea,[contenteditable]')) {
    if (el.type==='password' || el.autocomplete==='one-time-code' || values.some(v => (el.value || el.textContent || '').includes(v))) mask(el.getBoundingClientRect());
  }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node=walker.nextNode())) {
    if (['SCRIPT','STYLE','NOSCRIPT'].includes(node.parentElement?.tagName)) continue;
    if (values.some(v => node.textContent.includes(v))) {
      const range = document.createRange(); range.selectNodeContents(node);
      for (const r of range.getClientRects()) mask(r);
    }
  }
  document.documentElement.append(layer);
  return layer.childElementCount;
}"""


def screenshot_prelude(values):
    """Wrap browser-harness's real screenshot helper without persisting raw values."""
    expression = f"({MASK_JS})({json.dumps(variants(values))})"
    return f"""\n_ac_capture_screenshot = capture_screenshot
def capture_screenshot(*args, **kwargs):
    try:
        js({expression!r})
        return _ac_capture_screenshot(*args, **kwargs)
    finally:
        js("document.getElementById('__account_checker_masks')?.remove()")
"""
