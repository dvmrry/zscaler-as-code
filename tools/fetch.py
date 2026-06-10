"""Fetch detail-shaped Zscaler API JSON into pulls/<tenant>/<type>.json.

Runs with real credentials ONLY in trusted environments; here it is
exercised against fictional canned responses via an injected opener.
Stdlib-only, Python 3.6-floor. Per-resource knowledge lives in
tools/fetch_manifest.json (data); only auth/pagination patterns are code.
See AGENTS.md rules 1-5.
"""
import json
import os
import sys

MANIFEST_PATH = os.path.join("tools", "fetch_manifest.json")

_manifest_cache = {}


def load_manifest():
    if not _manifest_cache:
        with open(MANIFEST_PATH) as f:
            _manifest_cache.update(json.load(f))
    return _manifest_cache


def manifest_entry(resource_type):
    manifest = load_manifest()
    if resource_type not in manifest:
        raise KeyError(
            "%r not in fetch manifest; add it to tools/fetch_manifest.json"
            % resource_type
        )
    return manifest[resource_type]


def obfuscate_api_key(api_key, timestamp):
    """Port of the ZIA legacy key-obfuscation algorithm (public SDK).

    timestamp is milliseconds-since-epoch as a string. Raises ValueError
    on inputs too short to index, matching the SDK guard.
    """
    if len(timestamp) < 6 or len(api_key) < 12:
        raise ValueError("timestamp or api key below required length")
    high = timestamp[-6:]
    low = "%06d" % (int(high) >> 1)
    obfuscated = ""
    for ch in high:
        obfuscated += api_key[int(ch)]
    for ch in low:
        obfuscated += api_key[int(ch) + 2]
    return obfuscated
