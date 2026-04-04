import * as path from "node:path";
import { Duration, RemovalPolicy, Stack } from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ecrAssets from "aws-cdk-lib/aws-ecr-assets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import type * as s3 from "aws-cdk-lib/aws-s3";
import type * as s3vectors from "aws-cdk-lib/aws-s3vectors";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import { Construct } from "constructs";
import { DynamicKeyJsonItemReader } from "./dynamic-key-json-item-reader";
import { ingestionStateMachineName } from "./naming";

export interface IngestionPipelineProps {
  readonly env2: string;
  readonly table: dynamodb.ITableV2;
  readonly documentsBucket: s3.IBucket;
  readonly vectorBucket: s3vectors.CfnVectorBucket;
}

// services/common/common/config.py's `Settings.nova_model_id` default — kept in sync by hand,
// same as `DISTRIBUTED_MAP_MAX_CONCURRENCY` below, since Python and CDK don't share a config
// source. Nova has no inference profile (docs/10-roadmap.md's Phase 0 findings: only Sonnet/
// Haiku need the `us.`/`global.` prefix), so this is a plain foundation-model ARN with no
// account segment.
const NOVA_MODEL_ID = "amazon.nova-2-multimodal-embeddings-v1:0";

// docs/03-ingestion.md#state-machine: "Every state has Retry on States.TaskFailed/
// Lambda.ServiceException/Lambda.TooManyRequestsException with exponential backoff (2 s base,
// 2x rate, 4 attempts)."
const RETRYABLE_ERRORS = [
  "States.TaskFailed",
  "Lambda.ServiceException",
  "Lambda.TooManyRequestsException",
];
const RETRY_PROPS: sfn.RetryProps = {
  errors: RETRYABLE_ERRORS,
  interval: Duration.seconds(2),
  backoffRate: 2,
  maxAttempts: 4,
};

// docs/03-ingestion.md#performance-targets and docs/07-security.md#abuse-and-cost-controls:
// matches `Settings.distributed_map_max_concurrency` in services/common/common/config.py —
// kept in sync by hand, since Python and CDK don't share a config source.
//
// `Settings.ingestion_reserved_concurrency` (25) is **not** applied here — found live on first
// deploy: this sandbox account's entire Lambda concurrency ceiling is 10
// (`aws lambda get-account-settings` -> `AccountLimit.ConcurrentExecutions: 10`), and AWS
// hard-rejects any `ReservedConcurrentExecutions` that would leave fewer than 10 unreserved.
// With a 10-execution total budget, reserving *any* amount for *any* function is already
// impossible without violating that floor, let alone 25 x 5 functions. Left unreserved, every
// ingestion Lambda draws from the shared unreserved pool like `api`/`document-sweeper` already
// do; `maxConcurrency` on the Distributed Map below is still a real, working throttle (Step
// Functions' own retry-with-backoff on `Lambda.TooManyRequestsException` absorbs the
// throttling this account's tiny ceiling will cause under real fan-out). Revisit
// `reservedConcurrentExecutions` if this ever runs in an account with a normal (1000+) default
// concurrency limit — do not reintroduce it against this sandbox account.
const DISTRIBUTED_MAP_MAX_CONCURRENCY = 20;

/**
 * docs/03-ingestion.md's Step Functions Standard state machine: Probe -> ProcessPages
 * (Distributed Map) -> Chunk -> EnsureIndex -> EmbedAndIndex -> Finalize, each with
 * Catch -> MarkFailed.
 */
export class IngestionPipeline extends Construct {
  public readonly stateMachine: sfn.StateMachine;
  public readonly probeFunction: lambda.DockerImageFunction;
  public readonly pageFunction: lambda.DockerImageFunction;
  public readonly chunkFunction: lambda.DockerImageFunction;
  public readonly ensureIndexFunction: lambda.DockerImageFunction;
  public readonly embedAndIndexFunction: lambda.DockerImageFunction;
  public readonly finalizeFunction: lambda.DockerImageFunction;
  public readonly markFailedFunction: lambda.DockerImageFunction;

