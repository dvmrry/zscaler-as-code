"""Cross-pipeline consistency lint — stop sibling pipelines from drifting.

The recurring failure this guards: the bootstrap / drift / delivery pipelines
each re-state the same setup (terraform install, provider-auth env, backend
config), and nothing fails when the copies diverge. You find out at runtime
(a red run) or — worse — silently (a dropped ZIA_CLOUD panics the provider at
ConfigureProvider; a dropped HTTPS_PROXY; a mistyped ZSCALER_USE_LEGACY_CLIENT).
This linter turns drift into a loud failure at commit, the same way
`make refresh-gates` keeps gate logic in one pulled place instead of in adapted
YAML that never updates on pull.

Each rule is grounded in an incident this project actually hit:

  R1  terraform version must be IDENTICAL across every pipeline
      -> 1.15.4 is pinned in ~8 places; bump one, the rest silently drift.
  R2  provider-auth must route through steps/zscaler-auth.yml, never a
      hand-rolled `eval cred_env` / creds-make in a bare `script:` step
      -> a hand-assembled env block dropped ZIA_CLOUD and the provider
         panicked at ConfigureProvider ("Plugin did not respond").
  R3  non-secret config (clouds, the legacy flag, proxy, customer id) must
      NOT be hand-mapped in a step `env:` block
      -> in ADO only SECRETS need explicit env mapping; non-secret
         variable-group vars auto-expose to every step. The legacy flag kept
         getting dropped precisely because it was hand-typed as if it needed
         mapping — it does not.
  R4  one backend.conf strategy repo-wide (committed vs materialized inline)
      -> bootstrap materialized it inline while delivery read it from the
         repo; the split is how a job ends up planning against no backend.
  R5  the auth template's `tenant:` must be COMPILE-TIME, never `$(...)`
      -> ADO does not substitute runtime macros in env-var KEYS, so a
         runtime tenant silently maps no secrets at all.

No YAML library (stdlib-only, Python 3.6-floor — AGENTS.md rule 5), so the
files are scanned as structured text: one indentation-aware pass tracks block
scalars and the immediate key path, which is all these rules need. ERRORs gate
(exit 1); WARNs report. Every line carries a suggested fix — the output is the
decision table.
"""
import os
import re
import sys


class Report(object):
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, where, problem, fix):
        self.errors.append("ERROR %s: %s — %s" % (where, problem, fix))

    def warn(self, where, problem, fix):
        self.warnings.append("WARN  %s: %s — %s" % (where, problem, fix))


# ---------------------------------------------------------------------------
# A tiny structured-text scanner — NOT a YAML parser, just enough structure
# for the rules: per line we need (a) is it a comment, (b) is it the body of a
# block scalar and under which owner key, (c) for a mapping line, its key and
# its immediate parent key. That answers "is this an env: entry?" and "is this
# make command in a `script:` or a `command:`?".
# ---------------------------------------------------------------------------

class Line(object):
    __slots__ = ("n", "raw", "indent", "kind", "key", "value",
                 "parent_key", "block_owner")

    def __init__(self, n, raw, indent, kind, key, value, parent_key,
                 block_owner):
        self.n = n
        self.raw = raw
        self.indent = indent
        self.kind = kind            # blank | comment | mapping | seq | scalar
        self.key = key
        self.value = value
        self.parent_key = parent_key
        self.block_owner = block_owner


_BLOCK_SCALAR = re.compile(r"^[|>][+-]?\d*\s*$")


def _split_key_value(s):
    """Split 'key: value' at the first top-level colon-separator.

    Respects ${{ ... }} / $( ... ) expressions, [ ... ] flow seqs, and quotes
    so a colon inside them does not split. Returns (key, value) — value is ""
    when the colon ends the line — or (None, None) for a plain scalar / seq
    item with no mapping colon.
    """
    depth = 0
    quote = None
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if quote is not None:
            if c == quote:
                quote = None
        elif c in ("'", '"'):
            quote = c
        elif c in "{[(":
            depth += 1
        elif c in "}])":
            if depth > 0:
                depth -= 1
        elif c == ":" and depth == 0:
            if i + 1 >= n or s[i + 1] in " \t":
                return s[:i].strip(), s[i + 1:].strip()
        i += 1
    return None, None


def _parent_for(stack, indent):
    parent = None
    for ind, key in stack:
        if ind < indent:
            parent = key
        else:
            break
    return parent


