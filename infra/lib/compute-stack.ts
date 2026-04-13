import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { CfnOutput, Duration, RemovalPolicy, Stack, type StackProps } from "aws-cdk-lib";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import { HttpJwtAuthorizer } from "aws-cdk-lib/aws-apigatewayv2-authorizers";
import { HttpLambdaIntegration } from "aws-cdk-lib/aws-apigatewayv2-integrations";
import type * as appsync from "aws-cdk-lib/aws-appsync";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ecrAssets from "aws-cdk-lib/aws-ecr-assets";
import * as events from "aws-cdk-lib/aws-events";
import * as eventsTargets from "aws-cdk-lib/aws-events-targets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { SqsEventSource } from "aws-cdk-lib/aws-lambda-event-sources";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import type * as s3vectors from "aws-cdk-lib/aws-s3vectors";
import * as sqs from "aws-cdk-lib/aws-sqs";
import type { Construct } from "constructs";
import { IngestionPipeline } from "./ingestion-pipeline";
import { dashboardName } from "./naming";

const SONNET_MODEL_ID = "us.anthropic.claude-sonnet-4-6";
const HAIKU_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0";

const NOVA_MODEL_ID = "amazon.nova-2-multimodal-embeddings-v1:0";

const US_INFERENCE_PROFILE_REGIONS = ["us-east-1", "us-east-2", "us-west-2"];

function bedrockInferenceProfileArns(modelId: string, region: string, account: string): string[] {
  const bareModelId = modelId.replace(/^(us|global)\./, "");
  return [
    `arn:aws:bedrock:${region}:${account}:inference-profile/${modelId}`,
    ...US_INFERENCE_PROFILE_REGIONS.map(
      (r) => `arn:aws:bedrock:${r}::foundation-model/${bareModelId}`,
    ),
  ];
}

export interface CwdComputeStackProps extends StackProps {
  readonly env2: string;
  readonly userPoolClientId: string;
  readonly userPoolIssuer: string;
  readonly webDistributionDomainName: string;
  readonly table: dynamodb.ITableV2;
  readonly documentsBucket: s3.IBucket;
  readonly vectorBucket: s3vectors.CfnVectorBucket;
  readonly eventsApi: appsync.IEventApi;
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
  public readonly sweeperFunction: lambda.DockerImageFunction;
  public readonly messageSweeperFunction: lambda.DockerImageFunction;
  public readonly dlqHandlerFunction: lambda.DockerImageFunction;
  public readonly answeringFunction: lambda.DockerImageFunction;
  public readonly answerQueue: sqs.Queue;
  public readonly answerQueueDlq: sqs.Queue;
  public readonly ingestionPipeline: IngestionPipeline;

