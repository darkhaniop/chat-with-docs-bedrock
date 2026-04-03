import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { CwdComputeStack } from "../lib/compute-stack";
import { CwdDataStack } from "../lib/data-stack";

// Building `CwdComputeStack` stages a Docker build-context asset (services/Dockerfile +
// repo root), which is expensive relative to a plain CFN synth. Build it exactly once and
// share the resulting template/assembly across every assertion in this file, rather than
// once per `it`.
let template: Template;
let outdir: string;

beforeAll(() => {
  outdir = fs.mkdtempSync(path.join(os.tmpdir(), "cwd-compute-stack-"));
  const app = new App({ outdir });
  const cdkEnv = { account: "123456789012", region: "us-east-1" };
  const dataStack = new CwdDataStack(app, "TestDataStack", {
    env2: "dev",
    env: cdkEnv,
    webDistributionDomainName: "d111111abcdef8.cloudfront.net",
  });
  const stack = new CwdComputeStack(app, "TestComputeStack", {
    env2: "dev",
    env: cdkEnv,
    userPoolClientId: "test-client-id",
    userPoolIssuer: "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_test",
    webDistributionDomainName: "d111111abcdef8.cloudfront.net",
    table: dataStack.table,
    documentsBucket: dataStack.documentsBucket,
  });
  template = Template.fromStack(stack);
});

// Each run stages the Docker build-context asset (the whole repo root) into `outdir` — left
// uncleaned, repeated local `npm test` runs accumulate gigabytes in the OS temp dir until it
// fills the disk (found the hard way: ENOSPC after ~450 accumulated runs in one session).
afterAll(() => {
  fs.rmSync(outdir, { recursive: true, force: true });
});

