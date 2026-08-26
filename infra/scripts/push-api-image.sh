#!/usr/bin/env bash
# Build packages/api/Dockerfile for linux/arm64, push to ECR, update the search
# Lambda if it already exists. Does not run terraform apply.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
INFRA="$ROOT/infra"

: "${AWS_PROFILE:?AWS_PROFILE is required}"
: "${AWS_REGION:?AWS_REGION is required}"

TAG="${1:-${API_IMAGE_TAG:-latest}}"

if [[ -z "${REPOSITORY_URL:-}" ]]; then
  REPOSITORY_URL="$(terraform -chdir="$INFRA" output -raw repository_url)"
fi

IMAGE_URI="${REPOSITORY_URL}:${TAG}"
REGISTRY="${REPOSITORY_URL%%/*}"

aws ecr get-login-password --region "$AWS_REGION" --profile "$AWS_PROFILE" \
  | docker login --username AWS --password-stdin "$REGISTRY"

docker build --platform linux/arm64 \
  -t "$IMAGE_URI" \
  -f "$ROOT/packages/api/Dockerfile" \
  "$ROOT/packages/api"

docker push "$IMAGE_URI"

FUNCTION_NAME="${FUNCTION_NAME:-}"
if [[ -z "$FUNCTION_NAME" ]]; then
  FUNCTION_NAME="$(terraform -chdir="$INFRA" output -raw search_function_name 2>/dev/null || true)"
fi

if [[ -n "$FUNCTION_NAME" ]] \
  && aws lambda get-function \
    --function-name "$FUNCTION_NAME" \
    --region "$AWS_REGION" \
    --profile "$AWS_PROFILE" >/dev/null 2>&1; then
  aws lambda update-function-code \
    --function-name "$FUNCTION_NAME" \
    --image-uri "$IMAGE_URI" \
    --region "$AWS_REGION" \
    --profile "$AWS_PROFILE"
else
  echo "Lambda not in AWS yet; skipped update-function-code. Full terraform apply will CreateFunction from ${IMAGE_URI}."
fi
