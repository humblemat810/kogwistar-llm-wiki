#!/usr/bin/env bash

set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: publish_docker_images.sh [options]

Build and push the LLM-Wiki application image and, unless skipped, the
isolated multimodal Embedding Service image.

Options:
  --docker-hub-user USER       Docker Hub namespace (default: DOCKERHUB_USERNAME)
  --tag TAG                    Image tag (default: latest)
  --embedding-backend B  cpu or cu128 (default: cu128; CUDA tags use -cuda12.8)
  --login                      Run docker login before building
  --skip-embedding        Do not build or push the Embedding Service image
  -h, --help                   Show this help

Examples:
  ./scripts/publish_docker_images.sh --docker-hub-user example --tag v0.3.0
  ./scripts/publish_docker_images.sh --embedding-backend cpu
  ./scripts/publish_docker_images.sh --skip-embedding
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

docker_args() {
  docker "$@" || die "docker $* failed"
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"

docker_hub_user="${DOCKERHUB_USERNAME:-}"
tag="latest"
embedding_backend="cu128"
login=false
skip_embedding=false

while (($# > 0)); do
  case "$1" in
    --docker-hub-user)
      (($# >= 2)) || die "--docker-hub-user requires a value"
      docker_hub_user="$2"
      shift 2
      ;;
    --tag)
      (($# >= 2)) || die "--tag requires a value"
      tag="$2"
      shift 2
      ;;
    --embedding-backend)
      (($# >= 2)) || die "--embedding-backend requires a value"
      embedding_backend="$2"
      shift 2
      ;;
    --login)
      login=true
      shift
      ;;
    --skip-embedding)
      skip_embedding=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1 (use --help for usage)"
      ;;
  esac
done

case "$embedding_backend" in
  cpu|cu128) ;;
  *) die "--embedding-backend must be cpu or cu128" ;;
esac

docker_args info

if [[ -z "$docker_hub_user" ]]; then
  docker_hub_user="$(docker info 2>/dev/null | awk -F: '/^[[:space:]]*Username:/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')"
fi

if [[ -z "$docker_hub_user" && -t 0 ]]; then
  read -r -p "Docker Hub username for the image namespace: " docker_hub_user
fi

[[ -n "$docker_hub_user" ]] || die "Docker Hub namespace is unknown. Pass --docker-hub-user or set DOCKERHUB_USERNAME."

if [[ "$login" == true ]]; then
  docker_args login
fi

app_image="${docker_hub_user}/kogwistar-llm-wiki:${tag}"
echo "Building ${app_image}"
(
  cd -- "$repo_root"
  docker_args build --tag "$app_image" --file Dockerfile .
)
echo "Pushing ${app_image}"
docker_args push "$app_image"

if [[ "$skip_embedding" != true ]]; then
  if [[ "$embedding_backend" == cpu && "$tag" == latest ]]; then
    embedding_tag="latest-cpu"
  elif [[ "$embedding_backend" == cpu ]]; then
    embedding_tag="${tag}-cpu"
  elif [[ "$tag" == latest ]]; then
    embedding_tag="latest-cuda12.8"
  else
    embedding_tag="${tag}-cuda12.8"
  fi

  embedding_image="${docker_hub_user}/kogwistar-llm-wiki-embedding:${embedding_tag}"
  echo "Building ${embedding_image} (${embedding_backend})"
  (
    cd -- "$repo_root"
    docker_args build \
      --build-arg "LLM_WIKI_EMBEDDING_TORCH_BACKEND=${embedding_backend}" \
      --tag "$embedding_image" \
      --file Dockerfile.embedding-service \
      .
  )
  echo "Pushing ${embedding_image}"
  docker_args push "$embedding_image"
fi

echo "Images published under Docker Hub namespace '${docker_hub_user}'."
