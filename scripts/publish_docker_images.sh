#!/usr/bin/env bash

set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: publish_docker_images.sh [options]

Build and push one explicit image target. Application releases do not rebuild
the optional CPU/CUDA embedding images unless requested.

Options:
  --docker-hub-user USER       Docker Hub namespace (default: DOCKERHUB_USERNAME)
  --tag TAG                    Image tag (default: latest)
  --target TARGET              app, embedding-cpu, embedding-cuda12.8, or all
  --login                      Run docker login before building
  --skip-embedding             Compatibility alias for --target app
  -h, --help                   Show this help

Examples:
  ./scripts/publish_docker_images.sh --docker-hub-user profchan --tag v0.3.3
  ./scripts/publish_docker_images.sh --target embedding-cuda12.8 --tag v0.3.3
  ./scripts/publish_docker_images.sh --target all --tag v0.3.3
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

docker_args() {
  docker "$@" || die "docker $* failed"
}

build_and_push_app() {
  local app_image="${docker_hub_user}/kogwistar-llm-wiki:${tag}"
  echo "Building ${app_image}"
  (
    cd -- "$repo_root"
    docker_args buildx build --load --tag "$app_image" --file Dockerfile .
  )
  echo "Pushing ${app_image}"
  docker_args push "$app_image"
}

build_and_push_embedding() {
  local backend="$1"
  local torch_backend="$2"
  local image_tag
  if [[ "$tag" == latest ]]; then
    image_tag="latest-${backend}"
  else
    image_tag="${tag}-${backend}"
  fi
  local embedding_image="${docker_hub_user}/kogwistar-llm-wiki-embedding:${image_tag}"
  echo "Building ${embedding_image} (${torch_backend})"
  (
    cd -- "$repo_root"
    docker_args buildx build --load \
      --build-arg "LLM_WIKI_EMBEDDING_TORCH_BACKEND=${torch_backend}" \
      --tag "$embedding_image" \
      --file Dockerfile.embedding-service \
      .
  )
  echo "Pushing ${embedding_image}"
  docker_args push "$embedding_image"
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"

docker_hub_user="${DOCKERHUB_USERNAME:-}"
tag="latest"
target="app"
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
    --target)
      (($# >= 2)) || die "--target requires a value"
      target="$2"
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

if [[ "$skip_embedding" == true ]]; then
  [[ "$target" == app ]] || die "--skip-embedding is only compatible with the default --target app"
  target=app
fi

case "$target" in
  app|embedding-cpu|embedding-cuda12.8|all) ;;
  *) die "--target must be app, embedding-cpu, embedding-cuda12.8, or all" ;;
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

case "$target" in
  app) build_and_push_app ;;
  embedding-cpu) build_and_push_embedding cpu cpu ;;
  embedding-cuda12.8) build_and_push_embedding cuda12.8 cu128 ;;
  all)
    build_and_push_app
    build_and_push_embedding cpu cpu
    build_and_push_embedding cuda12.8 cu128
    ;;
esac

echo "Images published under Docker Hub namespace '${docker_hub_user}' for target '${target}'."
