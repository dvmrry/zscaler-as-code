"""Shared schema-reading core for the generators.

Loads the committed provider schema dumps and answers structural
questions: which provider owns a resource type, how attributes classify
(required / optional / computed-only), and how Terraform type encodings
map to HCL type expressions and JSON Schema fragments.

Stdlib-only, Python 3.6-floor syntax — see AGENTS.md rule 5.
"""
import json
import os

SCHEMA_DIR = os.path.join("schemas", "provider")
PROVIDER_PREFIXES = {"zia_": "zia", "zpa_": "zpa"}

_cache = {}


def _provider_for(resource_type):
    for prefix, provider in PROVIDER_PREFIXES.items():
        if resource_type.startswith(prefix):
            return provider
    raise KeyError("resource type %r has no known provider prefix" % resource_type)


def load_provider(provider):
    if provider not in _cache:
        path = os.path.join(SCHEMA_DIR, provider + ".json")
        with open(path) as f:
            _cache[provider] = json.load(f)
    return _cache[provider]


def load_resource(resource_type):
    """Return the schema entry for one resource type.

    Raises KeyError for unknown prefixes or resource types so a typo in
    tools/resources.txt fails the build instead of generating nothing.
    """
    provider = _provider_for(resource_type)
    schemas = load_provider(provider)["resource_schemas"]
    if resource_type not in schemas:
        raise KeyError("resource type %r not in %s schema" % (resource_type, provider))
    return schemas[resource_type]


def classify_attributes(block):
    """Split a block's attributes into required / optional / computed_only.

    required: must be supplied. optional: may be supplied (covers
    optional+computed). computed_only: provider-populated, excluded from
    input. All lists sorted for deterministic rendering. Fails loudly on
    plugin-framework nested_type attributes — none exist in the pinned
    schemas, and silent mishandling would corrupt generated modules.
    """
    out = {"required": [], "optional": [], "computed_only": []}
    for name, attr in sorted((block.get("attributes") or {}).items()):
        if "nested_type" in attr:
            raise ValueError(
                "attribute %r uses nested_type (plugin framework); "
                "the generator does not support it — add an override" % name
            )
        if attr.get("required"):
            out["required"].append(name)
        elif attr.get("optional"):
            out["optional"].append(name)
        else:
            out["computed_only"].append(name)
    return out
