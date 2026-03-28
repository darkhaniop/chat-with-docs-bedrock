import { describe, expect, it, vi } from "vitest";
import { uploadWithProgress } from "./documents";
import type { CreateDocumentResponse } from "../types";

type ProgressListener = (event: {
  lengthComputable: boolean;
  loaded: number;
  total: number;
}) => void;

class FakeUploadTarget {
  listeners: Record<string, ProgressListener[]> = {};
  addEventListener(type: string, listener: ProgressListener) {
    (this.listeners[type] ??= []).push(listener);
  }
}

// jsdom's real XMLHttpRequest has no way to simulate a presigned S3 PUT deterministically, so
// this substitutes a fake that records handlers and lets the test drive them — the thing under
// test is the promise/progress wiring in `uploadWithProgress`, not XHR itself.
class FakeXhr {
  static instances: FakeXhr[] = [];
  status = 0;
  upload = new FakeUploadTarget();
  openCalledWith: [string, string] | null = null;
  requestHeaders: Record<string, string> = {};
  sentBody: unknown = null;
  private listeners: Record<string, Array<() => void>> = {};

  constructor() {
    FakeXhr.instances.push(this);
  }

  open(method: string, url: string) {
    this.openCalledWith = [method, url];
  }

  setRequestHeader(name: string, value: string) {
    this.requestHeaders[name] = value;
  }

  addEventListener(type: string, listener: () => void) {
    (this.listeners[type] ??= []).push(listener);
  }

  send(body: unknown) {
    this.sentBody = body;
  }

  emitLoad(status: number) {
    this.status = status;
    for (const listener of this.listeners.load ?? []) listener();
  }

  emitError() {
    for (const listener of this.listeners.error ?? []) listener();
  }
}

function installFakeXhr(): void {
  FakeXhr.instances = [];
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
}

const upload: CreateDocumentResponse["upload"] = {
  url: "https://bucket.s3.amazonaws.com/raw/p/d/source.pdf",
  method: "PUT",
  headers: { "Content-Type": "application/pdf" },
  expiresAt: "2026-01-01T00:00:00Z",
};

describe("uploadWithProgress", () => {
  it("resolves when the XHR completes with a 2xx status", async () => {
    installFakeXhr();
    const file = new File(["hello"], "a.pdf", { type: "application/pdf" });

    const promise = uploadWithProgress(upload, file);
    const xhr = FakeXhr.instances.at(-1)!;
    expect(xhr.openCalledWith).toEqual(["PUT", upload.url]);
    xhr.emitLoad(200);

    await expect(promise).resolves.toBeUndefined();
  });

  it("reports fractional progress via onProgress", async () => {
    installFakeXhr();
    const file = new File(["hello"], "a.pdf", { type: "application/pdf" });
    const onProgress = vi.fn();

    const promise = uploadWithProgress(upload, file, onProgress);
    const xhr = FakeXhr.instances.at(-1)!;
    for (const listener of xhr.upload.listeners.progress ?? []) {
      listener({ lengthComputable: true, loaded: 50, total: 200 });
    }
    expect(onProgress).toHaveBeenCalledWith(0.25);

    xhr.emitLoad(200);
    await promise;
  });

  it("rejects when the XHR completes with a non-2xx status", async () => {
    installFakeXhr();
    const file = new File(["hello"], "a.pdf", { type: "application/pdf" });

    const promise = uploadWithProgress(upload, file);
    FakeXhr.instances.at(-1)!.emitLoad(403);

    await expect(promise).rejects.toThrow("upload failed: 403");
  });

  it("rejects on a network error", async () => {
    installFakeXhr();
    const file = new File(["hello"], "a.pdf", { type: "application/pdf" });

    const promise = uploadWithProgress(upload, file);
    FakeXhr.instances.at(-1)!.emitError();

    await expect(promise).rejects.toThrow("network error");
  });
});