def _push(stack, indent, key):
    while stack and stack[-1][0] >= indent:
        stack.pop()
    stack.append([indent, key])


def parse(text):
    """Scan YAML text into Line records. Stdlib-only, ADO/GHA subset."""
    out = []
    stack = []          # ascending list of [indent, key]
    block = None        # [owner_indent, owner_key] while inside a block scalar
    for n, raw in enumerate(text.split("\n"), 1):
        stripped = raw.strip()
        # YAML forbids tabs for indentation, but a hand-edit may slip one in;
        # expand so a tab-indented line isn't read as column 0.
        _exp = raw.expandtabs(8)
        indent = len(_exp) - len(_exp.lstrip(" "))
        if block is not None:
            owner_indent, owner_key = block
            if stripped == "" or indent > owner_indent:
                out.append(Line(n, raw, indent, "scalar", None, None,
                                owner_key, owner_key))
                continue
            block = None        # shallower line ends the scalar; fall through
        if stripped == "":
            out.append(Line(n, raw, indent, "blank", None, None, None, None))
            continue
        if stripped.startswith("#"):
            out.append(Line(n, raw, indent, "comment", None, None, None, None))
            continue
        # Peel leading "- " sequence markers, advancing the effective indent
        # to the column where the item content begins (ADO step lists).
        eff = indent
        content = stripped
        is_seq = False
        while content[:1] == "-" and (len(content) == 1 or content[1] == " "):
            is_seq = True
            eff += 2
            content = content[1:].lstrip(" ")
            if content == "":
                break
        key, value = _split_key_value(content) if content else (None, None)
        if key is not None:
            parent = _parent_for(stack, eff)
            _push(stack, eff, key)
            # a block-scalar indicator can carry a trailing comment: `| # ...`
            if value is not None and _BLOCK_SCALAR.match(
                    re.sub(r"\s+#.*$", "", value)):
                block = [eff, key]
            out.append(Line(n, raw, eff, "mapping", key, value, parent, None))
        else:
            parent = _parent_for(stack, eff)
            out.append(Line(n, raw, eff, "seq" if is_seq else "scalar",
                            None, content, parent, None))
    return out


# ---------------------------------------------------------------------------
# R1 — terraform version consistency (cross-file)
# ---------------------------------------------------------------------------

_VERSION_PATTERNS = (
    re.compile(r"terraform_version\s*:\s*['\"]?(\d+\.\d+\.\d+)"),   # GHA
    re.compile(r"terraformVersion\s*:\s*['\"]?(\d+\.\d+\.\d+)"),    # ADO task
    re.compile(r"install-tf\s+VERSION=(\d+\.\d+\.\d+)"),            # make target
    re.compile(r"\bTF_VERSION\s*[:=]\s*['\"]?(\d+\.\d+\.\d+)"),
)


def collect_versions(parsed_files):
    """[(path, lines)] -> [(version, path, line_no)] from non-comment lines."""
    found = []
    for path, lines in parsed_files:
        for ln in lines:
            if ln.kind == "comment":
                continue
            text = re.sub(r"\s+#.*$", "", ln.raw)   # ignore a trailing comment
            for pat in _VERSION_PATTERNS:
                m = pat.search(text)
                if m:
                    found.append((m.group(1), path, ln.n))
                    break
    return found


def check_terraform_version(found, report, expected=None):
    if not found:
        return
    if expected:
        for v, path, n in found:
            if v != expected:
                report.error("%s:%d" % (path, n),
                             "terraform %s != expected %s" % (v, expected),
                             "pin every pipeline to %s" % expected)
        return
    versions = sorted(set(v for v, _, _ in found))
    if len(versions) > 1:
        locs = "; ".join("%s:%d=%s" % (p, n, v) for v, p, n in sorted(
            found, key=lambda t: (t[1], t[2])))
        report.error("terraform version",
                     "pipelines pin DIFFERENT terraform versions %s"
                     % ", ".join(versions),
                     "pin them all to one version [%s]" % locs)


# ---------------------------------------------------------------------------
# R2 — provider-auth must route through the shared step template
# ---------------------------------------------------------------------------

