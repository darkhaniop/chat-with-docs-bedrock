import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { CwdAuthStack } from "../lib/auth-stack";
import { CwdDataStack } from "../lib/data-stack";
import { CwdRealtimeStack } from "../lib/realtime-stack";

// `onSubscribeFunction` stages a Docker image asset (services/Dockerfile, same convention as
// compute-stack.test.ts) — build it once and share across every assertion in this file.
let template: Template;
let outdir: string;

beforeAll(() => {
  outdir = fs.mkdtempSync(path.join(os.tmpdir(), "cwd-realtime-stack-"));
  const app = new App({ outdir });
  const cdkEnv = { account: "123456789012", region: "us-east-1" };
  const dataStack = new CwdDataStack(app, "TestDataStack", {
    env2: "dev",
    env: cdkEnv,
    webDistributionDomainName: "d111111abcdef8.cloudfront.net",
  });
  const authStack = new CwdAuthStack(app, "TestAuthStack", {
    env2: "dev",
    env: cdkEnv,
    webDistributionDomainName: "d111111abcdef8.cloudfront.net",
  });
  const stack = new CwdRealtimeStack(app, "TestRealtimeStack", {
    env2: "dev",
    env: cdkEnv,
    userPool: authStack.userPool,
    table: dataStack.table,
  });
  template = Template.fromStack(stack);
});

afterAll(() => {
  fs.rmSync(outdir, { recursive: true, force: true });
});

describe("CwdRealtimeStack", () => {
  it("creates exactly two channel namespaces: projects and conversations", () => {
    template.resourceCountIs("AWS::AppSync::ChannelNamespace", 2);
    template.hasResourceProperties("AWS::AppSync::ChannelNamespace", { Name: "projects" });
    template.hasResourceProperties("AWS::AppSync::ChannelNamespace", { Name: "conversations" });
  });

  it("configures Cognito user pool auth for connect/subscribe and IAM-only for publish", () => {
    // docs/07-security.md#channel-authorization: "no Cognito principal has publish permission on
    // either namespace" — publish is IAM-only, subscribe/connect is the Cognito user pool.
    template.hasResourceProperties("AWS::AppSync::Api", {
      EventConfig: Match.objectLike({
        AuthProviders: Match.arrayWith([
          Match.objectLike({ AuthType: "AMAZON_COGNITO_USER_POOLS" }),
          Match.objectLike({ AuthType: "AWS_IAM" }),
        ]),
        ConnectionAuthModes: [{ AuthType: "AMAZON_COGNITO_USER_POOLS" }],
        DefaultPublishAuthModes: [{ AuthType: "AWS_IAM" }],
        DefaultSubscribeAuthModes: [{ AuthType: "AMAZON_COGNITO_USER_POOLS" }],
      }),
    });
  });

  it("grants no Cognito-reachable principal appsync:EventPublish", () => {
    // The only IAM identity with EventPublish in this stack should be `onSubscribeFunction`'s
    // own role reading/writing nothing publish-related — actually it shouldn't have publish at
    // all, since it's an authorizer, not a publisher. No policy in this stack should ever grant
    // `appsync:EventPublish` to anything; ingestion/answering/sweeper Lambdas that legitimately
    // publish live in `CwdComputeStack` and get the grant there via `eventsApi.grantPublish`,
    // never as a resource inside this stack.
    const policies = template.findResources("AWS::IAM::Policy");
    for (const policy of Object.values(policies)) {
      const statements = policy.Properties.PolicyDocument.Statement as Array<
        Record<string, unknown>
      >;
      for (const statement of statements) {
        expect(JSON.stringify(statement.Action ?? "")).not.toContain("appsync:EventPublish");
      }
    }
  });

  it("configures onSubscribe as a direct Lambda integration on both namespaces", () => {
    const namespaces = template.findResources("AWS::AppSync::ChannelNamespace");
    for (const namespace of Object.values(namespaces)) {
      const onSubscribe = namespace.Properties.HandlerConfigs.OnSubscribe;
      expect(onSubscribe.Behavior).toBe("DIRECT");
    }
  });

  it("gives the onSubscribe function read-only DynamoDB access, not full read/write", () => {
    const policies = template.findResources("AWS::IAM::Policy", {
      Properties: {
        PolicyName: Match.stringLikeRegexp("^OnSubscribeFunctionServiceRoleDefaultPolicy"),
      },
    });
    const statements = Object.values(policies).flatMap(
      (p) => p.Properties.PolicyDocument.Statement as Array<Record<string, unknown>>,
    );
    const actions = statements.flatMap((s) =>
      Array.isArray(s.Action) ? s.Action : [s.Action],
    ) as string[];
    expect(actions).not.toContain("dynamodb:PutItem");
    expect(actions).not.toContain("dynamodb:UpdateItem");
    expect(actions).not.toContain("dynamodb:DeleteItem");
    expect(actions.some((a) => a === "dynamodb:GetItem" || a === "dynamodb:Query")).toBe(true);
  });

  it("outputs the events HTTP and realtime domains", () => {
    template.hasOutput("EventsHttpDomain", {});
    template.hasOutput("EventsRealtimeDomain", {});
  });
});
