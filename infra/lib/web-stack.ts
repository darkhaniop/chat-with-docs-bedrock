import { CfnOutput, RemovalPolicy, Stack, type StackProps } from "aws-cdk-lib";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3deploy from "aws-cdk-lib/aws-s3-deployment";
import type { Construct } from "constructs";
import { siteBucketName } from "./naming";

export interface CwdWebStackProps extends StackProps {
  readonly env2: string;
  /**
   * Directory deployed to the site bucket — `web/dist` once built, or the checked-in
   * placeholder before the first `scripts/build-web.sh` run
   * (docs/06-frontend.md#build-and-deploy). Callers decide which, rather than this construct
   * probing the filesystem itself, so a plain `cdk synth`/jest snapshot never depends on
   * whatever a developer happened to build locally beforehand.
   */
  readonly siteContentDir: string;
}

/**
 * docs/09-operations.md#stacks: private site bucket, CloudFront with OAC, SPA fallback for
 * 403/404, `BucketDeployment`. Changes on every frontend build.
 */
export class CwdWebStack extends Stack {
  public readonly distribution: cloudfront.Distribution;
  public readonly siteBucket: s3.Bucket;

  constructor(scope: Construct, id: string, props: CwdWebStackProps) {
    super(scope, id, props);

    this.siteBucket = new s3.Bucket(this, "SiteBucket", {
      bucketName: siteBucketName(props.env2, this.account),
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    this.distribution = new cloudfront.Distribution(this, "Distribution", {
      defaultRootObject: "index.html",
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(this.siteBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      },
      // SPA fallback: a client-side route with no matching S3 key comes back as 403 (OAC) or
      // 404 and should still serve index.html so the router can take over.
      errorResponses: [
        { httpStatus: 403, responseHttpStatus: 200, responsePagePath: "/index.html" },
        { httpStatus: 404, responseHttpStatus: 200, responsePagePath: "/index.html" },
      ],
    });

    new s3deploy.BucketDeployment(this, "Deployment", {
      sources: [s3deploy.Source.asset(props.siteContentDir)],
      destinationBucket: this.siteBucket,
      distribution: this.distribution,
      distributionPaths: ["/index.html", "/"],
      cacheControl: [s3deploy.CacheControl.noCache()],
      prune: true,
    });

    new CfnOutput(this, "DistributionDomainName", { value: this.distribution.domainName });
  }
}