describe("CwdComputeStack", () => {
  it("routes GET /health with no authorizer", () => {
    template.hasResourceProperties("AWS::ApiGatewayV2::Route", {
      RouteKey: "GET /health",
      AuthorizationType: "NONE",
    });
  });

  it("routes every documented project/document route through the JWT authorizer", () => {
    const routes = template.findResources("AWS::ApiGatewayV2::Route");
    const nonHealthRoutes = Object.values(routes).filter(
      (r) => r.Properties.RouteKey !== "GET /health",
    );
    expect(nonHealthRoutes).toHaveLength(11);
    for (const route of nonHealthRoutes) {
      expect(route.Properties.AuthorizationType).toBe("JWT");
      expect(route.Properties.AuthorizerId).toBeDefined();
    }
    const routeKeys = nonHealthRoutes.map((r) => r.Properties.RouteKey).sort();
    expect(routeKeys).toEqual(
      [
        "DELETE /projects/{projectId}",
        "DELETE /projects/{projectId}/documents/{documentId}",
        "GET /projects",
        "GET /projects/{projectId}",
        "GET /projects/{projectId}/documents",
        "GET /projects/{projectId}/documents/{documentId}",
        "GET /projects/{projectId}/documents/{documentId}/pages/{page}/render-url",
        "GET /projects/{projectId}/documents/{documentId}/source-url",
        "PATCH /projects/{projectId}",
        "POST /projects",
        "POST /projects/{projectId}/documents",
        "POST /projects/{projectId}/documents/{documentId}/ingest",
      ].sort(),
    );
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

  it("gives every Lambda function (api, sweeper, and the five ingestion functions) its own distinct role", () => {
    const roles = template.findResources("AWS::IAM::Role");
    expect(Object.keys(roles)).toHaveLength(8);
  });

  it("no role has any Bedrock permission at all", () => {
    const policies = template.findResources("AWS::IAM::Policy");
    for (const policy of Object.values(policies)) {
      const statements = JSON.stringify(policy.Properties.PolicyDocument.Statement);
      expect(statements).not.toMatch(/bedrock:/);
    }
  });

  it("has no wildcard-resource IAM statement anywhere in the stack, except Textract's DetectDocumentText (which AWS gives no resource-level permissions for)", () => {
    const policies = template.findResources("AWS::IAM::Policy");
    for (const [name, policy] of Object.entries(policies)) {
      for (const statement of policy.Properties.PolicyDocument.Statement) {
        if (statement.Effect !== "Allow") continue;
        const resources = ([] as unknown[]).concat(statement.Resource ?? []);
        if (resources.includes("*")) {
          const actions = ([] as unknown[]).concat(statement.Action ?? []);
          expect(actions).toEqual(["textract:DetectDocumentText"]);
          expect(name).toContain("ingestpage");
        }
      }
    }
  });

  it("scopes the api function's S3 access to raw/* and pages/* only, never artifacts/*", () => {
    const apiPolicy = template.findResources("AWS::IAM::Policy", {
      Properties: { PolicyName: Match.stringLikeRegexp("^ApiFunctionServiceRoleDefaultPolicy") },
    });
    const statements = Object.values(apiPolicy).flatMap(
      (p) => p.Properties.PolicyDocument.Statement as Array<Record<string, unknown>>,
    );
    const s3Statements = statements.filter((s) => JSON.stringify(s.Action ?? "").includes("s3:"));
    expect(s3Statements.length).toBeGreaterThan(0);
    for (const statement of s3Statements) {
      const resources = JSON.stringify(statement.Resource);
      expect(resources.includes("artifacts")).toBe(false);
    }
  });

  it("grants the ingest-page function read/write on artifacts/* (blocks) in addition to pages/*", () => {
    const pagePolicy = template.findResources("AWS::IAM::Policy", {
      Properties: {
        PolicyName: Match.stringLikeRegexp("^IngestionPipelineingestpageFunctionServiceRoleDefaultPolicy"),
      },
    });
    const statements = Object.values(pagePolicy).flatMap(
      (p) => p.Properties.PolicyDocument.Statement as Array<Record<string, unknown>>,
    );
    const s3Statements = statements.filter((s) => JSON.stringify(s.Action ?? "").includes("s3:"));
    const resources = JSON.stringify(s3Statements.map((s) => s.Resource));
    expect(resources).toContain("artifacts");
    expect(resources).toContain("pages");
  });

  it("grants the api function states:StartExecution scoped to the ingestion state machine only", () => {
    const apiPolicy = template.findResources("AWS::IAM::Policy", {
      Properties: { PolicyName: Match.stringLikeRegexp("^ApiFunctionServiceRoleDefaultPolicy") },
    });
    const statements = Object.values(apiPolicy).flatMap(
      (p) => p.Properties.PolicyDocument.Statement as Array<Record<string, unknown>>,
    );
    const startExecutionStatements = statements.filter((s) =>
      JSON.stringify(s.Action ?? "").includes("states:StartExecution"),
    );
    expect(startExecutionStatements).toHaveLength(1);
    const resources = JSON.stringify(startExecutionStatements[0]?.Resource);
    expect(resources).not.toBe('"*"');
    expect(resources).toContain("StateMachine");
  });

  it("sets 30-day log retention on both function log groups", () => {
    const logGroups = template.findResources("AWS::Logs::LogGroup");
    expect(Object.keys(logGroups).length).toBeGreaterThanOrEqual(2);
    for (const logGroup of Object.values(logGroups)) {
      expect(logGroup.Properties.RetentionInDays).toBe(30);
    }
  });

  it("builds exactly two images from services/Dockerfile (SERVICE=api, SERVICE=ingestion), x86_64 only", () => {
    const assets = JSON.parse(
      fs.readFileSync(path.join(outdir, "TestComputeStack.assets.json"), "utf8"),
    ) as { dockerImages: Record<string, { source: Record<string, unknown> }> };
    const images = Object.values(assets.dockerImages);
    expect(images.length).toBe(2);
    const services = images
      .map(
        (image) =>
          (image.source as { dockerBuildArgs: { SERVICE: string } }).dockerBuildArgs.SERVICE,
      )
      .sort();
    expect(services).toEqual(["api", "ingestion"]);
    for (const image of images) {
      expect(image.source).toMatchObject({
        dockerFile: "services/Dockerfile",
        platform: "linux/amd64",
      });
    }
  });

  it("gives each of the five ingestion Lambdas its own distinct command over the shared image", () => {
    const fns = template.findResources("AWS::Lambda::Function");
    const ingestionCommands = Object.values(fns)
      .map((fn) => fn.Properties.ImageConfig?.Command?.[0])
      .filter((cmd): cmd is string => typeof cmd === "string" && cmd.startsWith("ingestion."));
    expect(new Set(ingestionCommands).size).toBe(5);
    expect(ingestionCommands.sort()).toEqual(
      [
        "ingestion.handlers.probe_handler",
        "ingestion.handlers.page_handler",
        "ingestion.handlers.chunk_handler",
        "ingestion.handlers.finalize_handler",
        "ingestion.handlers.mark_failed_handler",
      ].sort(),
    );
  });

  it("schedules the sweeper to run once a day", () => {
    template.hasResourceProperties("AWS::Events::Rule", {
      ScheduleExpression: "rate(1 day)",
    });
  });

  it("allows CORS from the CloudFront origin and localhost dev, with Authorization allowed", () => {
    template.hasResourceProperties("AWS::ApiGatewayV2::Api", {
      CorsConfiguration: {
        AllowOrigins: Match.arrayWith([
          "https://d111111abcdef8.cloudfront.net",
          "http://localhost:5173",
        ]),
        AllowMethods: Match.arrayWith(["GET", "POST", "PATCH", "DELETE"]),
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
