import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { CwdDataStack } from "../lib/data-stack";

function synth(): Template {
  const app = new App();
  const stack = new CwdDataStack(app, "TestDataStack", {
    env2: "dev",
    env: { account: "123456789012", region: "us-east-1" },
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
