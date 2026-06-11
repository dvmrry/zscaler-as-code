"""Install a pinned terraform binary from releases.hashicorp.com.

For agents and fresh machines without terraform: downloads the zip for
the detected (or overridden) platform, verifies its SHA256 against the
published SHA256SUMS file, and extracts the binary to the destination
directory. Proxy comes from HTTPS_PROXY (urllib default behavior);
corporate TLS inspection is handled the same way as the fetcher —
point REQUESTS_CA_BUNDLE/SSL_CERT_FILE at the exported proxy root.

Usage: python -m tools.install_tf <version> [dest-dir]   (make install-tf)

Stdlib-only, Python 3.6-floor — see AGENTS.md rule 5.
"""
import hashlib
import io
import os
import platform
import ssl
import sys
import zipfile

RELEASES = "https://releases.hashicorp.com/terraform"


def detect_platform(system=None, machine=None):
    """Map (uname-ish system, machine) to terraform's os_arch token."""
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    os_token = {"darwin": "darwin", "linux": "linux", "windows": "windows"}.get(system)
    arch_token = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "amd64",
        "amd64": "amd64",
    }.get(machine)
    if not os_token or not arch_token:
        raise ValueError("unsupported platform %s/%s" % (system, machine))
    return "%s_%s" % (os_token, arch_token)


def zip_url(version, plat):
    return "%s/%s/terraform_%s_%s.zip" % (RELEASES, version, version, plat)


def sums_url(version):
    return "%s/%s/terraform_%s_SHA256SUMS" % (RELEASES, version, version)


def expected_sha(sums_text, version, plat):
    """Extract the published SHA256 for one zip from the SHA256SUMS body."""
    want = "terraform_%s_%s.zip" % (version, plat)
    for line in sums_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == want:
            return parts[0]
    raise ValueError("no checksum for %s in SHA256SUMS" % want)


def _fetch(url):
    import urllib.request

    bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    ctx = ssl.create_default_context(cafile=bundle) if bundle else None
    # timeout so a stalled connection (wedged proxy, dead mirror) fails fast
    # into main()'s "install failed" handler instead of hanging CI forever.
    # 60s suits a full ~20MB binary transfer (fetch.py's 15s TLS probe is
    # too tight here).
    with urllib.request.urlopen(url, context=ctx, timeout=60) as resp:
        return resp.read()


def install(version, dest, fetch=_fetch, plat=None):
    """Download, verify, extract. Returns the binary path."""
    plat = plat or detect_platform()
    sys.stderr.write("downloading terraform %s (%s)...\n" % (version, plat))
    blob = fetch(zip_url(version, plat))
    want = expected_sha(fetch(sums_url(version)).decode(), version, plat)
    got = hashlib.sha256(blob).hexdigest()
    if got != want:
        raise ValueError(
            "checksum mismatch for terraform %s: expected %s, got %s "
            "(corrupted or tampered download — do NOT use it)" % (version, want, got)
        )
    binary = "terraform.exe" if plat.startswith("windows") else "terraform"
    if not os.path.isdir(dest):
        os.makedirs(dest)
    out_path = os.path.join(dest, binary)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        with zf.open(binary) as src, open(out_path, "wb") as out:
            out.write(src.read())
    os.chmod(out_path, 0o755)
    return out_path


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) not in (1, 2):
        sys.stderr.write("usage: python -m tools.install_tf <version> [dest-dir]\n")
        return 2
    version = argv[0]
    dest = argv[1] if len(argv) == 2 else "bin"
    try:
        path = install(version, dest)
    except Exception as exc:
        sys.stderr.write("install failed: %s\n" % exc)
        return 1
    sys.stdout.write(
        "installed %s\nuse it via PATH, or per-invocation: make plan TF=%s ...\n"
        % (path, path)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
