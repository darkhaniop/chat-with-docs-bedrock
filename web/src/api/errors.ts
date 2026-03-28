import type { ApiErrorBody } from "./types";

/** docs/05-api-contracts.md#error-shape, thrown by `apiJson` on any non-2xx response. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(status: number, body: ApiErrorBody) {
    super(body.error.message);
    this.status = status;
    this.code = body.error.code;
    this.details = body.error.details ?? {};
  }
}
