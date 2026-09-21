import { t } from "../i18n";
import type { ProblemDetails } from "./types";

/**
 * RFC 9457 Problem Details 的严格判定。
 * B1 只接受结构完整的 ProblemDetails；不完整时视为"服务端未给出可展示的原因"，
 * 由 UI 退化为状态码文案（**不**臆造 detail）。
 */
export function isProblemDetails(value: unknown): value is ProblemDetails {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.type === "string" &&
    typeof candidate.title === "string" &&
    typeof candidate.status === "number" &&
    typeof candidate.detail === "string" &&
    typeof candidate.instance === "string"
  );
}

export class ApiError extends Error {
  readonly status: number;
  readonly problem: ProblemDetails | null;
  readonly requestId: string;
  readonly url: string;

  constructor(args: {
    status: number;
    problem: ProblemDetails | null;
    requestId: string;
    url: string;
    message?: string;
  }) {
    super(args.message ?? `HTTP ${args.status} ${args.url}`);
    this.name = "ApiError";
    this.status = args.status;
    this.problem = args.problem;
    this.requestId = args.requestId;
    this.url = args.url;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  /** 服务端**明确**拒绝（角色或课程作用域不符）。前端的隐藏入口不是安全边界。 */
  get isForbidden(): boolean {
    return this.status === 403;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }

  /** 并发冲突（例如基于过期 `expected_updated_at` 的编辑）。 */
  get isConflict(): boolean {
    return this.status === 409;
  }

  get isUnprocessable(): boolean {
    return this.status === 422;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/** 面向用户的错误摘要：优先服务端 `detail`，否则退回状态码语义。 */
export function describeApiError(error: unknown): string {
  if (isApiError(error)) {
    if (error.isForbidden) return t("common.forbiddenAction");
    if (error.isNotFound) return t("common.notFound");
    if (error.problem?.detail) return error.problem.detail;
    return `${t("common.errorTitle")}（HTTP ${error.status}）`;
  }
  if (error instanceof Error) return error.message;
  return t("common.unknownError");
}
