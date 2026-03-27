/**
 * Single source of truth for every stack and resource name, so the `-c env=` parameterisation
 * (docs/09-operations.md#environments) cannot drift between stacks. Every physical name that
 * appears in docs/02-data-model.md and docs/09-operations.md is produced by a function here —
 * nothing else in `infra/` should string-template a `cwd-...` name.
 */

export type StackPurpose = "Data" | "Auth" | "Compute" | "Realtime" | "Web";

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/** `Cwd{Env}{Purpose}Stack`, e.g. `CwdDevDataStack`. */
export function stackName(env: string, purpose: StackPurpose): string {
  return `Cwd${capitalize(env)}${purpose}Stack`;
}

/** `cwd-{env}` — the single DynamoDB table (docs/02-data-model.md#dynamodb-single-table). */
export function tableName(env: string): string {
  return `cwd-${env}`;
}

/** `cwd-documents-{env}-{account}` (docs/02-data-model.md#s3-layout). */
export function documentsBucketName(env: string, account: string): string {
  return `cwd-documents-${env}-${account}`;
}

/** `cwd-vectors-{env}-{account}` (docs/02-data-model.md#s3-vectors). */
export function vectorBucketName(env: string, account: string): string {
  return `cwd-vectors-${env}-${account}`;
}

/** `cwd-site-{env}-{account}` — the private static site bucket behind CloudFront. */
export function siteBucketName(env: string, account: string): string {
  return `cwd-site-${env}-${account}`;
}

/** `proj-{projectId}` (docs/02-data-model.md#s3-vectors) — for reference; created at runtime. */
export function vectorIndexName(projectId: string): string {
  return `proj-${projectId}`;
}

/** Cognito Managed Login domain prefix. Must be globally unique per region. */
export function cognitoDomainPrefix(env: string, account: string): string {
  return `cwd-${env}-${account}`;
}

/** `cwd-{env}` — CloudWatch dashboard name (docs/09-operations.md#dashboard). */
export function dashboardName(env: string): string {
  return `cwd-${env}`;
}
