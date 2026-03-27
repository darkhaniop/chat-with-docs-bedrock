import { CfnOutput, Duration, RemovalPolicy, Stack, type StackProps } from "aws-cdk-lib";
import * as cognito from "aws-cdk-lib/aws-cognito";
import type { Construct } from "constructs";
import { cognitoDomainPrefix } from "./naming";

export interface CwdAuthStackProps extends StackProps {
  readonly env2: string;
  /** CloudFront domain from `CwdWebStack`, used to build the OIDC callback/logout URLs. */
  readonly webDistributionDomainName: string;
}

/**
 * User pool, app client, Managed Login domain. Rarely changes; `RemovalPolicy.RETAIN` — recreating
 * the pool means recreating every user.
 */
export class CwdAuthStack extends Stack {
  public readonly userPool: cognito.UserPool;
  public readonly userPoolClient: cognito.UserPoolClient;
  public readonly domain: cognito.UserPoolDomain;

  constructor(scope: Construct, id: string, props: CwdAuthStackProps) {
    super(scope, id, props);

    const callbackUrls = [
      `https://${props.webDistributionDomainName}/callback`,
      "http://localhost:5173/callback",
    ];
    const logoutUrls = [`https://${props.webDistributionDomainName}/`, "http://localhost:5173/"];

    // Self sign-up disabled, 12+ char passwords, no forced rotation, MFA optional (TOTP).
    this.userPool = new cognito.UserPool(this, "UserPool", {
      userPoolName: `cwd-${props.env2}`,
      selfSignUpEnabled: false,
      signInAliases: { email: true },
      standardAttributes: { email: { required: true, mutable: true } },
      passwordPolicy: {
        minLength: 12,
        requireLowercase: false,
        requireUppercase: false,
        requireDigits: false,
        requireSymbols: false,
        tempPasswordValidity: Duration.days(7),
      },
      mfa: cognito.Mfa.OPTIONAL,
      mfaSecondFactor: { otp: true, sms: false },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    // Public client, PKCE, no secret.
    this.userPoolClient = this.userPool.addClient("WebClient", {
      userPoolClientName: `cwd-${props.env2}-web`,
      generateSecret: false,
      authFlows: { userSrp: false, adminUserPassword: false, custom: false, userPassword: false },
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
        callbackUrls,
        logoutUrls,
      },
      preventUserExistenceErrors: true,
      accessTokenValidity: Duration.hours(1),
      idTokenValidity: Duration.hours(1),
      refreshTokenValidity: Duration.days(30),
    });

    // Cognito: Managed Login (hosted UI), not the classic hosted UI.
    this.domain = this.userPool.addDomain("ManagedLoginDomain", {
      cognitoDomain: { domainPrefix: cognitoDomainPrefix(props.env2, this.account) },
      managedLoginVersion: cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
    });

    // Required for NEWER_MANAGED_LOGIN specifically: without a branding style assigned to the
    // app client, the hosted login pages 403 with "Login pages unavailable. Please contact an
    // administrator." — the L2 `addDomain` only creates the domain, not this.
    new cognito.CfnManagedLoginBranding(this, "ManagedLoginBranding", {
      userPoolId: this.userPool.userPoolId,
      clientId: this.userPoolClient.userPoolClientId,
      useCognitoProvidedValues: true,
    });

    new CfnOutput(this, "UserPoolId", { value: this.userPool.userPoolId });
    new CfnOutput(this, "UserPoolClientId", { value: this.userPoolClient.userPoolClientId });
    new CfnOutput(this, "UserPoolIssuer", {
      value: `https://cognito-idp.${this.region}.amazonaws.com/${this.userPool.userPoolId}`,
    });
    new CfnOutput(this, "UserPoolDomainUrl", {
      value: `https://${cognitoDomainPrefix(props.env2, this.account)}.auth.${this.region}.amazoncognito.com`,
    });
  }
}
