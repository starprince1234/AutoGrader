import { apiRequest } from "../../api/client";
import { newRequestId } from "../../logging/logger";
import type {
  RegistrationCodeAccepted,
  RegistrationCodeRequest,
  RegistrationCompleteRequest,
  RegistrationCreated,
} from "../../api/types";

/**
 * 自助注册的两个调用（契约 `requestRegistrationCode` / `createRegistration`）。
 * 请求/响应类型直接取自生成类型（`api/types.ts`，ADR-0002），本文件不手写字段。
 */

export type {
  RegistrationCodeAccepted,
  RegistrationCodeRequest,
  RegistrationCompleteRequest,
  RegistrationCreated,
};

/** 请求验证码。服务端在发信前已校验用户名/邮箱/学号唯一性与名单归属。 */
export async function requestRegistrationCode(
  payload: RegistrationCodeRequest,
): Promise<RegistrationCodeAccepted> {
  return apiRequest<RegistrationCodeAccepted>("/api/v1/auth/registrations/code", {
    method: "POST",
    body: payload,
    requestId: newRequestId(),
  });
}

/** 用验证码完成注册。成功即建号，可直接用用户名 + 密码登录。 */
export async function completeRegistration(
  payload: RegistrationCompleteRequest,
): Promise<RegistrationCreated> {
  return apiRequest<RegistrationCreated>("/api/v1/auth/registrations", {
    method: "POST",
    body: payload,
    requestId: newRequestId(),
  });
}
