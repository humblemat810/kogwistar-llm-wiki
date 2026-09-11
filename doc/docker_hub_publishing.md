# Docker Hub Publishing

The repository can publish three public images through
`.github/workflows/publish-dockerhub.yml`:

| Image | Tags | Purpose |
| --- | --- | --- |
| `kogwistar-llm-wiki` | `latest`, release version | Torch-free REST/MCP application |
| `kogwistar-llm-wiki-embedding` | `latest-cuda12.8`, `cuda12.8`, release version with `-cuda12.8` | GPU-default Qwen3-VL Embedding Service |
| `kogwistar-llm-wiki-embedding` | `cpu`, release version with `-cpu` | CPU fallback and smoke tests |

The Embedding Service images contain the inference runtime but do not contain the
Qwen checkpoint. Mount or configure the Hugging Face cache and set an immutable
`LLM_WIKI_EMBEDDING_MODEL_REVISION` before starting the service.

## One-Time Docker Hub Setup

1. Create two **public** Docker Hub repositories under the intended account:
   `kogwistar-llm-wiki` and `kogwistar-llm-wiki-embedding`.
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
image receives `latest`; the CUDA embedding image receives `latest-cuda12.8`
and `cuda12.8`; the CPU image receives `cpu`.

For a deliberate non-release build, open the workflow in GitHub Actions, choose
**Run workflow**, and set `confirm` to `true`. Do not use this to publish an
untested branch over `latest`.

## Pull From Docker Hub

```powershell
docker pull <dockerhub-user>/kogwistar-llm-wiki:latest
docker pull <dockerhub-user>/kogwistar-llm-wiki-embedding:latest-cuda12.8
```

Use the images with the existing Compose files:

```powershell
$env:LLM_WIKI_IMAGE = '<dockerhub-user>/kogwistar-llm-wiki:latest'
$env:LLM_WIKI_EMBEDDING_IMAGE = '<dockerhub-user>/kogwistar-llm-wiki-embedding:latest-cuda12.8'
$env:LLM_WIKI_EMBEDDING_MODEL_REVISION = '<40-character-model-commit-sha>'
docker compose -f compose.yml -f compose.multimodal.yml -f compose.embedding-cuda.yml up -d
```

For CPU:

```powershell
$env:LLM_WIKI_EMBEDDING_IMAGE = '<dockerhub-user>/kogwistar-llm-wiki-embedding:cpu'
docker compose -f compose.yml -f compose.multimodal.yml up -d
```

The Compose files still support local `build` when an image override is not
provided. Run `docker compose config --quiet` before starting and keep database,
application, and Hugging Face cache volumes separate from image lifecycle.

## Local PowerShell Publisher

Docker Desktop authentication is sufficient; the Docker CLI stores the login in
its configured credential helper. The publisher only needs the Docker Hub
namespace and does not read or print a password or token. If Docker Desktop
reports the account from `docker info`, the environment variable can be
omitted and the script will discover it or prompt for it:

```powershell
docker login
.\scripts\publish_docker_images.ps1 -Tag v0.3.0
```

For a non-interactive shell or CI, set the namespace explicitly:

```powershell
$env:DOCKERHUB_USERNAME = '<dockerhub-user>'
.\scripts\publish_docker_images.ps1 -Tag v0.3.0
```

Use `-Login` to run the interactive login from the script, or use
`-EmbeddingBackend cpu` for the CPU image:

```powershell
.\scripts\publish_docker_images.ps1 -Tag v0.3.0 -EmbeddingBackend cpu
```

For CI, use `DOCKERHUB_TOKEN` with `docker login --password-stdin` rather than
putting a token in a script or command history.
