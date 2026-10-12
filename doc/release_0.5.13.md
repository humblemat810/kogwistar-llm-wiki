# LLM-Wiki 0.5.13

## Release Status

- Version: `0.5.13`
- Parser dependency: `graph-knowledge-doc-parser>=0.2.14,<0.3`
- Parser release: `0.2.14`
- Kogwistar release: `0.6.8`
- Obsidian source plugin: optional external package; not bundled or pinned

## Changes

- Add the public `ObsidianSourceHost` contract for an external managed-vault
  source plugin.
- Add explicit Workbench extension boundaries and request validation for
  `/plugins/obsidian-source/` routes.
- Keep the existing Obsidian sink as the one-way projection exporter.
- Keep the Obsidian source package outside the standard LLM-Wiki dependency set
  and Docker image.
- Document the ACL, revision-fencing, graph-space validation, and explicit
  `scan -> plan -> apply` requirements for future host integration.

The external `kogwistar-obsidian-source` repository owns vault parsing,
deterministic export, import planning, and conflict detection. This release
only publishes the LLM-Wiki host boundary; it does not claim that the
production `WorkbenchApi` graph adapter is complete. Enabling the source
extension without those host callbacks fails closed.

## Release Gate

Run the complete LLM-Wiki CI matrix, including CPython 3.12-3.14 and PyPy
3.11, before merging or tagging `v0.5.13`. Do not publish the source plugin in
the standard application image. The source plugin must complete its own CI and
release process before an opt-in integration profile is added.
