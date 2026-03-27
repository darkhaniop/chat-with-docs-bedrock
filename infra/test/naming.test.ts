import {
  cognitoDomainPrefix,
  documentsBucketName,
  siteBucketName,
  stackName,
  tableName,
  vectorBucketName,
  vectorIndexName,
} from "../lib/naming";

describe("naming", () => {
  it("produces stack names as Cwd{Env}{Purpose}Stack", () => {
    expect(stackName("dev", "Data")).toBe("CwdDevDataStack");
    expect(stackName("dev", "Web")).toBe("CwdDevWebStack");
  });

  it("produces the documented resource names (docs/02-data-model.md)", () => {
    expect(tableName("dev")).toBe("cwd-dev");
    expect(documentsBucketName("dev", "123456789012")).toBe("cwd-documents-dev-123456789012");
    expect(vectorBucketName("dev", "123456789012")).toBe("cwd-vectors-dev-123456789012");
    expect(vectorIndexName("01JQABC")).toBe("proj-01JQABC");
    expect(siteBucketName("dev", "123456789012")).toBe("cwd-site-dev-123456789012");
  });

  it("produces a lowercase, hyphenated Cognito domain prefix", () => {
    expect(cognitoDomainPrefix("dev", "123456789012")).toMatch(/^[a-z0-9-]+$/);
  });
});