  constructor(scope: Construct, id: string, props: IngestionPipelineProps) {
    super(scope, id);

    const region = Stack.of(this).region;
    const novaModelArn = `arn:aws:bedrock:${region}::foundation-model/${NOVA_MODEL_ID}`;
    const vectorBucketArn = props.vectorBucket.attrVectorBucketArn;

    const repoRoot = path.join(__dirname, "..", "..");
    const sharedEnvironment = {
      CWD_ENV: props.env2,
      CWD_DOCUMENTS_BUCKET_NAME: props.documentsBucket.bucketName,
      CWD_VECTOR_BUCKET_NAME: props.vectorBucket.vectorBucketName as string,
    };

    const makeFunction = (name: string, cmd: string, memoryMb: number): lambda.DockerImageFunction => {
      const logGroup = new logs.LogGroup(this, `${name}LogGroup`, {
        logGroupName: `/aws/lambda/cwd-${props.env2}-${name}`,
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: RemovalPolicy.DESTROY,
      });
      const fn = new lambda.DockerImageFunction(this, `${name}Function`, {
        functionName: `cwd-${props.env2}-${name}`,
        code: lambda.DockerImageCode.fromImageAsset(repoRoot, {
          file: "services/Dockerfile",
          buildArgs: { SERVICE: "ingestion" },
          platform: ecrAssets.Platform.LINUX_AMD64,
          cmd: [cmd],
        }),
        architecture: lambda.Architecture.X86_64,
        memorySize: memoryMb,
        timeout: Duration.minutes(5),
        logGroup,
        environment: sharedEnvironment,
      });
      props.table.grantReadWriteData(fn);
      props.table.grant(fn, "dynamodb:TransactWriteItems");
      return fn;
    };

    // docs/03-ingestion.md#step-1--probe: downloads raw/, writes artifacts/probe.json, writes
    // Page items.
    this.probeFunction = makeFunction("ingest-probe", "ingestion.handlers.probe_handler", 1024);
    props.documentsBucket.grantRead(this.probeFunction, "raw/*");
    props.documentsBucket.grantWrite(this.probeFunction, "artifacts/*");

    // docs/03-ingestion.md#step-2--processpages: downloads raw/, writes pages/ + artifacts/
    // blocks/, calls Textract. 2048 MB: PyMuPDF rendering is the most memory-hungry step.
    this.pageFunction = makeFunction("ingest-page", "ingestion.handlers.page_handler", 2048);
    props.documentsBucket.grantRead(this.pageFunction, "raw/*");
    props.documentsBucket.grantReadWrite(this.pageFunction, "pages/*");
    props.documentsBucket.grantReadWrite(this.pageFunction, "artifacts/*");
    this.pageFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["textract:DetectDocumentText"],
        // Textract's DetectDocumentText has no resource-level permissions (AWS-enforced) —
        // "*" here is not the kind of wildcard docs/07-security.md's IAM table warns against
        // (that rule targets enumerable Bedrock model ARNs specifically).
        resources: ["*"],
      }),
    );

    // docs/03-ingestion.md#step-3--chunk: reads artifacts/blocks/*, writes Chunk items and
    // artifacts/chunks.jsonl.
    this.chunkFunction = makeFunction("ingest-chunk", "ingestion.handlers.chunk_handler", 1024);
    props.documentsBucket.grantRead(this.chunkFunction, "artifacts/*");
    props.documentsBucket.grantWrite(this.chunkFunction, "artifacts/*");

    // docs/07-security.md#iam: `ingest-*` gets `s3vectors:*` scoped to the environment's vector
    // bucket (and every index inside it) — a broad action wildcard is fine here because it's
    // resource-scoped to one bucket, unlike the enumerated-model-ARN rule that governs Bedrock.
    const grantVectorBucketAccess = (fn: lambda.IFunction): void => {
      fn.addToRolePolicy(
        new iam.PolicyStatement({
          actions: ["s3vectors:*"],
          resources: [vectorBucketArn, `${vectorBucketArn}/index/*`],
        }),
      );
    };

    // docs/03-ingestion.md#step-4--ensureindex: idempotent index creation only, no S3/Bedrock
    // access needed.
    this.ensureIndexFunction = makeFunction(
      "ingest-ensure-index",
      "ingestion.handlers.ensure_index_handler",
      512,
    );
    grantVectorBucketAccess(this.ensureIndexFunction);

    // docs/03-ingestion.md#step-5--embedandindex: reads chunks/pages (DynamoDB, granted by
    // `makeFunction` already) and each page's `.embed.jpg` render, embeds with Nova, writes
    // vectors. `bedrock:InvokeModel` is scoped to the Nova model ARN only
    // (docs/07-security.md#iam: "Model ARNs are enumerated, never wildcarded").
    this.embedAndIndexFunction = makeFunction(
      "ingest-embed-and-index",
      "ingestion.handlers.embed_and_index_handler",
      1024,
    );
    props.documentsBucket.grantRead(this.embedAndIndexFunction, "pages/*");
    grantVectorBucketAccess(this.embedAndIndexFunction);
    this.embedAndIndexFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:InvokeModel"],
        resources: [novaModelArn],
      }),
    );

    // docs/03-ingestion.md#step-6--finalize: Document/Project updates only, no S3 access.
    this.finalizeFunction = makeFunction("ingest-finalize", "ingestion.handlers.finalize_handler", 512);

    // docs/03-ingestion.md#failure-handling--markfailed: Document status update only.
    this.markFailedFunction = makeFunction("mark-failed", "ingestion.handlers.mark_failed_handler", 512);

    // -- state machine -------------------------------------------------------------------------

    const markFailedTask = new tasks.LambdaInvoke(this, "MarkFailed", {
      lambdaFunction: this.markFailedFunction,
      payloadResponseOnly: true,
      retryOnServiceExceptions: false,
      payload: sfn.TaskInput.fromObject({
        "projectId.$": "$.projectId",
        "documentId.$": "$.documentId",
        "error.$": "$.error",
      }),
    });
    const failState = new sfn.Fail(this, "IngestionFailed", {
      error: "IngestionFailed",
      causePath: "$.statusDetail",
    });
    const errorBranch = markFailedTask.next(failState);

    const probeTask = new tasks.LambdaInvoke(this, "Probe", {
      lambdaFunction: this.probeFunction,
      payloadResponseOnly: true,
      retryOnServiceExceptions: false,
      payload: sfn.TaskInput.fromObject({
        "projectId.$": "$.projectId",
        "documentId.$": "$.documentId",
        "s3Key.$": "$.s3Key",
        "contentType.$": "$.contentType",
      }),
    });
    probeTask.addRetry(RETRY_PROPS);
    probeTask.addCatch(errorBranch, { resultPath: "$.error" });

    const pageTask = new tasks.LambdaInvoke(this, "IngestPage", {
      lambdaFunction: this.pageFunction,
      payloadResponseOnly: true,
      retryOnServiceExceptions: false,
    });
    pageTask.addRetry(RETRY_PROPS);

    // docs/03-ingestion.md#step-2--processpages-distributed-map--ingest-page: item list comes
    // from `artifacts/{p}/{d}/probe.json`, not the state payload, so page count isn't bounded
    // by the 256 KB state limit. See DynamicKeyJsonItemReader's docstring for why a plain
    // `sfn.S3JsonItemReader` can't be used here (its `key` is a static string; ours is only
    // known at execution time).
    const processPages = new sfn.DistributedMap(this, "ProcessPages", {
      itemReader: new DynamicKeyJsonItemReader({
        bucket: props.documentsBucket,
        keyJsonPath: "$.probeKey",
        readablePrefix: "artifacts/*",
      }),
      maxConcurrency: DISTRIBUTED_MAP_MAX_CONCURRENCY,
      itemSelector: {
        "pageNumber.$": "$$.Map.Item.Value.pageNumber",
        "width.$": "$$.Map.Item.Value.width",
        "height.$": "$$.Map.Item.Value.height",
        "rotation.$": "$$.Map.Item.Value.rotation",
        "textSource.$": "$$.Map.Item.Value.textSource",
        "textDensity.$": "$$.Map.Item.Value.textDensity",
        "projectId.$": "$.projectId",
        "documentId.$": "$.documentId",
        "s3Key.$": "$.s3Key",
        "kind.$": "$.kind",
        "pageCount.$": "$.pageCount",
      },
      resultPath: "$.pageResults",
    });
    processPages.itemProcessor(pageTask);
    processPages.addRetry(RETRY_PROPS);
    processPages.addCatch(errorBranch, { resultPath: "$.error" });

    const chunkTask = new tasks.LambdaInvoke(this, "Chunk", {
      lambdaFunction: this.chunkFunction,
      payloadResponseOnly: true,
      retryOnServiceExceptions: false,
      payload: sfn.TaskInput.fromObject({
        "projectId.$": "$.projectId",
        "documentId.$": "$.documentId",
        "pageCount.$": "$.pageCount",
      }),
      resultPath: "$.chunkResult",
    });
    chunkTask.addRetry(RETRY_PROPS);
    chunkTask.addCatch(errorBranch, { resultPath: "$.error" });

    const ensureIndexTask = new tasks.LambdaInvoke(this, "EnsureIndex", {
      lambdaFunction: this.ensureIndexFunction,
      payloadResponseOnly: true,
      retryOnServiceExceptions: false,
      payload: sfn.TaskInput.fromObject({
        "projectId.$": "$.projectId",
        "documentId.$": "$.documentId",
      }),
      resultPath: "$.ensureIndexResult",
    });
    ensureIndexTask.addRetry(RETRY_PROPS);
    ensureIndexTask.addCatch(errorBranch, { resultPath: "$.error" });

    const embedAndIndexTask = new tasks.LambdaInvoke(this, "EmbedAndIndex", {
      lambdaFunction: this.embedAndIndexFunction,
      payloadResponseOnly: true,
      retryOnServiceExceptions: false,
      payload: sfn.TaskInput.fromObject({
        "projectId.$": "$.projectId",
        "documentId.$": "$.documentId",
        "pageCount.$": "$.pageCount",
      }),
      resultPath: "$.embedResult",
    });
    embedAndIndexTask.addRetry(RETRY_PROPS);
    embedAndIndexTask.addCatch(errorBranch, { resultPath: "$.error" });

    const finalizeTask = new tasks.LambdaInvoke(this, "Finalize", {
      lambdaFunction: this.finalizeFunction,
      payloadResponseOnly: true,
      retryOnServiceExceptions: false,
      payload: sfn.TaskInput.fromObject({
        "projectId.$": "$.projectId",
        "documentId.$": "$.documentId",
        "pageCount.$": "$.pageCount",
        "ocrPageCount.$": "$.ocrPageCount",
        "chunkCount.$": "$.chunkResult.chunkCount",
      }),
    });
    finalizeTask.addRetry(RETRY_PROPS);
    finalizeTask.addCatch(errorBranch, { resultPath: "$.error" });

    const definition = probeTask
      .next(processPages)
      .next(chunkTask)
      .next(ensureIndexTask)
      .next(embedAndIndexTask)
      .next(finalizeTask);

    // Standard, not Express (docs/03-ingestion.md#state-machine): a 500-page PDF can exceed
    // five minutes, full execution history matters for debugging, and Distributed Map requires
    // it.
    this.stateMachine = new sfn.StateMachine(this, "StateMachine", {
      stateMachineName: ingestionStateMachineName(props.env2),
      stateMachineType: sfn.StateMachineType.STANDARD,
      definitionBody: sfn.DefinitionBody.fromChainable(definition),
      timeout: Duration.hours(2),
    });
  }
}
