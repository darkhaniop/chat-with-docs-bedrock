import { App } from "aws-cdk-lib";

// Stacks arrive in Phase 1 (docs/10-roadmap.md). This pins the toolchain — CDK, TypeScript,
// jest — until there is a stack to assert against.
describe("cdk app scaffolding", () => {
  it("constructs an App without throwing", () => {
    expect(() => new App()).not.toThrow();
  });
});
