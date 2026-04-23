import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { App, Stack } from "aws-cdk-lib";
import * as appsync from "aws-cdk-lib/aws-appsync";
import { Match, Template } from "aws-cdk-lib/assertions";
import { CwdComputeStack } from "../lib/compute-stack";
import { CwdDataStack } from "../lib/data-stack";

/**
 * A minimal standalone `EventApi`. This file only needs *something* satisfying `IEventApi` for
 * `CwdComputeStack`'s cross-stack `grantPublish`/`httpDns` references to resolve.
 */
function testEventsApi(app: App, cdkEnv: { account: string; region: string }): appsync.IEventApi {
  const eventsStack = new Stack(app, "TestEventsStack", { env: cdkEnv });
  return new appsync.EventApi(eventsStack, "EventApi", {
    apiName: "test-events",
    authorizationConfig: {
      authProviders: [{ authorizationType: appsync.AppSyncAuthorizationType.IAM }],
      defaultPublishAuthModeTypes: [appsync.AppSyncAuthorizationType.IAM],
    },
  });
}

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
    vectorBucket: dataStack.vectorBucket,
    eventsApi: testEventsApi(app, cdkEnv),
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

  it("routes every documented project/document/conversation/message route through the JWT authorizer", () => {
    const routes = template.findResources("AWS::ApiGatewayV2::Route");
    const nonHealthRoutes = Object.values(routes).filter(
      (r) => r.Properties.RouteKey !== "GET /health",
    );
    expect(nonHealthRoutes).toHaveLength(21);
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
        "GET /projects/{projectId}/documents/{documentId}/pages/{page}",
        "GET /projects/{projectId}/documents/{documentId}/pages/{page}/render-url",
        "GET /projects/{projectId}/documents/{documentId}/source-url",
        "PATCH /projects/{projectId}",
        "POST /projects",
        "POST /projects/{projectId}/documents",
        "POST /projects/{projectId}/documents/{documentId}/ingest",
        "POST /projects/{projectId}/conversations",
        "GET /projects/{projectId}/conversations",
        "GET /conversations/{conversationId}",
        "PATCH /conversations/{conversationId}",
        "DELETE /conversations/{conversationId}",
        "GET /conversations/{conversationId}/messages",
        "POST /conversations/{conversationId}/messages",
        "POST /conversations/{conversationId}/messages/{messageId}/cancel",
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

  it("gives every Lambda function its own distinct role", () => {
    const roles = template.findResources("AWS::IAM::Role");
    expect(Object.keys(roles)).toHaveLength(13);
  });

  it("only ingest-embed-and-index and answering have Bedrock permission, never wildcarded", () => {
    const policies = template.findResources("AWS::IAM::Policy");
    for (const [name, policy] of Object.entries(policies)) {
      const statements = policy.Properties.PolicyDocument.Statement as Array<
        Record<string, unknown>
      >;
      const bedrockStatements = statements.filter((s) =>
        JSON.stringify(s.Action ?? "").includes("bedrock:"),
      );
      if (bedrockStatements.length === 0) continue;
      expect(name).toMatch(/ingestembedandindex|AnsweringFunction/);
      for (const statement of bedrockStatements) {
        const resources = ([] as unknown[]).concat(statement.Resource ?? []);
        expect(resources).not.toContain("*");
        const actions = ([] as unknown[]).concat(statement.Action ?? []).sort();
        if (name.startsWith("AnsweringFunction")) {
          const joined = JSON.stringify(resources);
          if (actions.length > 1) {
            expect(actions).toEqual(
              ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"].sort(),
            );
            expect(joined).toContain("inference-profile/us.anthropic.claude-sonnet-4-6");
            expect(joined).toContain(
              "inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0",
            );
            expect(joined).toContain("foundation-model/anthropic.claude-sonnet-4-6");
          } else {
            expect(actions).toEqual(["bedrock:InvokeModel"]);
            expect(joined).toContain(
              "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-2-multimodal-embeddings-v1:0",
            );
          }
        } else {
          expect(statement.Action).toBe("bedrock:InvokeModel");
          expect(JSON.stringify(resources)).toContain(
            "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-2-multimodal-embeddings-v1:0",
          );
        }
      }
    }
  });

  it("grants the answering function bedrock:InvokeModel on the Nova embeddings model, not just Sonnet/Haiku", () => {
    const policies = template.findResources("AWS::IAM::Policy", {
      Properties: {
        PolicyName: Match.stringLikeRegexp("^AnsweringFunctionServiceRoleDefaultPolicy"),
      },
    });
    const statements = Object.values(policies).flatMap(
      (p) => p.Properties.PolicyDocument.Statement as Array<Record<string, unknown>>,
    );
    const novaStatements = statements.filter((s) =>
      JSON.stringify(s.Resource ?? "").includes(
        "foundation-model/amazon.nova-2-multimodal-embeddings-v1:0",
      ),
    );
    expect(novaStatements).toHaveLength(1);
    expect(novaStatements[0]?.Action).toBe("bedrock:InvokeModel");
  });

  it("grants the api function sqs:SendMessage on the answer queue, never lambda:InvokeFunction on answering", () => {
    const apiPolicy = template.findResources("AWS::IAM::Policy", {
      Properties: { PolicyName: Match.stringLikeRegexp("^ApiFunctionServiceRoleDefaultPolicy") },
    });
    const statements = Object.values(apiPolicy).flatMap(
      (p) => p.Properties.PolicyDocument.Statement as Array<Record<string, unknown>>,
    );
    const invokeStatements = statements.filter((s) =>
      JSON.stringify(s.Action ?? "").includes("lambda:InvokeFunction"),
    );
    expect(invokeStatements).toHaveLength(0);
    const sendMessageStatements = statements.filter((s) =>
      JSON.stringify(s.Action ?? "").includes("sqs:SendMessage"),
    );
    expect(sendMessageStatements).toHaveLength(1);
    const resources = JSON.stringify(sendMessageStatements[0]?.Resource);
    expect(resources).not.toBe('"*"');
    expect(resources).toContain("AnswerQueue");
  });

  it("scopes the answering function's s3vectors grant to QueryVectors+GetVectors only, never wildcarded", () => {
    const policies = template.findResources("AWS::IAM::Policy", {
      Properties: {
        PolicyName: Match.stringLikeRegexp("^AnsweringFunctionServiceRoleDefaultPolicy"),
      },
    });
    const statements = Object.values(policies).flatMap(
      (p) => p.Properties.PolicyDocument.Statement as Array<Record<string, unknown>>,
    );
    const s3vectorsStatements = statements.filter((s) =>
      JSON.stringify(s.Action ?? "").includes("s3vectors:"),
    );
    expect(s3vectorsStatements).toHaveLength(1);
    expect(([] as unknown[]).concat(s3vectorsStatements[0]?.Action ?? []).sort()).toEqual(
      ["s3vectors:GetVectors", "s3vectors:QueryVectors"].sort(),
    );
    const resources = JSON.stringify(s3vectorsStatements[0]?.Resource);
    expect(resources).not.toBe('"*"');
    expect(resources).toContain("VectorBucket");
  });

  it("scopes every s3vectors grant to the vector bucket, never wildcarded, and api never gets more than delete", () => {
    const policies = template.findResources("AWS::IAM::Policy");
    for (const [name, policy] of Object.entries(policies)) {
      const statements = policy.Properties.PolicyDocument.Statement as Array<
        Record<string, unknown>
      >;
      for (const statement of statements) {
        const actions = ([] as unknown[]).concat(statement.Action ?? []);
        if (!actions.some((a) => typeof a === "string" && a.startsWith("s3vectors:"))) continue;
        const resources = ([] as unknown[]).concat(statement.Resource ?? []);
        expect(resources).not.toContain("*");
        expect(JSON.stringify(resources)).toContain("VectorBucket");
        if (name.startsWith("ApiFunction")) {
          expect(actions.sort()).toEqual(["s3vectors:DeleteIndex", "s3vectors:DeleteVectors"]);
        }
      }
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
        PolicyName: Match.stringLikeRegexp(
          "^IngestionPipelineingestpageFunctionServiceRoleDefaultPolicy",
        ),
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

  it("builds exactly three images from services/Dockerfile (SERVICE=api, SERVICE=ingestion, SERVICE=answering), x86_64 only", () => {
    const assets = JSON.parse(
      fs.readFileSync(path.join(outdir, "TestComputeStack.assets.json"), "utf8"),
    ) as { dockerImages: Record<string, { source: Record<string, unknown> }> };
    const images = Object.values(assets.dockerImages);
    expect(images.length).toBe(3);
    const services = images
      .map(
        (image) =>
          (image.source as { dockerBuildArgs: { SERVICE: string } }).dockerBuildArgs.SERVICE,
      )
      .sort();
    expect(services).toEqual(["answering", "api", "ingestion"]);
    for (const image of images) {
      expect(image.source).toMatchObject({
        dockerFile: "services/Dockerfile",
        platform: "linux/amd64",
      });
    }
  });

  it("gives each of the seven ingestion Lambdas its own distinct command over the shared image", () => {
    const fns = template.findResources("AWS::Lambda::Function");
    const ingestionCommands = Object.values(fns)
      .map((fn) => fn.Properties.ImageConfig?.Command?.[0])
      .filter((cmd): cmd is string => typeof cmd === "string" && cmd.startsWith("ingestion."));
    expect(new Set(ingestionCommands).size).toBe(7);
    expect(ingestionCommands.sort()).toEqual(
      [
        "ingestion.handlers.probe_handler",
        "ingestion.handlers.page_handler",
        "ingestion.handlers.chunk_handler",
        "ingestion.handlers.ensure_index_handler",
        "ingestion.handlers.embed_and_index_handler",
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
