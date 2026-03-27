import { RemovalPolicy, Stack, type StackProps } from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3vectors from "aws-cdk-lib/aws-s3vectors";
import type { Construct } from "constructs";
import { documentsBucketName, tableName, vectorBucketName } from "./naming";

export interface CwdDataStackProps extends StackProps {
  readonly env2: string; // logical env name (`dev`), distinct from cdk.Environment's `env`
}

/**
 * docs/09-operations.md#stacks: table, documents bucket, vector bucket. Rarely changes;
 * everything here is `RemovalPolicy.RETAIN` because a replacement means data loss
 * (docs/09-operations.md#rollback).
 */
export class CwdDataStack extends Stack {
  public readonly table: dynamodb.TableV2;
  public readonly documentsBucket: s3.Bucket;
  public readonly vectorBucket: s3vectors.CfnVectorBucket;

  constructor(scope: Construct, id: string, props: CwdDataStackProps) {
    super(scope, id, props);

    const account = this.account;

    // docs/02-data-model.md#dynamodb-single-table: pk/sk strings, on-demand, PITR, TTL on
    // `expiresAt`.
    this.table = new dynamodb.TableV2(this, "Table", {
      tableName: tableName(props.env2),
      partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "sk", type: dynamodb.AttributeType.STRING },
      billing: dynamodb.Billing.onDemand(),
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      timeToLiveAttribute: "expiresAt",
      removalPolicy: RemovalPolicy.RETAIN,
    });

    // docs/02-data-model.md#s3-layout. CORS and lifecycle rules land in Phase 2 once the
    // upload path exists to exercise them.
    this.documentsBucket = new s3.Bucket(this, "DocumentsBucket", {
      bucketName: documentsBucketName(props.env2, account),
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      versioned: false,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    // docs/02-data-model.md#s3-vectors. One vector bucket per environment; indexes are created
    // lazily per-project at ingest time (Phase 4), not here.
    this.vectorBucket = new s3vectors.CfnVectorBucket(this, "VectorBucket", {
      vectorBucketName: vectorBucketName(props.env2, account),
      encryptionConfiguration: { sseType: "AES256" },
    });
    this.vectorBucket.applyRemovalPolicy(RemovalPolicy.RETAIN);
  }
}