  constructor(scope: Construct, id: string, props: CwdComputeStackProps) {
    super(scope, id, props);

    // Repository root — the Docker build context must be the root because
    // services/Dockerfile COPYs services/common alongside services/api.
    const repoRoot = path.join(__dirname, "..", "..");

    this.ingestionPipeline = new IngestionPipeline(this, "IngestionPipeline", {
      env2: props.env2,
      table: props.table,
      documentsBucket: props.documentsBucket,
      vectorBucket: props.vectorBucket,
      eventsApi: props.eventsApi,
    });

    const apiImageCode = lambda.DockerImageCode.fromImageAsset(repoRoot, {
      file: "services/Dockerfile",
      buildArgs: { SERVICE: "api" },
      platform: ecrAssets.Platform.LINUX_AMD64,
      cmd: ["api.handler.lambda_handler"],
    });

    const sharedEnvironment = {
      CWD_ENV: props.env2,
      CWD_VERSION: currentVersion(repoRoot),
      CWD_COMMIT: currentCommit(),
      CWD_DOCUMENTS_BUCKET_NAME: props.documentsBucket.bucketName,
      CWD_VECTOR_BUCKET_NAME: props.vectorBucket.vectorBucketName as string,
      CWD_EVENTS_HTTP_DOMAIN: props.eventsApi.httpDns,
    };

    const apiLogGroup = new logs.LogGroup(this, "ApiLogGroup", {
      logGroupName: `/aws/lambda/cwd-${props.env2}-api`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    this.answerQueueDlq = new sqs.Queue(this, "AnswerQueueDlq", {
      queueName: `cwd-${props.env2}-answer-queue-dlq`,
      retentionPeriod: Duration.days(14),
      visibilityTimeout: Duration.minutes(2),
    });
    this.answerQueue = new sqs.Queue(this, "AnswerQueue", {
      queueName: `cwd-${props.env2}-answer-queue`,
      visibilityTimeout: Duration.minutes(5),
      deadLetterQueue: { queue: this.answerQueueDlq, maxReceiveCount: 2 },
    });

    const answeringLogGroup = new logs.LogGroup(this, "AnsweringLogGroup", {
      logGroupName: `/aws/lambda/cwd-${props.env2}-answering`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    this.answeringFunction = new lambda.DockerImageFunction(this, "AnsweringFunction", {
      functionName: `cwd-${props.env2}-answering`,
      code: lambda.DockerImageCode.fromImageAsset(repoRoot, {
        file: "services/Dockerfile",
        buildArgs: { SERVICE: "answering" },
        platform: ecrAssets.Platform.LINUX_AMD64,
        cmd: ["answering.handler.lambda_handler"],
      }),
      architecture: lambda.Architecture.X86_64,
      memorySize: 2048,
      timeout: Duration.minutes(5),
      logGroup: answeringLogGroup,
      environment: sharedEnvironment,
    });
    this.answeringFunction.addEventSource(new SqsEventSource(this.answerQueue, { batchSize: 1 }));
    this._grantAnsweringPermissions(this.answeringFunction, props);
    props.eventsApi.grantPublish(this.answeringFunction);

    this.apiFunction = new lambda.DockerImageFunction(this, "ApiFunction", {
      functionName: `cwd-${props.env2}-api`,
      code: apiImageCode,
      architecture: lambda.Architecture.X86_64,
      memorySize: 512,
      timeout: Duration.seconds(29),
      logGroup: apiLogGroup,
      environment: {
        ...sharedEnvironment,
        CWD_INGESTION_STATE_MACHINE_ARN: this.ingestionPipeline.stateMachine.stateMachineArn,
        CWD_ANSWER_QUEUE_URL: this.answerQueue.queueUrl,
      },
    });
    this._grantApiPermissions(this.apiFunction, props);
    this.ingestionPipeline.stateMachine.grantStartExecution(this.apiFunction);
    this.answerQueue.grantSendMessages(this.apiFunction);

    const messageSweeperLogGroup = new logs.LogGroup(this, "MessageSweeperLogGroup", {
      logGroupName: `/aws/lambda/cwd-${props.env2}-message-sweeper`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    this.messageSweeperFunction = new lambda.DockerImageFunction(this, "MessageSweeperFunction", {
      functionName: `cwd-${props.env2}-message-sweeper`,
      code: lambda.DockerImageCode.fromImageAsset(repoRoot, {
        file: "services/Dockerfile",
        buildArgs: { SERVICE: "api" },
        platform: ecrAssets.Platform.LINUX_AMD64,
        cmd: ["api.message_sweeper.lambda_handler"],
      }),
      architecture: lambda.Architecture.X86_64,
      memorySize: 512,
      timeout: Duration.minutes(5),
      logGroup: messageSweeperLogGroup,
      environment: sharedEnvironment,
    });
    props.table.grantReadWriteData(this.messageSweeperFunction);
    props.eventsApi.grantPublish(this.messageSweeperFunction);
    new events.Rule(this, "MessageSweeperSchedule", {
      schedule: events.Schedule.rate(Duration.minutes(5)),
      targets: [new eventsTargets.LambdaFunction(this.messageSweeperFunction)],
    });

    const dlqHandlerLogGroup = new logs.LogGroup(this, "DlqHandlerLogGroup", {
      logGroupName: `/aws/lambda/cwd-${props.env2}-answering-dlq-handler`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    this.dlqHandlerFunction = new lambda.DockerImageFunction(this, "DlqHandlerFunction", {
      functionName: `cwd-${props.env2}-answering-dlq-handler`,
      code: lambda.DockerImageCode.fromImageAsset(repoRoot, {
        file: "services/Dockerfile",
        buildArgs: { SERVICE: "api" },
        platform: ecrAssets.Platform.LINUX_AMD64,
        cmd: ["api.dlq_handler.lambda_handler"],
      }),
      architecture: lambda.Architecture.X86_64,
      memorySize: 512,
      timeout: Duration.minutes(1),
      logGroup: dlqHandlerLogGroup,
      environment: sharedEnvironment,
    });
    this.dlqHandlerFunction.addEventSource(
      new SqsEventSource(this.answerQueueDlq, { batchSize: 1 }),
    );
    props.table.grantReadWriteData(this.dlqHandlerFunction);
    props.eventsApi.grantPublish(this.dlqHandlerFunction);

    const sweeperLogGroup = new logs.LogGroup(this, "SweeperLogGroup", {
      logGroupName: `/aws/lambda/cwd-${props.env2}-document-sweeper`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    this.sweeperFunction = new lambda.DockerImageFunction(this, "SweeperFunction", {
      functionName: `cwd-${props.env2}-document-sweeper`,
      code: lambda.DockerImageCode.fromImageAsset(repoRoot, {
        file: "services/Dockerfile",
        buildArgs: { SERVICE: "api" },
        platform: ecrAssets.Platform.LINUX_AMD64,
        cmd: ["api.sweeper.lambda_handler"],
      }),
      architecture: lambda.Architecture.X86_64,
      memorySize: 512,
      timeout: Duration.minutes(5),
      logGroup: sweeperLogGroup,
      environment: sharedEnvironment,
    });
    this._grantSweeperPermissions(this.sweeperFunction, props);

    new events.Rule(this, "SweeperSchedule", {
      schedule: events.Schedule.rate(Duration.days(1)),
      targets: [new eventsTargets.LambdaFunction(this.sweeperFunction)],
    });

    this.api = new apigwv2.HttpApi(this, "HttpApi", {
      apiName: `cwd-${props.env2}-api`,
      createDefaultStage: true,
      // The SPA is a browser client on a different origin (CloudFront) from the API
      // (execute-api).
      corsPreflight: {
        allowOrigins: [`https://${props.webDistributionDomainName}`, "http://localhost:5173"],
        allowMethods: [
          apigwv2.CorsHttpMethod.GET,
          apigwv2.CorsHttpMethod.POST,
          apigwv2.CorsHttpMethod.PATCH,
          apigwv2.CorsHttpMethod.DELETE,
        ],
        allowHeaders: ["Authorization", "Content-Type"],
      },
    });

    const jwtAuthorizer = new HttpJwtAuthorizer("JwtAuthorizer", props.userPoolIssuer, {
      jwtAudience: [props.userPoolClientId],
    });

    const integration = new HttpLambdaIntegration("ApiIntegration", this.apiFunction);

    this.api.addRoutes({ path: "/health", methods: [apigwv2.HttpMethod.GET], integration });

    const authenticatedRoutes: Array<[string, apigwv2.HttpMethod]> = [
      ["/projects", apigwv2.HttpMethod.POST],
      ["/projects", apigwv2.HttpMethod.GET],
      ["/projects/{projectId}", apigwv2.HttpMethod.GET],
      ["/projects/{projectId}", apigwv2.HttpMethod.PATCH],
      ["/projects/{projectId}", apigwv2.HttpMethod.DELETE],
      ["/projects/{projectId}/documents", apigwv2.HttpMethod.POST],
      ["/projects/{projectId}/documents", apigwv2.HttpMethod.GET],
      ["/projects/{projectId}/documents/{documentId}", apigwv2.HttpMethod.GET],
      ["/projects/{projectId}/documents/{documentId}", apigwv2.HttpMethod.DELETE],
      ["/projects/{projectId}/documents/{documentId}/ingest", apigwv2.HttpMethod.POST],
      ["/projects/{projectId}/documents/{documentId}/source-url", apigwv2.HttpMethod.GET],
      [
        "/projects/{projectId}/documents/{documentId}/pages/{page}/render-url",
        apigwv2.HttpMethod.GET,
      ],
      ["/projects/{projectId}/conversations", apigwv2.HttpMethod.POST],
      ["/projects/{projectId}/conversations", apigwv2.HttpMethod.GET],
      ["/conversations/{conversationId}", apigwv2.HttpMethod.GET],
      ["/conversations/{conversationId}", apigwv2.HttpMethod.PATCH],
      ["/conversations/{conversationId}", apigwv2.HttpMethod.DELETE],
      ["/conversations/{conversationId}/messages", apigwv2.HttpMethod.GET],
      ["/conversations/{conversationId}/messages", apigwv2.HttpMethod.POST],
      ["/conversations/{conversationId}/messages/{messageId}/cancel", apigwv2.HttpMethod.POST],
    ];
    for (const [routePath, method] of authenticatedRoutes) {
      this.api.addRoutes({
        path: routePath,
        methods: [method],
        integration,
        authorizer: jwtAuthorizer,
      });
    }

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

  private _grantApiPermissions(fn: lambda.IFunction, props: CwdComputeStackProps): void {
    props.table.grantReadWriteData(fn);
    props.table.grant(fn, "dynamodb:TransactWriteItems");
    for (const prefix of ["raw/*", "pages/*"]) {
      props.documentsBucket.grantReadWrite(fn, prefix);
      props.documentsBucket.grantDelete(fn, prefix);
    }
    const vectorBucketArn = props.vectorBucket.attrVectorBucketArn;
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["s3vectors:DeleteVectors", "s3vectors:DeleteIndex"],
        resources: [vectorBucketArn, `${vectorBucketArn}/index/*`],
      }),
    );
  }

  private _grantSweeperPermissions(fn: lambda.IFunction, props: CwdComputeStackProps): void {
    props.table.grantReadWriteData(fn);
    props.table.grant(fn, "dynamodb:TransactWriteItems");
    for (const prefix of ["raw/*", "pages/*"]) {
      props.documentsBucket.grantRead(fn, prefix);
      props.documentsBucket.grantDelete(fn, prefix);
    }
  }

  private _grantAnsweringPermissions(fn: lambda.IFunction, props: CwdComputeStackProps): void {
    props.table.grantReadWriteData(fn);
    props.table.grant(fn, "dynamodb:TransactWriteItems");
    props.documentsBucket.grantRead(fn, "pages/*");

    const vectorBucketArn = props.vectorBucket.attrVectorBucketArn;
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["s3vectors:QueryVectors", "s3vectors:GetVectors"],
        resources: [vectorBucketArn, `${vectorBucketArn}/index/*`],
      }),
    );

    const region = Stack.of(this).region;
    const account = Stack.of(this).account;
    const modelArns = [
      ...bedrockInferenceProfileArns(SONNET_MODEL_ID, region, account),
      ...bedrockInferenceProfileArns(HAIKU_MODEL_ID, region, account),
    ];
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        resources: modelArns,
      }),
    );

    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:InvokeModel"],
        resources: [`arn:aws:bedrock:${region}::foundation-model/${NOVA_MODEL_ID}`],
      }),
    );
  }
}