# Targets that need provider credentials. The trailing (?![\w-]) keeps a bare
# verb from matching a compound creds-FREE sibling — `plan` must not fire on
# `plan-report`/`plan-checks`, `drift` not on `drift-report`, `fetch` not on
# `fetch-diag`. The non-greedy `\S+ ` eater skips make flags AND their args
# (`make -C . plan`). Longer alternatives precede their prefixes.
_CREDS_MAKE = re.compile(
    r"\bmake\s+(?:\S+\s+)*?\b"
    r"(fetch|plan-changed|plan|apply|drift|import-one|stage-imports|statefill"
    r"|refresh-gates)(?![\w-])")
# ADO shorthands whose block body is shell, same as `script:`.
_SHELL_BLOCKS = ("script", "bash", "powershell", "pwsh")


def check_auth_routing(path, lines, report):
    for ln in lines:
        if ln.kind == "comment":
            continue
        if "cred_env" in ln.raw and ln.kind in ("scalar", "mapping"):
            report.error("%s:%d" % (path, ln.n),
                         "cred_env invoked inline in a pipeline",
                         "route auth through steps/zscaler-auth.yml; a "
                         "hand-rolled env block is how a var gets dropped "
                         "(the provider then panics at ConfigureProvider)")
        if ln.kind == "scalar" and ln.block_owner in _SHELL_BLOCKS:
            m = _CREDS_MAKE.search(ln.raw)
            if m:
                report.error("%s:%d" % (path, ln.n),
                             "'make %s' run in a bare script step" % m.group(1),
                             "pass it as `command:` to steps/zscaler-auth.yml "
                             "so the auth env cannot be dropped")


# ---------------------------------------------------------------------------
# R3 — non-secret config must not be hand-mapped in a step env block
# ---------------------------------------------------------------------------

def config_kind(key):
    """Return a label if `key` is a known NON-secret config var, else None.

    These auto-expose from an ADO variable group and must never be hand-typed
    into a step env: block. Secrets (PASSWORD/API_KEY/CLIENT_SECRET/SAS_TOKEN)
    deliberately do NOT match — those DO need mapping.
    """
    k = key.upper().strip()
    if k.startswith("${{") or k.startswith("$("):
        return None
    if k.endswith("_CLOUD") or k == "CLOUD":
        return "cloud"
    if k.endswith("USE_LEGACY_CLIENT"):
        return "legacy-mode flag"
    if "PROXY" in k:
        return "proxy"
    if k.endswith("CUSTOMER_ID") and "ZPA" in k:
        return "ZPA customer id"
    if k.endswith("CA_BUNDLE"):
        return "CA bundle"
    return None


def check_config_in_env(path, lines, report):
    for ln in lines:
        if ln.kind != "mapping" or ln.parent_key != "env":
            continue
        kind = config_kind(ln.key)
        if kind:
            report.error("%s:%d" % (path, ln.n),
                         "%s '%s' hand-mapped in a step env block"
                         % (kind, ln.key),
                         "put it in the variable group (non-secret vars "
                         "auto-expose to every step); per-step mapping is how "
                         "it gets dropped")


# ---------------------------------------------------------------------------
# R5 — the auth template's tenant must be compile-time
# ---------------------------------------------------------------------------

def check_tenant_compile_time(path, lines, report):
    for ln in lines:
        if ln.kind != "mapping" or ln.key != "tenant":
            continue
        v = ln.value.strip("'\"") if ln.value else ""   # quoted or bare macro
        if v.startswith("$("):
            report.error("%s:%d" % (path, ln.n),
                         "auth-template tenant set to a runtime macro %s" % v,
                         "use a compile-time value (a parameter or "
                         "${{ variables.X }}); ADO can't macro-substitute "
                         "env-var keys, so a runtime tenant maps nothing")


# ---------------------------------------------------------------------------
# R4 — one backend.conf strategy across the ADO pipelines (cross-file, WARN)
# ---------------------------------------------------------------------------

_BACKEND_WRITE = re.compile(
    r"(?:>>?|tee\b)[^\n]*backend\.conf|printf[^\n]*backend\.conf")


def backend_profile(lines):
    """(references_backend, materializes_inline) for one file."""
    references = False
    materializes = False
    for ln in lines:
        if ln.kind == "comment":
            continue
        if "backend.conf" in ln.raw:
            references = True
        if ln.kind == "scalar" and _BACKEND_WRITE.search(ln.raw):
            materializes = True
    return references, materializes


