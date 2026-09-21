# LLM-Wiki 0.4.1

This release records the merged PyPy and cross-repository CI work on the
LLM-Wiki `main` branch. It updates the application package version to `0.4.1`.
Creating `v0.4.1` and publishing Docker images remain separate release actions.

## Tested Dependency Pins

The application workflows check out these immutable sibling revisions:

| Component | Revision | Role |
|---|---|---|
| Kogwistar | `519f69de352f61e12ab316b300818e1564650650` | Core runtime and graph substrate |
| kg-doc-parser | `c82a9e59217a7292d78fdce51300b17010112327` | Parser workflow and layered ingestion |
| kogwistar-obsidian-sink | `cb7ccf1247ac3875356d2cb9f1849b1796484c08` | Obsidian projection sink |

The parser main revision includes the refreshed Poetry lock metadata for the
merged Kogwistar revision.

## Compatibility Gates

The merged main validation covered:

- CPython 3.12, 3.13, and 3.14 provider-free CI suites;
- direct PyPy 3.11 CI using `pypy-3.11-v7.3.20`;
- official MCP SDK lifecycle and authenticated read/write protocol tests;
- Kogwistar Rust checks and Python lint;
- PyPy 3.12 as a separate experimental profile;
- same-host slots/runtime benchmarks;
- the Linux PyPy 3.11 container smoke test.

The primary PyPy gate is the direct GitHub runner job. The container workflow
is a secondary deployment smoke test and is not used as the compatibility
authority.

## Runtime Notes

- CPython uses the Joblib cache backend by default.
- PyPy uses the DiskCache backend through the shared cache abstraction.
- The MCP implementation uses the official `mcp` SDK on both CPython and PyPy;
  standalone FastMCP is not part of the supported path.
- PyPy 3.12 remains experimental and is not a release requirement for the
  PyPy 3.11 compatibility target.

## Release Procedure

After reviewing the release workflow and image contents, create the matching
tag and push it explicitly:

```powershell
git tag v0.4.1
git push origin v0.4.1
```

The tag must match the package version in `pyproject.toml`. Docker Hub
publishing is triggered by the repository release workflow; this documentation
change does not publish an image by itself.
