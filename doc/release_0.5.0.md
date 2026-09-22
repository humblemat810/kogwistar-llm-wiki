# LLM-Wiki 0.5.0

LLM-Wiki `0.5.0` is the MCP 2.x release. It is built on Kogwistar
`0.5.0`, KG Doc Parser `0.2.0`, and the Obsidian sink `0.2.0`.

## Highlights

- Migrates the application MCP adapter to `mcp>=2.2.0,<3`.
- Keeps `/mcp` as the preferred Streamable HTTP endpoint.
- Retains stdio, legacy `/sse` and `/messages`, REST tool endpoints, and
  wire compatibility for supported older MCP clients.
- Preserves request-scoped authentication, workspace and namespace isolation,
  tool filtering, and explicit argument validation.
- Adds the verified PyPy 3.11 native image build path. The native extension
  is built and smoke-tested in the image; it is published separately from
  the standard CPU application image.

## Dependency pins

The release workflows use these immutable revisions:

| Component | Revision |
| --- | --- |
| Kogwistar | `05a474474a83e7d8ca1b4720afd6ef6fa190c2a0` |
| KG Doc Parser | `a80b9d846452bb0232f5569c3def08f916810fe8` |
| Obsidian sink | `bb9cec922c156465af8395e0dfb3b3cf76748f5f` |

## Docker images

After tagging the merged commit as `v0.5.0`, the publish workflows produce:

```text
profchan/kogwistar-llm-wiki:v0.5.0
profchan/kogwistar-llm-wiki:latest
profchan/kogwistar-llm-wiki-pypy311-native:pypy3.11-v0.5.0
```

The standard application image is Torch-free. Multimodal embedding inference
continues to run in the separate embedding service. The PyPy image is an
optional native-runtime image and does not replace the standard image.

## Verification

The merged release was verified by the normal CI matrix on CPython 3.12,
3.13, 3.14, and PyPy 3.11, including the PyPy container smoke test and the
native-extension smoke test. The PyPy 3.12 beta lane remains experimental and
is not a release gate.
