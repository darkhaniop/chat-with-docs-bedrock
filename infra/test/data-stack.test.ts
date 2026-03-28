import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { CwdDataStack } from "../lib/data-stack";

function synth(): Template {
  const app = new App();
  const stack = new CwdDataStack(app, "TestDataStack", {
    env2: "dev",
    env: { account: "123456789012", region: "us-east-1" },
    webDistributionDomainName: "d111111abcdef8.cloudfront.net",
  });
  return Template.fromStack(stack);
}

describe("CwdDataStack", () => {
  it("creates a table with pk/sk, on-demand billing, PITR, and TTL on expiresAt", () => {
    const template = synth();
    template.hasResourceProperties("AWS::DynamoDB::GlobalTable", {
      TableName: "cwd-dev",
      AttributeDefinitions: Match.arrayWith([
        { AttributeName: "pk", AttributeType: "S" },
        { AttributeName: "sk", AttributeType: "S" },
      ]),
      KeySchema: [
        { AttributeName: "pk", KeyType: "HASH" },
        { AttributeName: "sk", KeyType: "RANGE" },
      ],
      BillingMode: "PAY_PER_REQUEST",
      TimeToLiveSpecification: { AttributeName: "expiresAt", Enabled: true },
    });
  });

  it("retains the table on stack deletion", () => {
    const template = synth();
    template.hasResource("AWS::DynamoDB::GlobalTable", { DeletionPolicy: "Retain" });
  });

  it("blocks public access and encrypts the documents bucket", () => {
    const template = synth();
    template.hasResourceProperties("AWS::S3::Bucket", {
      BucketName: "cwd-documents-dev-123456789012",
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
      BucketEncryption: {
        ServerSideEncryptionConfiguration: Match.arrayWith([
          Match.objectLike({
            ServerSideEncryptionByDefault: { SSEAlgorithm: "AES256" },
          }),
        ]),
      },
    });
  });

  it("retains the documents bucket on stack deletion", () => {
    const template = synth();
    template.hasResource("AWS::S3::Bucket", {
      DeletionPolicy: "Retain",
      Properties: Match.objectLike({ BucketName: "cwd-documents-dev-123456789012" }),
    });
  });

  it("allows the documents bucket's CORS from the CloudFront origin and localhost only", () => {
    const template = synth();
    template.hasResourceProperties("AWS::S3::Bucket", {
      BucketName: "cwd-documents-dev-123456789012",
      CorsConfiguration: {
        CorsRules: Match.arrayWith([
          Match.objectLike({
            AllowedOrigins: Match.arrayWith([
              "https://d111111abcdef8.cloudfront.net",
              "http://localhost:5173",
            ]),
            AllowedMethods: Match.arrayWith(["PUT", "GET"]),
          }),
        ]),
      },
    });
  });

  it("transitions artifacts/ to Infrequent Access after 30 days and aborts stale multipart uploads", () => {
    const template = synth();
    template.hasResourceProperties("AWS::S3::Bucket", {
      BucketName: "cwd-documents-dev-123456789012",
      LifecycleConfiguration: {
        Rules: Match.arrayWith([
          Match.objectLike({
            Prefix: "artifacts/",
            Transitions: Match.arrayWith([
              Match.objectLike({ StorageClass: "STANDARD_IA", TransitionInDays: 30 }),
            ]),
          }),
          Match.objectLike({ AbortIncompleteMultipartUpload: { DaysAfterInitiation: 1 } }),
        ]),
      },
    });
  });

  it("creates one vector bucket named per the env/account convention", () => {
    const template = synth();
    template.hasResourceProperties("AWS::S3Vectors::VectorBucket", {
      VectorBucketName: "cwd-vectors-dev-123456789012",
    });
    template.hasResource("AWS::S3Vectors::VectorBucket", { DeletionPolicy: "Retain" });
  });

  it("matches the committed template snapshot", () => {
    expect(synth().toJSON()).toMatchSnapshot();
  });
});
