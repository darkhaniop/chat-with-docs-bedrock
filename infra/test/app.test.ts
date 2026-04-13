import * as path from "node:path";
import { App } from "aws-cdk-lib";
import { CwdAuthStack } from "../lib/auth-stack";
import { CwdComputeStack } from "../lib/compute-stack";
import { CwdDataStack } from "../lib/data-stack";
import { CwdRealtimeStack } from "../lib/realtime-stack";
import { CwdWebStack } from "../lib/web-stack";
import { stackName } from "../lib/naming";

// Mirrors bin/infra.ts's wiring so a cross-stack reference mistake (e.g. a stack reading a
// property before its producer assigns it) fails a fast offline test instead of only showing up
// in `cdk synth` against a real account.
describe("the full app", () => {
  it("wires Data, Web, Auth, and Compute together and synthesizes without throwing", () => {
    const app = new App();
    const env2 = "dev";
    const cdkEnv = { account: "123456789012", region: "us-east-1" };

    const siteContentDir = path.join(__dirname, "..", "assets", "web-placeholder");
    const webStack = new CwdWebStack(app, stackName(env2, "Web"), {
      env2,
      env: cdkEnv,
      siteContentDir,
    });
    const dataStack = new CwdDataStack(app, stackName(env2, "Data"), {
      env2,
      env: cdkEnv,
      webDistributionDomainName: webStack.distribution.domainName,
    });
    const authStack = new CwdAuthStack(app, stackName(env2, "Auth"), {
      env2,
      env: cdkEnv,
      webDistributionDomainName: webStack.distribution.domainName,
    });
    const realtimeStack = new CwdRealtimeStack(app, stackName(env2, "Realtime"), {
      env2,
      env: cdkEnv,
      userPool: authStack.userPool,
      table: dataStack.table,
    });
    new CwdComputeStack(app, stackName(env2, "Compute"), {
      env2,
      env: cdkEnv,
      userPoolClientId: authStack.userPoolClient.userPoolClientId,
      userPoolIssuer: `https://cognito-idp.${cdkEnv.region}.amazonaws.com/${authStack.userPool.userPoolId}`,
      webDistributionDomainName: webStack.distribution.domainName,
      table: dataStack.table,
      documentsBucket: dataStack.documentsBucket,
      vectorBucket: dataStack.vectorBucket,
      eventsApi: realtimeStack.eventApi,
    });

    expect(() => app.synth()).not.toThrow();
  });
});
