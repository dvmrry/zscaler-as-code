---
name: operate-change
description: >
  Use for BAU Zscaler config edits from a ticket -- add/remove a URL, keyword,
  IP range, location IP, or app-segment domain; enable/disable a URL-filtering /
  SSL-inspection / cloud-app-control rule or a ZPA app segment / segment group.
  Allowlisted single edits only; produces a reviewed draft PR, never applies.
---
# Operate Change
Load and follow `../../../docs/workflows/operate-change.md` and its harness
`../../../docs/workflows/operate-change.harness.md`. The canonical workflow is the
source of truth; this skill is only a portable runtime entrypoint.
