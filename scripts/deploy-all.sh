#!/usr/bin/env bash
# Runs the full deploy runbook in one command, so a bare `cdk deploy --all` can't accidentally
# ship a stale/placeholder web build.
#
# `web/dist` needs CwdAuthStack/CwdComputeStack's outputs baked in at build time, but those
# outputs don't exist until after a deploy — CDK stages every stack's assets before any of them
# actually apply, so this can't be collapsed into a single `cdk deploy --all` invocation. This
# script instead chains the three real steps: deploy everything (ships whatever's currently in
# web/dist, stale or placeholder), rebuild web/dist from the fresh outputs, then redeploy just
# the web stack with the corrected build.
#
# Usage: ./scripts/deploy-all.sh [env]   (defaults to "dev")
set -euo pipefail

ENV="${1:-dev}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUTS_FILE="$REPO_ROOT/infra/cdk-outputs.${ENV}.json"

env_title="$(tr '[:lower:]' '[:upper:]' <<<"${ENV:0:1}")${ENV:1}"
web_stack="Cwd${env_title}WebStack"
compute_stack="Cwd${env_title}ComputeStack"

echo "==> [1/4] cdk diff --all (review before deploying)"
(cd "$REPO_ROOT/infra" && npx cdk diff --all --context "env=${ENV}")

echo "==> [2/4] cdk deploy --all"
(cd "$REPO_ROOT/infra" && npx cdk deploy --all --context "env=${ENV}" --require-approval broadening \
  --outputs-file "cdk-outputs.${ENV}.json")

echo "==> [3/4] rebuild web/dist from fresh outputs, redeploy ${web_stack} alone"
"$REPO_ROOT/scripts/build-web.sh" "$ENV"

web_outputs_scratch="$(mktemp)"
trap 'rm -f "$web_outputs_scratch"' EXIT

(cd "$REPO_ROOT/infra" && npx cdk deploy "$web_stack" --context "env=${ENV}" \
  --require-approval broadening --outputs-file "$web_outputs_scratch")

jq -s '.[0] * .[1]' "$OUTPUTS_FILE" "$web_outputs_scratch" >"${OUTPUTS_FILE}.tmp"
mv "${OUTPUTS_FILE}.tmp" "$OUTPUTS_FILE"

echo "==> [4/4] smoke test"
api_base=$(jq -er --arg s "$compute_stack" '.[$s].ApiBaseUrl' "$OUTPUTS_FILE")
curl -sf "${api_base%/}/health" | jq .

echo "Done. ${OUTPUTS_FILE} is complete and up to date for all stacks."
