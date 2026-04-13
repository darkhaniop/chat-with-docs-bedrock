import * as path from "node:path";
import { CfnOutput, Duration, RemovalPolicy, Stack, type StackProps } from "aws-cdk-lib";
import * as appsync from "aws-cdk-lib/aws-appsync";
import type * as cognito from "aws-cdk-lib/aws-cognito";
import type * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ecrAssets from "aws-cdk-lib/aws-ecr-assets";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import type { Construct } from "constructs";

export interface CwdRealtimeStackProps extends StackProps {
  readonly env2: string;
  readonly userPool: cognito.IUserPool;
  readonly table: dynamodb.ITableV2;
}

// docs/05-api-contracts.md#appsync-events: the two documented channel namespaces. A channel's
// namespace is its first path segment (`/projects/{id}` -> namespace `projects`), which is also
// what `services/api/api/channel_authorizer.py` reads off `event.info.channel.segments[0]`.
const NAMESPACES = ["projects", "conversations"] as const;

/**
 * docs/09-operations.md#stacks: "AppSync Events API, namespaces, authorizers." Depends on
 * `CwdDataStack` (the `onSubscribe` authorizer needs to read `ownerSub`) and `CwdAuthStack` (the
 * Cognito user pool backs the subscribe/connect auth mode) but nothing here depends on
 * `CwdComputeStack` — deliberately one-directional, so `CwdComputeStack` can depend on *this*
 * stack's `eventApi` (to grant its own Lambdas `appsync:EventPublish` and to read
 * `eventApi.httpDns` into their environment) without creating a cycle.
 */
export class CwdRealtimeStack extends Stack {
  public readonly eventApi: appsync.EventApi;
  public readonly onSubscribeFunction: lambda.DockerImageFunction;

  constructor(scope: Construct, id: string, props: CwdRealtimeStackProps) {
    super(scope, id, props);

    const repoRoot = path.join(__dirname, "..", "..");

    // docs/07-security.md#channel-authorization: "Parses the channel path for the resource id,
    // reads the resource's ownerSub from DynamoDB, compares to the JWT sub." Shares the `api`
    // Docker image (same convention as `document-sweeper`/`message-sweeper`) with its own CMD,
    // role, and function — read-only DynamoDB access is all it ever needs.
    const logGroup = new logs.LogGroup(this, "OnSubscribeLogGroup", {
      logGroupName: `/aws/lambda/cwd-${props.env2}-channel-authorizer`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    this.onSubscribeFunction = new lambda.DockerImageFunction(this, "OnSubscribeFunction", {
      functionName: `cwd-${props.env2}-channel-authorizer`,
      code: lambda.DockerImageCode.fromImageAsset(repoRoot, {
        file: "services/Dockerfile",
        buildArgs: { SERVICE: "api" },
        platform: ecrAssets.Platform.LINUX_AMD64,
        cmd: ["api.channel_authorizer.lambda_handler"],
      }),
      architecture: lambda.Architecture.X86_64,
      memorySize: 512,
      // Synchronous, on the critical path of every subscribe attempt — kept short deliberately.
      timeout: Duration.seconds(10),
      logGroup,
      environment: { CWD_ENV: props.env2 },
    });
    props.table.grantReadData(this.onSubscribeFunction);

    // docs/07-security.md#channel-authorization / #iam: Cognito user pool for the browser
    // (connect + subscribe), IAM for every backend publisher — "no Cognito principal has publish
    // permission on either namespace."
    this.eventApi = new appsync.EventApi(this, "EventApi", {
      apiName: `cwd-${props.env2}-events`,
      authorizationConfig: {
        authProviders: [
          {
            authorizationType: appsync.AppSyncAuthorizationType.USER_POOL,
            cognitoConfig: { userPool: props.userPool },
          },
          { authorizationType: appsync.AppSyncAuthorizationType.IAM },
        ],
        connectionAuthModeTypes: [appsync.AppSyncAuthorizationType.USER_POOL],
        defaultPublishAuthModeTypes: [appsync.AppSyncAuthorizationType.IAM],
        defaultSubscribeAuthModeTypes: [appsync.AppSyncAuthorizationType.USER_POOL],
      },
    });

    const onSubscribeDataSource = this.eventApi.addLambdaDataSource(
      "OnSubscribeDataSource",
      this.onSubscribeFunction,
    );

    for (const namespace of NAMESPACES) {
      // `direct: true` (default `REQUEST_RESPONSE` invoke type): AppSync calls the Lambda
      // directly for every subscribe attempt and expects `null` (allow) or `{error}` (deny) —
      // no channel-handler JS code needed (docs/07-security.md#channel-authorization).
      this.eventApi.addChannelNamespace(namespace, {
        channelNamespaceName: namespace,
        subscribeHandlerConfig: { dataSource: onSubscribeDataSource, direct: true },
      });
    }

    new CfnOutput(this, "EventsHttpDomain", { value: this.eventApi.httpDns });
    new CfnOutput(this, "EventsRealtimeDomain", { value: this.eventApi.realtimeDns });
  }
}
