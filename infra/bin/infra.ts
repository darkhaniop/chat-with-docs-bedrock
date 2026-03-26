#!/usr/bin/env node
import "source-map-support/register";
import { App } from "aws-cdk-lib";

// Stacks are added in Phase 1 (docs/10-roadmap.md, docs/09-operations.md#stacks), parameterised
// by `-c env=`. This entry point exists so `cdk synth`/`npm test` have something to run against
// during Phase 0 scaffolding.
const app = new App();
const env = app.node.tryGetContext("env") ?? "dev";
void env;
