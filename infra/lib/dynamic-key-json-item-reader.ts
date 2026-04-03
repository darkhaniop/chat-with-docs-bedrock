import * as iam from "aws-cdk-lib/aws-iam";
import type * as s3 from "aws-cdk-lib/aws-s3";
import type * as sfn from "aws-cdk-lib/aws-stepfunctions";

/**
 * A Distributed Map `ItemReader` for a JSON array file in S3 whose **key is only known at
 * execution time** (docs/03-ingestion.md#step-2--processpages-distributed-map--ingest-page:
 * `artifacts/{projectId}/{documentId}/probe.json`).
 *
 * CDK's built-in `sfn.S3JsonItemReader` only accepts a static `key: string` — there is no
 * `keyPath` sibling to `bucketNamePath` the way there is for the bucket. The underlying ASL
 * `ItemReader.Parameters` field supports a `"Key.$"` JsonPath reference natively (the same
 * `.$`-suffix convention every other Task `Parameters` field uses); this class renders that
 * directly rather than going through the L2 wrapper.
 *
 * > **Verify before relying on this in a real execution.** This shape (`Resource:
 * > "arn:aws:states:::s3:getObject"`, `ReaderConfig.InputType: "JSON"`, `Parameters: {Bucket,
 * > "Key.$"}`) is not exercised by any offline `cdk synth`/jest assertion beyond "the JSON looks
 * > right" — it needs a real Distributed Map execution against a real `probe.json` object to
 * > confirm Step Functions accepts a JsonPath `Key` here. Recorded as a blocker in
 * > docs/10-roadmap.md's Phase 3 log; re-check the first time `/ingest` is actually run against
 * > the deployed `dev` stack.
 */
export class DynamicKeyJsonItemReader implements sfn.IItemReader {
  public readonly bucket: s3.IBucket;
  public readonly resource = "arn:aws:states:::s3:getObject";
  public readonly maxItems?: number;
  private readonly keyJsonPath: string;
  private readonly readablePrefix: string;

  constructor(props: { bucket: s3.IBucket; keyJsonPath: string; readablePrefix: string }) {
    this.bucket = props.bucket;
    this.keyJsonPath = props.keyJsonPath;
    this.readablePrefix = props.readablePrefix;
  }

  public render(): unknown {
    return {
      Resource: this.resource,
      ReaderConfig: { InputType: "JSON" },
      Parameters: {
        Bucket: this.bucket.bucketName,
        "Key.$": this.keyJsonPath,
      },
    };
  }

  public providePolicyStatements(): iam.PolicyStatement[] {
    return [
      new iam.PolicyStatement({
        actions: ["s3:GetObject"],
        resources: [this.bucket.arnForObjects(this.readablePrefix)],
      }),
    ];
  }

  public validateItemReader(): string[] {
    return [];
  }
}
