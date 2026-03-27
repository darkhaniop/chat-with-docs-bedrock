import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { CfnOutput, Duration, RemovalPolicy, Stack, type StackProps } from "aws-cdk-lib";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import { HttpJwtAuthorizer } from "aws-cdk-lib/aws-apigatewayv2-authorizers";
import { HttpLambdaIntegration } from "aws-cdk-lib/aws-apigatewayv2-integrations";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as ecrAssets from "aws-cdk-lib/aws-ecr-assets";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import type { Construct } from "constructs";
import { dashboardName } from "./naming";

export interface CwdComputeStackProps extends StackProps {
  readonly env2: string;
  readonly userPoolClientId: string;
  readonly userPoolIssuer: string;
}

/** The deployed commit, for `/health` (it's how drift between `main` and the deployed stack is
 * detected without a CI/CD pipeline). Falls back to "unknown" outside a git checkout. */
function currentCommit(): string {
  try {
    return execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
  } catch {
    return "unknown";
  }
}

function currentVersion(repoRoot: string): string {
  const text = fs.readFileSync(path.join(repoRoot, "pyproject.toml"), "utf8");
  const match = /^version\s*=\s*"([^"]+)"/m.exec(text);
  return match?.[1] ?? "unknown";
}

/**
 * The `api` Lambda, HTTP API with the JWT authorizer, `/health` (unauthenticated) and one
 * authenticated echo route. This is the stack that changes constantly, unlike Data and Auth.
 */
export class CwdComputeStack extends Stack {
  public readonly api: apigwv2.HttpApi;
  public readonly apiFunction: lambda.DockerImageFunction;

  constructor(scope: Construct, id: string, props: CwdComputeStackProps) {
    super(scope, id, props);

    // Repository root — the Docker build context must be the root because
    // services/Dockerfile COPYs services/common alongside services/api.
    const repoRoot = path.join(__dirname, "..", "..");

    const logGroup = new logs.LogGroup(this, "ApiLogGroup", {
      logGroupName: `/aws/lambda/cwd-${props.env2}-api`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    this.apiFunction = new lambda.DockerImageFunction(this, "ApiFunction", {
      functionName: `cwd-${props.env2}-api`,
      code: lambda.DockerImageCode.fromImageAsset(repoRoot, {
        file: "services/Dockerfile",
        buildArgs: { SERVICE: "api" },
        platform: ecrAssets.Platform.LINUX_AMD64,
        cmd: ["api.handler.lambda_handler"],
      }),
      architecture: lambda.Architecture.X86_64,
      memorySize: 512,
      timeout: Duration.seconds(10),
      logGroup,
      environment: {
        CWD_ENV: props.env2,
        CWD_VERSION: currentVersion(repoRoot),
        CWD_COMMIT: currentCommit(),
      },
    });

    this.api = new apigwv2.HttpApi(this, "HttpApi", {
      apiName: `cwd-${props.env2}-api`,
      createDefaultStage: true,
    });

    const jwtAuthorizer = new HttpJwtAuthorizer("JwtAuthorizer", props.userPoolIssuer, {
      jwtAudience: [props.userPoolClientId],
    });

    const integration = new HttpLambdaIntegration("ApiIntegration", this.apiFunction);

    this.api.addRoutes({
      path: "/health",
      methods: [apigwv2.HttpMethod.GET],
      integration,
    });

    this.api.addRoutes({
      path: "/echo",
      methods: [apigwv2.HttpMethod.GET],
      integration,
      authorizer: jwtAuthorizer,
    });

    new cloudwatch.Dashboard(this, "Dashboard", {
      dashboardName: dashboardName(props.env2),
      widgets: [
        [
          new cloudwatch.GraphWidget({
            title: "api Lambda invocations / errors",
            left: [this.apiFunction.metricInvocations(), this.apiFunction.metricErrors()],
          }),
          new cloudwatch.GraphWidget({
            title: "api Lambda duration",
            left: [this.apiFunction.metricDuration()],
          }),
        ],
      ],
    });

    new CfnOutput(this, "ApiBaseUrl", { value: this.api.apiEndpoint });
  }
}
