import { apiRequest } from "../../api/client";
import { describeApiError, isApiError } from "../../api/problem";
import { newRequestId } from "../../logging/logger";

/**
 * 自助注册的两个调用（契约 `requestRegistrationCode` / `createRegistration`）。
 *
 * 类型**手写**而不取自 `api/generated/b4.ts`：那是 `pnpm api:generate` 的产物，本机没有
 * `node_modules`，无法重新生成。手写类型只覆盖这两个请求/响应，不影响登录链路；
 * 一旦在具备依赖的环境跑过生成器，可以把这里换成 `B4Operations[...]`。
 */

export type RegistrationCodePayload = {
  username: string;
  email: string;
  student_number: string;
  display_name: string;
};

export type RegistrationCodeAccepted = {
  expires_in_seconds: number;
  resend_after_seconds: number;
};

export type RegistrationCompletePayload = {
  email: string;
  code: string;
  password: string;
};

export type RegistrationCreated = {
  user_id: string;
  username: string;
};

/** 请求验证码。服务端在发信前已校验用户名/邮箱/学号唯一性与名单归属。 */
export async function requestRegistrationCode(
  payload: RegistrationCodePayload,
): Promise<RegistrationCodeAccepted> {
  return apiRequest<RegistrationCodeAccepted>("/api/v1/auth/registrations/code", {
    method: "POST",
    body: payload,
    requestId: newRequestId(),
  });
}

/** 用验证码完成注册。成功即建号，可直接用用户名 + 密码登录。 */
export async function completeRegistration(
  payload: RegistrationCompletePayload,
): Promise<RegistrationCreated> {
  return apiRequest<RegistrationCreated>("/api/v1/auth/registrations", {
    method: "POST",
    body: payload,
    requestId: newRequestId(),
  });
}

/** 注册页的错误文案：503 单独说明，其余交给统一的服务端 detail。 */
export function registrationErrorText(error: unknown): string {
  if (isApiError(error) && error.status === 503) {
    return "邮件服务当前不可用，验证码无法发送；请稍后重试或联系课程管理员。";
  }
  return describeApiError(error);
}
