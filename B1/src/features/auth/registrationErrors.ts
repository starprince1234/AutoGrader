import { describeApiError, isApiError } from "../../api/problem";
import { t } from "../../i18n";
import type { RegistrationErrors } from "./registrationValidation";

/**
 * 把服务端的注册/发码失败翻译成"哪一个字段错了 + 一句可读原因"。
 *
 * B4 的 `detail` 是中文可读文本（例如「该用户名已被使用。」「验证码无效或已过期,请重新获取。」），
 * 因此按关键词映射到字段；映射不到时**原样显示**服务端 detail，不臆造原因。
 * 密码、验证码永远不进日志，也不出现在错误文本里。
 */

export type RegistrationPhase = "code" | "register";

export type RegistrationFailure = {
  fieldErrors: RegistrationErrors;
  /** 表单级原因（映射不到具体字段时）；与字段错误二选一或并存。 */
  message: string | null;
  requestId: string | null;
  /**
   * 创建失败后清空密码与验证码并要求重新验证（验证码可能已被消费/过期）。
   * 发码失败不清空任何输入。
   */
  clearSecrets: boolean;
};

export function describeRegistrationFailure(
  error: unknown,
  phase: RegistrationPhase,
): RegistrationFailure {
  const empty = (message: string | null): RegistrationFailure => ({
    fieldErrors: {},
    message,
    requestId: null,
    clearSecrets: phase === "register",
  });

  if (!isApiError(error)) {
    return empty(
      phase === "register" ? t("register.error.unconfirmed") : t("register.error.network"),
    );
  }

  const detail = error.problem?.detail ?? "";
  const fieldErrors: RegistrationErrors = {};
  let message: string | null = null;

  if (error.isConflict) {
    // 「该用户名、邮箱或学号已被占用。」同时点名多个字段：定位到其中任意一个都会误导，
    // 因此保留为表单级原因。
    const mentioned = ["用户名", "邮箱", "学号"].filter((field) => detail.includes(field)).length;
    if (mentioned > 1) message = t("register.error.conflict");
    else if (detail.includes("用户名")) fieldErrors.username = "register.error.usernameTaken";
    else if (detail.includes("邮箱")) fieldErrors.email = "register.error.emailTaken";
    else if (detail.includes("学号")) fieldErrors.studentNumber = "register.error.studentNumberTaken";
    else message = t("register.error.conflict");
  } else if (error.status === 422) {
    if (detail.includes("验证码")) {
      fieldErrors.code = detail.includes("不正确")
        ? "register.error.codeMismatch"
        : "register.error.codeInvalid";
    } else if (detail.includes("名单")) {
      // 名单归属是身份级结论，不属于某个输入框的格式错误。
      message = detail;
    } else if (detail.includes("密码")) {
      fieldErrors.password = detail.includes("最长")
        ? "register.error.passwordTooLong"
        : "register.error.password";
    } else if (detail.includes("学号")) {
      fieldErrors.studentNumber = "register.error.studentNumber";
    } else if (detail.includes("姓名")) {
      fieldErrors.displayName = "register.error.displayName";
    } else {
      message = detail || describeApiError(error);
    }
  } else if (error.status === 429) {
    if (phase === "register") fieldErrors.code = "register.error.codeTooMany";
    else message = detail || t("register.error.codeTooMany");
  } else if (error.status === 503) {
    message = t("register.error.mailUnavailable");
  } else {
    // 服务端 5xx 的 detail 往往是给运维读的：认证入口只给用户可行动的话 + request_id。
    message =
      error.status >= 500 ? t("register.error.unconfirmed") : describeApiError(error);
  }

  return {
    fieldErrors,
    message,
    requestId: error.requestId,
    clearSecrets: phase === "register",
  };
}
