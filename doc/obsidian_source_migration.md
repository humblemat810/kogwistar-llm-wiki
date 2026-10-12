# Obsidian Source Migration

`kogwistar-obsidian-sink` remains the supported one-way projection exporter.
The new `kogwistar-obsidian-source` package is a separate, optional plugin for
explicitly synchronized managed vaults. Installing it does not enable it and
the standard LLM-Wiki image does not include it.

## Managed Vault Contract

The source plugin owns only the vault representation. The graph host owns ACLs,
revision checks, plan persistence, and mutation through the normal
revision-fenced maintenance patch path. A managed vault contains:

- `.kogwistar/obsidian-source/manifest.json`
- deterministic `Notes/<kind>/<title>--<identity-hash>.md` files;
- JSON frontmatter with the workspace/entity identity, baseline revision and
  digest, and `kogwistar-obsidian-source/v1` format version;
- a canonical JSON relation block between the relation markers.

Wikilinks, headings, and canvases are presentation only. They never create
graph relations during import. Unsupported canonical fields remain in the
manifest sidecar payload so a later export does not silently discard them.
Arbitrary Markdown, attachments, canvases, symlinks, path escapes, malformed
records, and unknown format versions are reported and skipped or fail closed;
they are not imported as graph entities.

## Explicit Workflow

The safe workflow is always `scan -> plan -> apply`:

```text
kogwistar-obsidian-source scan --vault ./vault
kogwistar-obsidian-source plan-import \
  --snapshot ./graph-snapshot.json --vault ./vault --output ./plan.json
kogwistar-obsidian-source conflicts --plan ./plan.json
kogwistar-obsidian-source apply-import \
  --plan ./plan.json --host trusted_host:factory
```

`scan` and `plan-import` are read-only. Applying requires a persisted plan ID;
the LLM-Wiki host must recheck the current graph revision and ACL inside the
mutation unit of work. A stale plan, changed authorization, changed relation
endpoint, or graph/vault concurrent edit is rejected rather than overwritten.

Missing managed files are ignored by default. To request explicit tombstones,
plan with `--deletion-mode tombstone`; only manifest-tracked records can then be
tombstoned.

## Explicit LLM-Wiki Enablement

Install the plugin in an opt-in environment and set:

```text
LLM_WIKI_WORKBENCH_EXTENSIONS=obsidian-source
```

The host integration must provide the typed `ObsidianSourceHost` callbacks. It
must not expose raw engines, storage paths, or credentials to the plugin. The
routes are limited to `/plugins/obsidian-source/`; reads require read scope and
`apply-import` requires write scope. The sink remains unchanged during this
migration and should be used to produce a managed baseline before enabling
source imports.

## Current Boundary

The source repository provides the format, deterministic conversion, planner,
CLI, and Workbench route adapter. The LLM-Wiki host binding is intentionally an
explicit deployment seam: it must translate accepted `SyncIntent` records into
existing ACL-checked, revision-fenced maintenance patches. No plugin code may
write raw graph storage or immutable source/revision artifacts directly.
