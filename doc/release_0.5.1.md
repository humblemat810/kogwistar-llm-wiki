# LLM-Wiki 0.5.1

LLM-Wiki `0.5.1` is a maintenance release for the MCP 2.x application and
unified adapter-image publishing path. The foundational dependency versions
remain independently versioned and are not changed by this release.

## Release scope

- Bumps the LLM-Wiki package and application image version to `0.5.1`.
- Keeps the default image free of optional Pinecone and Qdrant SDKs.
- Keeps the gated all-adapters image in the same Docker Hub repository.
- Keeps the optional PyPy 3.11 image in the same Docker Hub repository.

## Docker image tags

The release workflow produces these application tags from the merged `v0.5.1`
commit:

```text
profchan/kogwistar-llm-wiki:v0.5.1
profchan/kogwistar-llm-wiki:all-v0.5.1
profchan/kogwistar-llm-wiki:pypy3.11-v0.5.1
```

The embedding service remains a separate image because it has a different
runtime and GPU/resource profile.

## Version policy

This release does not rename or retag existing images. The `0.5.0` release
notes remain historical, and the Kogwistar core, KG Doc Parser, Obsidian sink,
Pinecone adapter, and Qdrant adapter retain their own release versions.
