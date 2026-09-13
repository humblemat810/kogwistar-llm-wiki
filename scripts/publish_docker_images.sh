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
  --allow-existing-tag         Allow updating an existing tag (unsafe; explicit)
  --skip-embedding             Compatibility alias for --target app
  -h, --help                   Show this help

Examples:
  ./scripts/publish_docker_images.sh --docker-hub-user profchan --tag v0.3.4
  ./scripts/publish_docker_images.sh --target embedding-cuda12.8 --tag v0.3.4
  ./scripts/publish_docker_images.sh --target all --tag v0.3.4
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
allow_existing_tag=false

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
    --allow-existing-tag)
      allow_existing_tag=true
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

python_cmd="python3"
command -v "$python_cmd" >/dev/null 2>&1 || python_cmd="python"
"$python_cmd" "$repo_root/scripts/verify_release_version.py" --tag "$tag" || die "release version verification failed"

remote_tag_exists() {
  local image="$1"
  local output
  local status
  set +e
  output="$(docker buildx imagetools inspect "$image" 2>&1)"
  status=$?
  set -e
  if ((status == 0)); then
    return 0
  fi
  if grep -Eiq 'no such manifest|manifest unknown|not found' <<<"$output"; then
    return 1
  fi
  die "could not verify whether Docker tag exists: $image\n$output"
}

publish_images=()
if [[ "$target" == app || "$target" == all ]]; then
  publish_images+=("${docker_hub_user}/kogwistar-llm-wiki:${tag}")
fi
if [[ "$target" == embedding-cpu || "$target" == all ]]; then
  embedding_tag="latest-cpu"
  [[ "$tag" == latest ]] || embedding_tag="${tag}-cpu"
  publish_images+=("${docker_hub_user}/kogwistar-llm-wiki-embedding:${embedding_tag}")
fi
if [[ "$target" == embedding-cuda12.8 || "$target" == all ]]; then
  embedding_tag="latest-cuda12.8"
  [[ "$tag" == latest ]] || embedding_tag="${tag}-cuda12.8"
  publish_images+=("${docker_hub_user}/kogwistar-llm-wiki-embedding:${embedding_tag}")
fi
if [[ "$allow_existing_tag" == false ]]; then
  for image in "${publish_images[@]}"; do
    if remote_tag_exists "$image"; then
      die "Docker tag already exists: $image; choose a new package release or pass --allow-existing-tag explicitly"
    fi
  done
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
