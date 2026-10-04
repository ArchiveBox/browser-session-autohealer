"""Exercise the compiled guard against the real Docker host's CPU capabilities."""

import json
import subprocess


def test_guard_only_masks_sme_on_cpus_without_sve():
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "uv", "account-checker-browser:dev",
         "run", "--no-project", "--python", "/venv/bin/python", "python", "-c", """
import ctypes, json, os, platform
native = ctypes.CDLL(None).getauxval
guard = ctypes.CDLL(os.environ['ACCOUNT_CHECKER_CPU_GUARD']).getauxval
for fn in (native, guard):
    fn.argtypes = [ctypes.c_ulong]
    fn.restype = ctypes.c_ulong
print(json.dumps({'arch': platform.machine(),
    'native': [native(n) for n in (16, 26, 6)],
    'guard': [guard(n) for n in (16, 26, 6)]}))
"""], capture_output=True, text=True, timeout=30, check=True,
    )
    data = json.loads(result.stdout)
    hwcap, hwcap2, pagesize = data["native"]
    mask = ((1 << 23) | (1 << 37)) if data["arch"] == "aarch64" and not hwcap & (1 << 22) else 0
    assert data["guard"] == [hwcap, hwcap2 & ~mask, pagesize]
