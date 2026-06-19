# Extending the template downstream

`zscaler-as-code` is a **template**. `main` is the source of truth, and the
template's own files (the `Makefile`, generators, `modules/`, `docs/`,
pipelines, `.gitignore`) reach a deployment by `git pull` and are
**overwritten on every template update**. So a deployment never customizes the
template by editing those files -- the edits would be clobbered and would fork
the deployment from the template.

Instead, every deployment-specific customization lives in a **deployment-owned**
file or directory the template does not ship and never overwrites. The whole
customization surface is small and **contained**: one config file, one overlay
directory, plus `local.mk` for targets and the existing gitignored areas for
secrets.

## The extension points

| Need | Where it goes | Template-owned? |
|------|---------------|-----------------|
| Extra `make` targets / variable overrides | `local.mk` (auto-`-include`d) | No |
| Deployment config (overlay name + pointers) | `deployment.json` (copy of `deployment.example.json`) | No -- you commit it |
| Deployment-private data and config | the **overlay directory** (named in `deployment.json`) | No -- you commit it |
| Sensitive / ephemeral data (real pulls, secrets) | the gitignored areas (`pulls/`, `backend.conf`, `OPERATING.local.md`) | Gitignored |

The rule is the same for all of them: **never edit a template-owned file to
customize a deployment.** If you want to, there is an extension point for it.

## The two-piece deployment surface

A deployment's customization is two **committable** things (versioned in your
private fork), plus the existing gitignored areas for anything secret:

1. **`deployment.json`** -- one little root config file, copied from the
   template's `deployment.example.json`. It names your overlay directory and
   any other deployment pointers. It is plain JSON -- your scripts and make
   targets read it directly; the template ships no reader for it.
2. **The overlay directory** -- named in `deployment.json`, this is where your
   deployment-owned data and config live, versioned in your fork.

### Why this is conflict-free on a template update

- `deployment.json` and your overlay directory are **absent from the upstream
  template** -- the template ships only `deployment.example.json` and has no
  overlay dir. So a sync (a merge from upstream) sees them as "added by your
  fork, untouched by upstream" and never collides. This is the same mechanism
  that makes `local.mk` safe.
- Every template gate is **path-scoped** to the template's own directories:
  `generate CHECK=1` -> `modules/`, `schemas/`; `check-demo` ->
  `config/<tenant>/`, `imports/<tenant>/`; `check-envs` -> `envs/`. Nothing
  scans the repo root, so a committable overlay directory never trips a gate.

### Using it

```sh
cp deployment.example.json deployment.json     # your committable config
$EDITOR deployment.json                         # set "overlay" to e.g. "acme-corp"
mkdir acme-corp                                 # your committable overlay dir
# ... put deployment data/config in acme-corp/ ...
git add deployment.json acme-corp               # commit both in your fork
```

`deployment.json`:

```json
{
  "overlay": "acme-corp"
}
```

That is the whole setup. `deployment.json` is plain JSON, so your scripts read it
with a one-line `json.load`, and a `local.mk` target that needs the name can read
it the same way -- the template ships no reader because nothing in the template
consumes the overlay. The config is extensible -- add your own keys for future
pointers (keys beginning with `$` are treated as comments).

### Sensitive data

The overlay is **committable** -- it is for data and config you *want* versioned
in your private fork. Anything you must not commit *anywhere* (real tenant
pulls, credentials, backend secrets) stays in the existing gitignored areas
(`pulls/`, `backend.conf`, `OPERATING.local.md`), never the overlay. If you need
a gitignored sub-area of your own, add it to your fork's `.git/info/exclude` (a
per-clone ignore file that is never committed and never overwritten by a
template update) -- never to `.gitignore`, which is template-owned.

### What does NOT belong in the overlay

The overlay is deployment-private and the template cannot see it. Do not put
anything in it that the template itself must build or test. If the template
needs to consume a downstream input, that flows through an explicit, sanitized
**artifact the template reads by an exact path** (the same decoupling used for
contract facts and pulls) -- never by the template reaching into the overlay.
