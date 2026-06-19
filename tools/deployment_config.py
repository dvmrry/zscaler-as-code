"""Deployment config accessor -- the single reader for the one committable root
config file, deployment.json.

A deployment copies deployment.example.json to deployment.json and commits THAT
in its private fork. The template ships only the example and never overwrites
deployment.json (it is absent upstream), so the copy is sync-safe. deployment.json
names the private overlay directory and any other deployment pointers; this
module is the one place Make and Python read it, so the config stays
single-source. When deployment.json is absent (the template itself, or CI),
DEFAULTS apply.

Stdlib only, Python 3.6 floor.
"""
import json
import os
import sys

CONFIG_FILE = "deployment.json"

DEFAULTS = {
    "overlay": "_local",
}


def load(root="."):
    """DEFAULTS merged with deployment.json if present. Keys beginning with '$'
    are treated as comments and ignored."""
    out = dict(DEFAULTS)
    path = os.path.join(root, CONFIG_FILE)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key, value in data.items():
                if not key.startswith("$"):
                    out[key] = value
    return out


def overlay_dir(root="."):
    """The private overlay directory name (deployment.json 'overlay', default
    _local). Falls back to the default for an empty/missing value."""
    return load(root).get("overlay") or DEFAULTS["overlay"]


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    key = argv[0] if argv else "overlay"
    value = load().get(key, "")
    sys.stdout.write("%s\n" % ("" if value is None else value))
    return 0


if __name__ == "__main__":
    sys.exit(main())
