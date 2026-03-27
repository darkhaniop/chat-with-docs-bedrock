#!/usr/bin/env node
import * as fs from "node:fs";
import * as path from "node:path";
import "source-map-support/register";
import { App } from "aws-cdk-lib";
import { CwdAuthStack } from "../lib/auth-stack";
import { CwdComputeStack } from "../lib/compute-stack";
import { CwdDataStack } from "../lib/data-stack";
import { CwdWebStack } from "../lib/web-stack";
import { stackName } from "../lib/naming";

const app = new App();
const env2 = (app.node.tryGetContext("env") as string | undefined) ?? "dev";

// docs/06-frontend.md#build-and-deploy: `web/dist` is built by `scripts/build-web.sh`, which
// itself needs CwdWebStack's outputs first — the deploy runbook
// (docs/09-operations.md#deployment) resolves the chicken-and-egg by deploying once with the
// placeholder, building against the fresh outputs, then redeploying CwdWebStack alone.
const webDist = path.join(__dirname, "..", "..", "web", "dist");
const siteContentDir = fs.existsSync(webDist)
  ? webDist
  : path.join(__dirname, "..", "assets", "web-placeholder");

// Environment-specific (not environment-agnostic): physical resource names bake in the account
// id (docs/02-data-model.md#s3-layout), so account/region must be concrete at synth time. The
// CDK CLI populates these from the caller's resolved AWS profile.
const account = process.env.CDK_DEFAULT_ACCOUNT;
const region = process.env.CDK_DEFAULT_REGION ?? "us-east-1";
const cdkEnv = account !== undefined ? { account, region } : { region };

new CwdDataStack(app, stackName(env2, "Data"), { env2, env: cdkEnv });

const webStack = new CwdWebStack(app, stackName(env2, "Web"), {
  env2,
  env: cdkEnv,
  siteContentDir,
});

const authStack = new CwdAuthStack(app, stackName(env2, "Auth"), {
  env2,
  env: cdkEnv,
  webDistributionDomainName: webStack.distribution.domainName,
});

new CwdComputeStack(app, stackName(env2, "Compute"), {
  env2,
  env: cdkEnv,
  userPoolClientId: authStack.userPoolClient.userPoolClientId,
  userPoolIssuer: `https://cognito-idp.${cdkEnv.region}.amazonaws.com/${authStack.userPool.userPoolId}`,
});
