"""Shared adoption-status facts.

This module is intentionally tiny: it loads repo-owned facts about
resources that are not ordinary fetch-managed config yet, plus known
temporary drop-report holds. The facts live in JSON so adoption reports
and acceptance checks do not depend on Linear comments or agent memory.

Stdlib-only, Python 3.6-floor; see AGENTS.md rule 5.
"""
import json
import os


STATUS_PATH = os.path.join("tools", "adoption_status.json")

VALID_DISPOSITIONS = frozenset((
    "action-not-resource",
    "gateway-parked",
    "identity-skip",
    "manual-only",
))


def load_status(path=STATUS_PATH):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    validate_status(data, path)
    return data


def validate_status(data, path=STATUS_PATH):
    if not isinstance(data, dict):
        raise ValueError("%s must contain a JSON object" % path)
    dispositions = data.get("dispositions") or {}
    known_holds = data.get("known_holds") or {}
    if not isinstance(dispositions, dict):
        raise ValueError("%s dispositions must be an object" % path)
    if not isinstance(known_holds, dict):
        raise ValueError("%s known_holds must be an object" % path)
    for rt, entry in dispositions.items():
        if not isinstance(entry, dict):
            raise ValueError("%s disposition for %s must be an object" % (path, rt))
        status = entry.get("status")
        if status not in VALID_DISPOSITIONS:
            raise ValueError(
                "%s disposition for %s has invalid status %r"
                % (path, rt, status))
        if not entry.get("reason"):
            raise ValueError("%s disposition for %s needs a reason" % (path, rt))
    for rt, holds in known_holds.items():
        if not isinstance(holds, list):
            raise ValueError("%s known_holds for %s must be a list" % (path, rt))
        for hold in holds:
            if not isinstance(hold, dict) or not hold.get("path"):
                raise ValueError(
                    "%s known_holds for %s must include path objects"
                    % (path, rt))
    return True


def disposition_for(resource_type, status=None):
    data = status if status is not None else load_status()
    return (data.get("dispositions") or {}).get(resource_type)


def known_holds_for(resource_type, status=None):
    data = status if status is not None else load_status()
    return list((data.get("known_holds") or {}).get(resource_type) or [])


def known_hold_paths(resource_type, status=None):
    return sorted(h["path"] for h in known_holds_for(resource_type, status))
