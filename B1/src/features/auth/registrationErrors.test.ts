import { describe, expect, it } from "vitest";
import { ApiError } from "../../api/problem";
import { describeRegistrationFailure } from "./registrationErrors";

function apiError(status: number, detail: string): ApiError {
  return new ApiError({
    status,
    requestId: "req-test",
    url: "/api/v1/auth/registrations",
    problem: {
      type: `https://autograder.local/problems/${status}`,
      title: "Error",
      status,
      detail,
      instance: "/api/v1/auth/registrations",
    },
  });
}

describe("注册失败原因映射（只展示服务端给出的可读原因）", () => {
  it("409 用户名冲突映射到用户名字段", () => {
    const failure = describeRegistrationFailure(apiError(409, "该用户名已被使用。"), "register");
    expect(failure.fieldErrors.username).toBe("register.error.usernameTaken");
    expect(failure.clearSecrets).toBe(true);
  });

  it("409 邮箱与学号冲突分别映射到对应字段", () => {
    expect(
      describeRegistrationFailure(apiError(409, "该邮箱已被其他账号绑定。"), "register")
        .fieldErrors.email,
    ).toBe("register.error.emailTaken");
    expect(
      describeRegistrationFailure(apiError(409, "该学号已被注册。"), "register").fieldErrors
        .studentNumber,
    ).toBe("register.error.studentNumberTaken");
  });

  it("无法细分的冲突保留为表单级原因", () => {
    const failure = describeRegistrationFailure(
      apiError(409, "该用户名、邮箱或学号已被占用。"),
      "register",
    );
    expect(failure.fieldErrors).toEqual({});
    expect(failure.message).toContain("用户名");
    expect(failure.requestId).toBe("req-test");
  });

  it("422 验证码错误落在验证码字段，并区分无效与不正确", () => {
    expect(
      describeRegistrationFailure(apiError(422, "验证码无效或已过期,请重新获取。"), "register")
        .fieldErrors.code,
    ).toBe("register.error.codeInvalid");
    expect(
      describeRegistrationFailure(apiError(422, "验证码不正确。"), "register").fieldErrors.code,
    ).toBe("register.error.codeMismatch");
  });

  it("422 名单归属失败保留服务端原文作为表单级原因", () => {
    const failure = describeRegistrationFailure(
      apiError(422, "该身份已不在注册名单中,请联系课程管理员。"),
      "register",
    );
    expect(failure.message).toContain("名单");
    expect(failure.fieldErrors).toEqual({});
  });

  it("429 发码失败显示服务端给出的等待原因", () => {
    const failure = describeRegistrationFailure(apiError(429, "请 42 秒后再获取验证码。"), "code");
    expect(failure.message).toBe("请 42 秒后再获取验证码。");
  });

  it("503 发码失败说明邮件通道不可用，且不清空身份字段", () => {
    const failure = describeRegistrationFailure(
      apiError(503, "the registration mail channel"),
      "code",
    );
    expect(failure.message).toContain("邮件服务");
    expect(failure.clearSecrets).toBe(false);
  });

  it("网络失败与未知错误不冒充注册成功", () => {
    const failure = describeRegistrationFailure(new TypeError("Failed to fetch"), "register");
    expect(failure.message).toContain("无法确认");
    expect(failure.clearSecrets).toBe(true);
    const codeFailure = describeRegistrationFailure(new TypeError("Failed to fetch"), "code");
    expect(codeFailure.message).toContain("无法连接");
  });

  it("5xx 的技术 detail 不直接抛给用户，但保留 request_id", () => {
    const failure = describeRegistrationFailure(
      apiError(500, "Traceback: connection refused to smtp.internal"),
      "register",
    );
    expect(failure.message).toContain("无法确认");
    expect(failure.message).not.toContain("Traceback");
    expect(failure.requestId).toBe("req-test");
  });
});
