# LLM-Wiki 0.5.3

LLM-Wiki `0.5.3` is the downstream alignment release for the merged
Kogwistar 0.6 substrate and its compatible parser and projection adapters.

## Release scope

- Bumps the LLM-Wiki package and application image version to `0.5.3`.
- Uses the merged Kogwistar 0.6 projection, telemetry, authority, and lane
  contracts.
- Keeps maintenance scheduling, memory authority, provenance, and proposal
  acceptance owned by LLM-Wiki.
- Keeps the default application image free of optional Pinecone and Qdrant
  SDKs; the gated all-adapters image remains a separate target.
- Keeps the PyPy 3.11 image in the same Docker Hub repository as the main
  application image.

## Pinned source revisions

The release builds use immutable merged revisions:

```text
Kogwistar:       ae24e1663a66deb0e65d6e2bf74a2e5ef317e1ea
KG Doc Parser:   9a6ad09d779782466808b084396bdd16e3a4ca95
Obsidian sink:   8724c36c5d6fec89db4902baae0610e0ac77e4b8
```

Pinecone and Qdrant are consumed through their released `0.6` compatibility
ranges and are not copied into the default image.

## Docker image tags

The versioned release workflow produces:

```text
profchan/kogwistar-llm-wiki:v0.5.3
profchan/kogwistar-llm-wiki:all-v0.5.3
profchan/kogwistar-llm-wiki:pypy3.11-v0.5.3
```

The embedding service remains a separate image because it has a different
runtime and GPU/resource profile.
