import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { CwdComputeStack } from "../lib/compute-stack";

// Building `CwdComputeStack` stages a Docker build-context asset (services/Dockerfile +
// repo root), which is expensive relative to a plain CFN synth. Build it exactly once and
// share the resulting template/assembly across every assertion in this file, rather than
// once per `it`.
let template: Template;
let outdir: string;

beforeAll(() => {
  outdir = fs.mkdtempSync(path.join(os.tmpdir(), "cwd-compute-stack-"));
  const app = new App({ outdir });
  const stack = new CwdComputeStack(app, "TestComputeStack", {
    env2: "dev",
    env: { account: "123456789012", region: "us-east-1" },
    userPoolClientId: "test-client-id",
    userPoolIssuer: "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_test",
    webDistributionDomainName: "d111111abcdef8.cloudfront.net",
  });
  template = Template.fromStack(stack);
});

describe("CwdComputeStack", () => {
  it("routes GET /health with no authorizer", () => {
    template.hasResourceProperties("AWS::ApiGatewayV2::Route", {
      RouteKey: "GET /health",
      AuthorizationType: "NONE",
    });
  });

  it("routes every other route through the JWT authorizer", () => {
    const routes = template.findResources("AWS::ApiGatewayV2::Route");
    const nonHealthRoutes = Object.values(routes).filter(
      (r) => r.Properties.RouteKey !== "GET /health",
    );
    expect(nonHealthRoutes.length).toBeGreaterThan(0);
    for (const route of nonHealthRoutes) {
      expect(route.Properties.AuthorizationType).toBe("JWT");
      expect(route.Properties.AuthorizerId).toBeDefined();
    }
  });

  it("configures the JWT authorizer against the user pool issuer and app client audience", () => {
    template.hasResourceProperties("AWS::ApiGatewayV2::Authorizer", {
      AuthorizerType: "JWT",
      JwtConfiguration: {
        Issuer: "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_test",
        Audience: ["test-client-id"],
      },
    });
  });

  it("gives the api function its own role with no Bedrock permissions at all", () => {
    const roles = template.findResources("AWS::IAM::Role");
    expect(Object.keys(roles)).toHaveLength(1);

    const policies = template.findResources("AWS::IAM::Policy");
    for (const policy of Object.values(policies)) {
      const statements = JSON.stringify(policy.Properties.PolicyDocument.Statement);
      expect(statements).not.toMatch(/bedrock:/);
    }
  });

  it("has no wildcard-resource IAM statement anywhere in the stack", () => {
    const policies = template.findResources("AWS::IAM::Policy");
    for (const policy of Object.values(policies)) {
      for (const statement of policy.Properties.PolicyDocument.Statement) {
        if (statement.Effect !== "Allow") continue;
        const resources = ([] as unknown[]).concat(statement.Resource ?? []);
        expect(resources).not.toContain("*");
      }
    }
  });

  it("sets 30-day log retention on the api function's log group", () => {
    template.hasResourceProperties("AWS::Logs::LogGroup", {
      RetentionInDays: 30,
    });
  });

  it("builds the api function image from services/Dockerfile with SERVICE=api, x86_64 only", () => {
    const assets = JSON.parse(
      fs.readFileSync(path.join(outdir, "TestComputeStack.assets.json"), "utf8"),
    ) as { dockerImages: Record<string, { source: Record<string, unknown> }> };
    const images = Object.values(assets.dockerImages);
    expect(images).toHaveLength(1);
    const [image] = images;
    expect(image?.source).toMatchObject({
      dockerFile: "services/Dockerfile",
      dockerBuildArgs: { SERVICE: "api" },
      platform: "linux/amd64",
    });
  });

  it("allows CORS from the CloudFront origin and localhost dev, with Authorization allowed", () => {
    template.hasResourceProperties("AWS::ApiGatewayV2::Api", {
      CorsConfiguration: {
        AllowOrigins: Match.arrayWith([
          "https://d111111abcdef8.cloudfront.net",
          "http://localhost:5173",
        ]),
        AllowHeaders: Match.arrayWith(["Authorization"]),
      },
    });
  });

  it("sets CWD_COMMIT to the current git commit for /health drift detection (ADR-008)", () => {
    template.hasResourceProperties("AWS::Lambda::Function", {
      Environment: {
        Variables: {
          CWD_COMMIT: Match.stringLikeRegexp("^[0-9a-f]{40}$"),
        },
      },
    });
  });

  it("matches the committed template snapshot", () => {
    // CWD_COMMIT is `git rev-parse HEAD` at synth time (compute-stack.ts) — it changes on
    // every commit by design (ADR-008), so it's redacted here rather than asserted verbatim;
    // its shape is covered by the dedicated test above instead.
    const redacted = JSON.stringify(template.toJSON()).replace(
      /"CWD_COMMIT":"[0-9a-f]{40}"/g,
      '"CWD_COMMIT":"<commit-sha>"',
    );
    expect(JSON.parse(redacted)).toMatchSnapshot();
  });
});