def check_backend_strategy(ado_files, report):
    inline = []
    committed = []
    for path, lines in ado_files:
        references, materializes = backend_profile(lines)
        if not references:
            continue
        if materializes:
            inline.append(path)
        else:
            committed.append(path)
    if inline and committed:
        report.warn("backend.conf strategy",
                    "split across pipelines — materialized inline in %s but "
                    "read from the repo in %s"
                    % (", ".join(sorted(inline)), ", ".join(sorted(committed))),
                    "pick one: commit backend.conf (checkout restores it) OR "
                    "materialize it in every state job — not a mix")


# ---------------------------------------------------------------------------
# Discovery / classification / driver
# ---------------------------------------------------------------------------

def discover(directory):
    paths = []
    for root, _dirs, files in os.walk(directory):
        for f in files:
            if f.endswith(".yml") or f.endswith(".yaml"):
                paths.append(os.path.join(root, f))
    return sorted(paths)


def classify(path, text):
    norm = path.replace(os.sep, "/")
    if "/steps/" in norm:
        return "template"
    base = os.path.basename(path).lower()
    if base.startswith("azure-pipelines"):
        return "ado"
    if "github" in base or "gha" in base:
        return "gha"
    if "bitbucket" in base:
        return "bitbucket"
    if "runs-on:" in text or "uses:" in text:
        return "gha"
    if re.search(r"^\s*image\s*:", text, re.M) and "pipelines:" in text:
        return "bitbucket"
    return "ado"        # default unknown to the strictest ruleset


def lint_paths(paths, expected_tf=None):
    """Lint an explicit list of pipeline files. Cross-file rules span exactly
    this set — so pass ONLY the operative pipelines (not the shipped examples)
    when your pipelines live in a shared/root directory."""
    report = Report()
    parsed = []
    roles = {}
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        roles[p] = classify(p, text)
        parsed.append((p, parse(text)))
    # Cross-file: terraform version spans every platform's pipelines.
    check_terraform_version(collect_versions(parsed), report, expected_tf)
    # Per-file ADO rules (templates are where shared auth/config legitimately
    # live, so the "don't hand-roll it" rules apply to PIPELINES, not them).
    for p, lines in parsed:
        if roles[p] != "ado":
            continue
        check_auth_routing(p, lines, report)
        check_config_in_env(p, lines, report)
        check_tenant_compile_time(p, lines, report)
    check_backend_strategy(
        [(p, l) for p, l in parsed if roles[p] == "ado"], report)
    return report


def lint_dir(directory, expected_tf=None):
    paths = discover(directory)
    return lint_paths(paths, expected_tf), len(paths)


_USAGE = ("usage: python -m tools.lint_pipelines [--dir DIR | FILE ...] "
          "[--tf-version X.Y.Z] [--strict]\n")


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    directory = "pipelines"
    files = []
    expected_tf = None
    strict = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--dir" and i + 1 < len(argv):
            i += 1
            directory = argv[i]
        elif a == "--tf-version" and i + 1 < len(argv):
            i += 1
            expected_tf = argv[i]
        elif a == "--strict":
            strict = True
        elif a.startswith("-"):
            sys.stderr.write(_USAGE)
            return 2
        else:
            # A positional arg is an explicit file to lint. Use this when your
            # pipelines share a directory with other YAML (e.g. the repo root,
            # alongside the shipped examples): name only the operative files so
            # the cross-file rules don't compare them against the examples.
            files.append(a)
        i += 1
    if files:
        missing = [f for f in files if not os.path.isfile(f)]
        if missing:
            sys.stderr.write("error: no such file(s): %s\n"
                             % ", ".join(missing))
            return 2
        report = lint_paths(files, expected_tf)
        count = len(files)
    else:
        if not os.path.isdir(directory):
            sys.stderr.write("error: no such directory: %s\n" % directory)
            return 2
        report, count = lint_dir(directory, expected_tf)
    if count == 0:
        sys.stderr.write("error: no pipeline YAML under %s — checking nothing "
                         "is not success\n" % directory)
        return 2
    for w in report.warnings:
        sys.stdout.write(w + "\n")
    for e in report.errors:
        sys.stdout.write(e + "\n")
    sys.stdout.write("\n%d pipeline file(s) checked, %d error(s), "
                     "%d warning(s)\n"
                     % (count, len(report.errors), len(report.warnings)))
    gate = len(report.errors) + (len(report.warnings) if strict else 0)
    return 1 if gate else 0


if __name__ == "__main__":
    sys.exit(main())
