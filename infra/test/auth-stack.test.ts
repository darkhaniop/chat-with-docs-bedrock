import { App } from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { CwdAuthStack } from "../lib/auth-stack";

function synth(): Template {
  const app = new App();
  const stack = new CwdAuthStack(app, "TestAuthStack", {
    env2: "dev",
    env: { account: "123456789012", region: "us-east-1" },
    webDistributionDomainName: "d111111abcdef8.cloudfront.net",
  });
  return Template.fromStack(stack);
}

describe("CwdAuthStack", () => {
  it("disables self sign-up and enforces a 12-character password minimum", () => {
    const template = synth();
    template.hasResourceProperties("AWS::Cognito::UserPool", {
      AdminCreateUserConfig: { AllowAdminCreateUserOnly: true },
      Policies: {
        PasswordPolicy: Match.objectLike({ MinimumLength: 12 }),
      },
    });
  });

  it("retains the user pool on stack deletion", () => {
    const template = synth();
    template.hasResource("AWS::Cognito::UserPool", { DeletionPolicy: "Retain" });
  });

  it("configures a public client with PKCE-compatible auth code grant and no secret", () => {
    const template = synth();
    template.hasResourceProperties("AWS::Cognito::UserPoolClient", {
      GenerateSecret: false,
      AllowedOAuthFlows: ["code"],
      CallbackURLs: Match.arrayWith([
        "https://d111111abcdef8.cloudfront.net/callback",
        "http://localhost:5173/callback",
      ]),
    });
  });

  it("creates a Managed Login domain", () => {
    const template = synth();
    template.hasResourceProperties("AWS::Cognito::UserPoolDomain", {
      Domain: "cwd-dev-123456789012",
      ManagedLoginVersion: 2,
    });
  });

  it("matches the committed template snapshot", () => {
    expect(synth().toJSON()).toMatchSnapshot();
  });
});
