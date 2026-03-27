import * as path from "node:path";
import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { CwdWebStack } from "../lib/web-stack";

// Fixed and checked-in, never `web/dist` — a snapshot must not depend on whether a developer
// happened to run `npm run build` in `web/` before running this test.
const testSiteContentDir = path.join(__dirname, "..", "assets", "web-placeholder");

function synth(): Template {
  const app = new App();
  const stack = new CwdWebStack(app, "TestWebStack", {
    env2: "dev",
    env: { account: "123456789012", region: "us-east-1" },
    siteContentDir: testSiteContentDir,
  });
  return Template.fromStack(stack);
}

describe("CwdWebStack", () => {
  it("blocks all public access on the site bucket", () => {
    const template = synth();
    template.hasResourceProperties("AWS::S3::Bucket", {
      BucketName: "cwd-site-dev-123456789012",
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
    });
  });

  it("serves the site bucket only through CloudFront via Origin Access Control", () => {
    const template = synth();
    template.resourceCountIs("AWS::CloudFront::OriginAccessControl", 1);
    template.hasResourceProperties("AWS::CloudFront::Distribution", {
      DistributionConfig: Match.objectLike({
        DefaultRootObject: "index.html",
        Origins: Match.arrayWith([Match.objectLike({ OriginAccessControlId: Match.anyValue() })]),
      }),
    });
  });

  it("falls back to index.html on 403 and 404 for SPA client-side routing", () => {
    const template = synth();
    template.hasResourceProperties("AWS::CloudFront::Distribution", {
      DistributionConfig: Match.objectLike({
        CustomErrorResponses: Match.arrayWith([
          Match.objectLike({
            ErrorCode: 403,
            ResponseCode: 200,
            ResponsePagePath: "/index.html",
          }),
          Match.objectLike({
            ErrorCode: 404,
            ResponseCode: 200,
            ResponsePagePath: "/index.html",
          }),
        ]),
      }),
    });
  });

  it("matches the committed template snapshot", () => {
    expect(synth().toJSON()).toMatchSnapshot();
  });
});
