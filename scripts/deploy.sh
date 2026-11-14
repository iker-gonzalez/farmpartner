#!/usr/bin/env bash
# Build the image, push it to Artifact Registry, and deploy it with Terraform (terraform/ owns the
# Cloud Run service, its service account, secrets and IAM).
#   ./scripts/deploy.sh            # build + push + terraform apply
#   PLAN_ONLY=1 ./scripts/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT=${PROJECT:-launchpad-all-dev-01}
REGION=${REGION:-europe-west1}
REPO=${REPO:-launchpad-all-docker-ar-dev-01}
TAG=$(git rev-parse --short HEAD)$(git diff --quiet || echo "-dirty")-$(date +%Y%m%d%H%M)
IMG=${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/farmpartner:${TAG}

docker build --platform linux/amd64 -t "$IMG" .
docker push "$IMG"

# Terraform's Google provider and GCS backend accept a gcloud access token when there are no ADC.
if ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
  export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token)
fi
cd terraform
terraform init -input=false >/dev/null
if [ -n "${PLAN_ONLY:-}" ]; then
  terraform plan -input=false -var "image=$IMG"
else
  terraform apply -input=false -auto-approve -var "image=$IMG"
fi
