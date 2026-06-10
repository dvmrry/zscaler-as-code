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
