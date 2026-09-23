# LLM-Wiki 0.5.2

LLM-Wiki `0.5.2` is a multimodal evidence and profile-isolation release for
the MCP 2.x application. It consumes the merged Kogwistar core and KG Doc
Parser revisions without changing their independent release versions.

## Release scope

- Bumps the LLM-Wiki package and application image version to `0.5.2`.
- Adds typed multimodal evidence spans and embedding-reference projections
  while preserving legacy text grounding.
- Keeps embedding profiles isolated, including profiles with the same vector
  dimension but different models or preprocessing.
- Keeps the default image free of optional Pinecone and Qdrant SDKs.
- Keeps the gated all-adapters image in the same Docker Hub repository.
- Keeps the optional PyPy 3.11 image in the same Docker Hub repository.

## Pinned source revisions

The release builds use immutable merged revisions:

```text
Kogwistar:             82bcd0544fa878a4082e40aba070b5f266c122e2
KG Doc Parser:         e7e42760e5578cce7d362b5b0e00996527e2ecbf
Obsidian sink:         bb9cec922c156465af8395e0dfb3b3cf76748f5f
Pinecone adapter:      f66f6bc9755f55ab0411a52838e0aae7c241f62c
Qdrant adapter:        af16f6864c1839add7e74f87d29b0d5503d626fc
```

## Docker image tags

The release workflow produces these application tags from the merged `v0.5.2`
commit:

```text
profchan/kogwistar-llm-wiki:v0.5.2
profchan/kogwistar-llm-wiki:all-v0.5.2
profchan/kogwistar-llm-wiki:pypy3.11-v0.5.2
```

The embedding service remains a separate image because it has a different
runtime and GPU/resource profile.

## Version policy

This release does not rename or retag existing images. The `0.5.0` and
`0.5.1` release notes remain historical, and the Kogwistar core, KG Doc
Parser, Obsidian sink, Pinecone adapter, and Qdrant adapter retain their own
release versions.
