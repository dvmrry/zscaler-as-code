"""Human-readable lookup sidecars for ID-bearing config references.

V1 is intentionally small: resolve zia_url_filtering_rules.url_categories
through zia_url_categories.configured_name. The manifest below is the source
of truth; do not infer references from Terraform schema shapes.

Stdlib-only, Python 3.6-floor - see AGENTS.md rule 5.
"""
import fnmatch
import json
import os
import re
import sys

from tools.registry import generated_types

UNKNOWN = "<unknown>"
CONFIG_SUFFIX = ".auto.tfvars.json"
LOOKUP_SUFFIX = ".lookup.json"

REFERENCES = {
    "zia_url_filtering_rules": {
        "url_categories": {
            "referent": "zia_url_categories",
            "name_field": "configured_name",
        },
    },
}

LOOKUP_SOURCES = {
    "zia_url_categories": {
        "name_field": "configured_name",
    },
}

_VALID_TENANT = re.compile(r"^[A-Za-z0-9_.-]+$")


class LookupDataError(Exception):
    pass


def _copy_nested(mapping):
    return dict(
        (key, dict((inner_key, dict(inner_value))
                   for inner_key, inner_value in value.items()))
        for key, value in mapping.items()
    )


def reference_manifest():
    return _copy_nested(REFERENCES)


def lookup_sources():
    return dict((key, dict(value)) for key, value in LOOKUP_SOURCES.items())


def check_tenant(tenant):
    if not _VALID_TENANT.match(tenant or "") or tenant in (".", ".."):
        raise ValueError(
            "tenant must match [A-Za-z0-9_.-]+ and not be . or .. (got %r)"
            % tenant
        )


def lookup_path(tenant, referent, config_root="config"):
    return os.path.join(config_root, tenant, referent + LOOKUP_SUFFIX)


def config_path(tenant, resource_type, config_root="config"):
    return os.path.join(config_root, tenant, resource_type + CONFIG_SUFFIX)


def _display_name(item, name_field):
    value = item.get(name_field)
    if not isinstance(value, str) or not value.strip():
        return UNKNOWN
    return value


def build_lookup(items, name_field):
    out = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        ident = item.get("id")
        if ident is None or ident == "":
            continue
        out[str(ident)] = _display_name(item, name_field)
    return out


def render_lookup(mapping):
    return json.dumps(mapping, indent=2, sort_keys=True) + "\n"


def write_lookup(tenant, referent, items, config_root="config"):
    source = LOOKUP_SOURCES.get(referent)
    if source is None:
        return None
    path = lookup_path(tenant, referent, config_root=config_root)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_lookup(build_lookup(items, source["name_field"])))
    return path


def load_json_object(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except ValueError as e:
        raise LookupDataError("%s is not valid JSON: %s" % (path, e))
    if not isinstance(data, dict):
        raise LookupDataError("%s must contain a JSON object" % path)
    return data


def load_lookup(tenant, referent, config_root="config"):
    path = lookup_path(tenant, referent, config_root=config_root)
    if not os.path.exists(path):
        return {}
    data = load_json_object(path)
    out = {}
    for key, value in data.items():
        out[str(key)] = value if isinstance(value, str) else UNKNOWN
    return out


def _expand_selectors(selectors):
    generated = set(generated_types())
    if not selectors:
        return sorted(generated)
    selected = set()
    unknown = []
    for selector in selectors:
        if selector in generated:
            selected.add(selector)
            continue
        if selector in ("zia", "zpa", "zcc"):
            matches = sorted(rt for rt in generated if rt.startswith(selector + "_"))
        else:
            matches = sorted(rt for rt in generated if fnmatch.fnmatch(rt, selector))
        if matches:
            selected.update(matches)
        else:
            unknown.append(selector)
    if unknown:
        raise ValueError(
            "unknown resource selector(s): %s" % ", ".join(sorted(unknown))
        )
    return sorted(selected)


def _is_system_constant(value):
    if not isinstance(value, str):
        return False
    if value.startswith("CUSTOM_"):
        return False
    return value == value.upper() and value.replace("_", "").isalnum()


def _resolve_id(value, mapping):
    ident = str(value)
    if ident in mapping:
        return mapping[ident], ident
    if _is_system_constant(ident):
        return ident, ident
    return UNKNOWN, ident


def _field_values(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def _format_resolved(values, mapping):
    grouped = {}
    for value in values:
        display, ident = _resolve_id(value, mapping)
        grouped.setdefault(display, []).append(ident)
    parts = []
    for display in sorted(grouped):
        ids = sorted(set(grouped[display]))
        parts.append("%s (%s)" % (display, ", ".join(ids)))
    return ", ".join(parts)


def _item_label(key, item):
    name = item.get("name") if isinstance(item, dict) else None
    if isinstance(name, str) and name.strip():
        return name
    return key


def render_explain(tenant, selectors=None, config_root="config"):
    check_tenant(tenant)
    lines = []
    for resource_type in _expand_selectors(selectors or []):
        refs = REFERENCES.get(resource_type)
        if not refs:
            continue
        path = config_path(tenant, resource_type, config_root=config_root)
        if not os.path.exists(path):
            continue
        data = load_json_object(path)
        items = data.get("items") or {}
        resource_lines = []
        lookups = {}
        for field, spec in refs.items():
            lookups[field] = load_lookup(
                tenant, spec["referent"], config_root=config_root
            )
        for key in sorted(items):
            item = items[key]
            if not isinstance(item, dict):
                continue
            field_lines = []
            for field in sorted(refs):
                values = _field_values(item.get(field))
                if not values:
                    continue
                field_lines.append(
                    "    %s: %s" % (field, _format_resolved(values, lookups[field]))
                )
            if not field_lines:
                continue
            resource_lines.append("  %s" % _item_label(key, item))
            resource_lines.extend(field_lines)
        if resource_lines:
            lines.append(resource_type)
            lines.extend(resource_lines)
    return "\n".join(lines) + ("\n" if lines else "")


def _usage():
    return "usage: python -m tools.lookup explain <tenant> [resource selectors...]\n"


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] != "explain" or len(argv) < 2:
        sys.stderr.write(_usage())
        return 2
    tenant = argv[1]
    selectors = argv[2:]
    try:
        sys.stdout.write(render_explain(tenant, selectors))
    except LookupDataError as e:
        sys.stderr.write("error: %s\n" % e)
        return 1
    except ValueError as e:
        sys.stderr.write("error: %s\n" % e)
        return 2
    except (IOError, OSError) as e:
        sys.stderr.write("error: %s\n" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
