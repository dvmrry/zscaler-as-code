"""Dependency bump check — the basic renovate for environments without one.

The dependency surface is deliberately tiny: the three pinned Zscaler
providers (tools/schema-extract/main.tf, the single source of truth the
generated module pins are parsed from) and terraform itself. This checks
the public Terraform registry (and releases.hashicorp.com for terraform)
for newer versions and reports current -> latest per dependency.

Exit codes: 0 = everything current, 4 = update(s) available (schedule a
pipeline on this; a red run IS the notification), 1 = check failed
(network/parse — fails loudly rather than reporting stale "all current").

Updating a provider is the documented human flow (RUNBOOK "Provider
Bumps"): edit the pin, re-extract schemas, regenerate, review the drop
report. This tool only detects; it never auto-bumps — the drop-report
review is a judgment step.

Proxy via HTTPS_PROXY; corporate-MITM CA via REQUESTS_CA_BUNDLE /
SSL_CERT_FILE, same as the fetcher and install_tf.

Stdlib-only, Python 3.6-floor — see AGENTS.md rule 5.
"""
import json
import os
import re
import ssl
import subprocess
import sys

from tools.gen_module import _load_provider_pins

REGISTRY = "https://registry.terraform.io/v1/providers/zscaler/%s/versions"
TF_RELEASES = "https://releases.hashicorp.com/terraform/index.json"


# ---------------------------------------------------------------------------
# Version ordering (semver core + prerelease, exactly as deep as we need)
# ---------------------------------------------------------------------------

def _split(version):
    """'0.1.0-beta.1' -> ((0,1,0), 'beta.1'); '4.7.24' -> ((4,7,24), '')."""
    core, _, pre = version.partition("-")
    nums = []
    for part in core.split("."):
        nums.append(int(part) if part.isdigit() else 0)
    return tuple(nums), pre


def _pad(a, b):
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)), b + (0,) * (n - len(b))


def _pre_key(pre):
    """Prerelease ordering: dot/dash chunks, numeric chunks numerically."""
    out = []
    for chunk in re.split(r"[._-]", pre):
        if chunk.isdigit():
            out.append((0, int(chunk), ""))
        else:
            out.append((1, 0, chunk))
    return out


def is_newer(candidate, current):
    """True when candidate is a strictly newer version than current.

    Releases beat prereleases of the same core (1.0.0 > 1.0.0-beta.2);
    prereleases of the same core compare chunk-wise.
    """
    ca, pa = _split(candidate)
    cb, pb = _split(current)
    ca, cb = _pad(ca, cb)
    if ca != cb:
        return ca > cb
    if not pa and pb:
        return True
    if pa and not pb:
        return False
    if pa == pb:
        return False
    return _pre_key(pa) > _pre_key(pb)


def latest_of(versions, include_prerelease):
    """Newest version string in an iterable, or None if empty."""
    best = None
    for v in versions:
        if not include_prerelease and "-" in v:
            continue
        if best is None or is_newer(v, best):
            best = v
    return best


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def _fetch(url):
    import urllib.request

    bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    ctx = ssl.create_default_context(cafile=bundle) if bundle else None
    with urllib.request.urlopen(url, context=ctx, timeout=30) as resp:
        return resp.read()


def provider_versions(name, fetch=_fetch):
    data = json.loads(fetch(REGISTRY % name).decode("utf-8"))
    return [v.get("version", "") for v in data.get("versions") or [] if v.get("version")]


def terraform_versions(fetch=_fetch):
    data = json.loads(fetch(TF_RELEASES).decode("utf-8"))
    return list((data.get("versions") or {}).keys())


def local_terraform_version(tf="terraform"):
    """Installed terraform version, or None when unavailable."""
    try:
        out = subprocess.check_output([tf, "version"], stderr=subprocess.STDOUT)
    except (OSError, subprocess.CalledProcessError):
        return None
    m = re.search(r"Terraform v(\d+\.\d+\.\d+)", out.decode("utf-8", "replace"))
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Check + report
# ---------------------------------------------------------------------------

def check(pins, registry=provider_versions, tf_releases=terraform_versions,
          tf_current=None):
    """Returns (lines, updates_available). Pure given injected sources."""
    lines = []
    updates = 0
    for provider in sorted(pins):
        current = pins[provider]
        available = registry(provider)
        # A prerelease pin (zcc beta) opts that provider into seeing newer
        # prereleases too; stable pins only ever suggest stable.
        include_pre = "-" in current
        latest = latest_of(available, include_pre)
        if latest is None:
            lines.append("?? zscaler/%s: registry returned no versions" % provider)
            updates += 1
            continue
        if is_newer(latest, current):
            lines.append(
                "UPDATE zscaler/%s: %s -> %s (see RUNBOOK 'Provider Bumps' — "
                "edit tools/schema-extract/main.tf, make schemas && make "
                "generate, review the drop report)" % (provider, current, latest)
            )
            updates += 1
        else:
            lines.append("ok zscaler/%s: %s is current" % (provider, current))
        if not include_pre:
            latest_any = latest_of(available, True)
            if latest_any and is_newer(latest_any, latest):
                lines.append(
                    "   note zscaler/%s: prerelease %s exists (ignored for a "
                    "stable pin)" % (provider, latest_any)
                )

    try:
        tf_latest = latest_of(tf_releases(), False)
    except Exception as exc:
        # the provider results above are already computed — a terraform
        # index failure must not discard them (partial output beats a
        # bare "bump check failed" with nothing to act on)
        lines.append("?? terraform: release index unreachable (%s) — "
                     "provider results above are still valid" % exc)
        return lines, updates + 1
    if tf_latest is None:
        lines.append("?? terraform: release index returned no versions")
        updates += 1
    elif tf_current is None:
        lines.append(
            "-- terraform: latest is %s (no local binary to compare; pass "
            "the pipeline's pinned version via make install-tf docs)" % tf_latest
        )
    elif is_newer(tf_latest, tf_current):
        lines.append(
            "UPDATE terraform: %s -> %s (bump the pinned version in your "
            "pipeline yaml / make install-tf VERSION=)" % (tf_current, tf_latest)
        )
        updates += 1
    else:
        lines.append("ok terraform: %s is current" % tf_current)

    return lines, updates


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if argv:
        sys.stderr.write("usage: python -m tools.bump_check  (make bump-check)\n")
        return 2
    pins = _load_provider_pins()
    try:
        lines, updates = check(
            pins, tf_current=local_terraform_version(os.environ.get("TF", "terraform"))
        )
    except Exception as exc:
        sys.stderr.write(
            "bump check failed: %s\n"
            "hint: network/proxy? HTTPS_PROXY + REQUESTS_CA_BUNDLE apply, "
            "same as make fetch\n" % exc
        )
        return 1
    for line in lines:
        sys.stdout.write(line + "\n")
    if updates:
        sys.stdout.write("\n%d update(s) available\n" % updates)
        return 4
    sys.stdout.write("\nall dependencies current\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
