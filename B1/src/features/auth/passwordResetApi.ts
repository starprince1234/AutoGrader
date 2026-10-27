import { apiRequest } from "../../api/client";
import type {
  PasswordResetCodeAccepted,
  PasswordResetCodeRequest,
  PasswordResetCompleteRequest,
} from "../../api/types";
import { newRequestId } from "../../logging/logger";

/**
 * 找回密码的两个公开调用（契约 `requestPasswordResetCode` / `completePasswordReset`）。
 *
 * 类型来自生成类型；请求体与响应体不手写字段（ADR-0002）。
 * 验证码与口令都不写日志、不写浏览器存储。
 */

/** 请求重置验证码。服务端对任意合法邮箱返回同一响应，避免账号枚举。 */
export async function requestPasswordResetCode(
  email: string,
): Promise<PasswordResetCodeAccepted> {
  const payload: PasswordResetCodeRequest = { email };
  return apiRequest<PasswordResetCodeAccepted>("/api/v1/auth/password-reset/code", {
    method: "POST",
    body: payload,
    requestId: newRequestId(),
  });
}

/** 用邮箱验证码设置新密码；成功后服务端会撤销该账号的全部 refresh 会话。 */
export async function completePasswordReset(
  payload: PasswordResetCompleteRequest,
): Promise<void> {
  return apiRequest<void>("/api/v1/auth/password-reset", {
    method: "POST",
    body: payload,
    requestId: newRequestId(),
  });
}
