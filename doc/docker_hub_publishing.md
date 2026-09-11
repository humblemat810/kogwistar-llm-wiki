# Docker Hub Publishing

The repository can publish three public images through
`.github/workflows/publish-dockerhub.yml`:

| Image | Tags | Purpose |
| --- | --- | --- |
| `kogwistar-llm-wiki` | `latest`, release version | Torch-free REST/MCP application |
| `kogwistar-llm-wiki-representation` | `latest`, `cuda12.8`, release version | GPU-default Qwen3-VL service |
| `kogwistar-llm-wiki-representation` | `cpu`, release version with `-cpu` | CPU fallback and smoke tests |

The representation images contain the inference runtime but do not contain the
Qwen checkpoint. Mount or configure the Hugging Face cache and set an immutable
`LLM_WIKI_REPRESENTATION_MODEL_REVISION` before starting the service.

## One-Time Docker Hub Setup

1. Create two **public** Docker Hub repositories under the intended account:
   `kogwistar-llm-wiki` and `kogwistar-llm-wiki-representation`.
2. Create a Docker Hub access token with permission to push to those
   repositories.
3. In GitHub, open **Settings**, **Secrets and variables**, **Actions**, and
   add:
   - `DOCKERHUB_USERNAME`: the Docker Hub account name.
   - `DOCKERHUB_TOKEN`: the Docker Hub access token.

The workflow never stores either value in the repository or image labels.

## Publish A Release

From PowerShell:

```powershell
git tag v0.3.0
git push origin v0.3.0
```

The tag starts all three builds and publishes the release tags. The application
image receives `latest`; the CUDA representation image receives `latest` and
`cuda12.8`; the CPU image receives `cpu`.

For a deliberate non-release build, open the workflow in GitHub Actions, choose
**Run workflow**, and set `confirm` to `true`. Do not use this to publish an
untested branch over `latest`.

## Pull From Docker Hub

```powershell
docker pull <dockerhub-user>/kogwistar-llm-wiki:latest
docker pull <dockerhub-user>/kogwistar-llm-wiki-representation:latest
```

Use the images with the existing Compose files:

```powershell
$env:LLM_WIKI_IMAGE = '<dockerhub-user>/kogwistar-llm-wiki:latest'
$env:LLM_WIKI_REPRESENTATION_IMAGE = '<dockerhub-user>/kogwistar-llm-wiki-representation:latest'
$env:LLM_WIKI_REPRESENTATION_MODEL_REVISION = '<40-character-model-commit-sha>'
docker compose -f compose.yml -f compose.multimodal.yml -f compose.representation-cuda.yml up -d
```

For CPU:

```powershell
$env:LLM_WIKI_REPRESENTATION_IMAGE = '<dockerhub-user>/kogwistar-llm-wiki-representation:cpu'
docker compose -f compose.yml -f compose.multimodal.yml up -d
```

The Compose files still support local `build` when an image override is not
provided. Run `docker compose config --quiet` before starting and keep database,
application, and Hugging Face cache volumes separate from image lifecycle.
