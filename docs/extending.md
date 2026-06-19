# Extending the template downstream

`zscaler-as-code` is a **template**. `main` is the source of truth, and the
template's own files (the `Makefile`, generators, `modules/`, `docs/`,
pipelines, `.gitignore`) reach a deployment by `git pull` and are
**overwritten on every template update**. So a deployment never customizes the
template by editing those files -- the edits would be clobbered and would fork
the deployment from the template.

Instead, every deployment-specific customization lives in a **deployment-owned**
file or directory the template does not ship and never touches.

## The extension points

| Need | Where it goes | Template-owned? |
|------|---------------|-----------------|
| Extra `make` targets / variable overrides | `local.mk` (auto-`-include`d) | No |
| Deployment-private data and config | a **deployment overlay directory** (below) | No |
| Operating notes for your environment | `OPERATING.local.md` (gitignored) | No |
| Remote-state backend settings | `backend.conf` (gitignored) | No |

The rule is the same for all of them: **never edit a template-owned file to
customize a deployment.** If you want to, there is an extension point for it.

## The deployment overlay directory

The overlay is the `local.mk` analogue for **data**: a private, top-level
directory where a deployment keeps its own data and config -- tenant overlays,
intake artifacts, deployment-specific inputs, anything produced or stored
downstream -- without ever colliding with a template update.

It works because **every template gate is path-scoped** to the template's own
directories:

- `make generate CHECK=1` checks `modules/`, `schemas/provider`, `schemas/tfvars`
- `make check-demo` checks `config/<tenant>/` and `imports/<tenant>/`
- `make check-envs` checks `envs/`

Nothing scans the repository root, so a top-level overlay directory is
invisible to every gate -- it can never make a check fail or show up as
template drift. This guarantee is pinned by
`tools/tests/test_deployment_overlay.py`; if a future change makes a gate scan
the root, that test fails.

### Using it

**Default (zero config).** Put your data under `_local/`. It is gitignored by
the template, and the `Makefile` variable `OVERLAY` defaults to `_local`, so
`local.mk` targets can reference `$(OVERLAY)/...` with no setup.

**A named overlay (clearer, recommended).** Many deployments prefer a directory
named for the deployment (e.g. the org name) so its purpose is obvious. Two
deployment-side steps, neither touching a template-owned file:

1. Tell **git** to ignore it -- add the name to `.git/info/exclude`, a
   per-clone ignore file that is never committed and never overwritten by a
   template update. Do **not** add it to `.gitignore` (template-owned).

   ```sh
   echo '/acme-corp/' >> .git/info/exclude
   ```

2. Tell **make** the name -- override `OVERLAY` in `local.mk`:

   ```make
   OVERLAY = acme-corp
   ```

Now `$(OVERLAY)` resolves to your directory everywhere, your `local.mk` targets
can read and write it, and template updates leave it untouched.

### What does NOT belong in the overlay

The overlay is deployment-private and the template cannot see it. Do not put
anything in it that the template itself must build or test. If the template
needs to consume a downstream input, that flows through an explicit, sanitized
**artifact the template reads by an exact path** (the same decoupling used for
contract facts and pulls) -- never by the template reaching into the overlay.
