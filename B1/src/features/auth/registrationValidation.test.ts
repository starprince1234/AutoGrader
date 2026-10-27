import { describe, expect, it } from "vitest";
import {
  firstErrorField,
  validateIdentity,
  validateRegistration,
  type RegistrationValues,
} from "./registrationValidation";

const VALID: RegistrationValues = {
  displayName: "张三",
  studentNumber: "2023123456789",
  username: "zhangsan",
  email: "zhangsan@example.com",
  code: "123456",
  password: "password1",
  confirmPassword: "password1",
};

function withValues(overrides: Partial<RegistrationValues>): RegistrationValues {
  return { ...VALID, ...overrides };
}

describe("注册身份字段的本地校验（格式层，最终由 B4 判定）", () => {
  it("合法身份没有错误", () => {
    expect(validateIdentity(VALID)).toEqual({});
  });

  it("学号必须是 13 位数字：12 位、14 位与非数字都被拦下", () => {
    for (const studentNumber of ["202312345678", "20231234567890", "2023abcdefghi", "2023 12345678"]) {
      expect(validateIdentity(withValues({ studentNumber })).studentNumber).toBe(
        "register.error.studentNumber",
      );
    }
  });

  it("用户名必须 3–32 位、以字母开头且只用小写字母/数字/点/_/-", () => {
    for (const username of ["ab", "张三", "ZhangSan", "1abc", "abc def", "a".repeat(33)]) {
      expect(validateIdentity(withValues({ username })).username).toBe(
        "register.error.username",
      );
    }
    expect(validateIdentity(withValues({ username: "a" })).username).toBeDefined();
    expect(validateIdentity(withValues({ username: "a".repeat(32) })).username).toBeUndefined();
  });

  it("姓名必填且不超过 50 字符", () => {
    expect(validateIdentity(withValues({ displayName: "   " })).displayName).toBe(
      "register.error.displayName",
    );
    expect(
      validateIdentity(withValues({ displayName: "名".repeat(51) })).displayName,
    ).toBe("register.error.displayNameTooLong");
    expect(validateIdentity(withValues({ displayName: " 张三 " })).displayName).toBeUndefined();
  });

  it("邮箱必须是基本可读格式", () => {
    for (const email of ["", "name", "name@", "name@example", "name example@example.com"]) {
      expect(validateIdentity(withValues({ email })).email).toBe("register.error.email");
    }
    expect(validateIdentity(withValues({ email: "name@example.com" })).email).toBeUndefined();
  });
});

describe("完整注册表单校验", () => {
  it("合法表单没有错误", () => {
    expect(validateRegistration(VALID)).toEqual({});
  });

  it("验证码必须是 6 位数字", () => {
    for (const code of ["", "12345", "1234567", "12345a"]) {
      expect(validateRegistration(withValues({ code })).code).toBe("register.error.code");
    }
  });

  it("密码至少 8 位、最多 128 位（契约边界）", () => {
    expect(validateRegistration(withValues({ password: "1234567", confirmPassword: "1234567" })).password).toBe(
      "register.error.password",
    );
    const long = "a".repeat(129);
    expect(
      validateRegistration(withValues({ password: long, confirmPassword: long })).password,
    ).toBe("register.error.passwordTooLong");
  });

  it("两次密码不一致必须报在确认密码字段", () => {
    const errors = validateRegistration(withValues({ confirmPassword: "password2" }));
    expect(errors.confirmPassword).toBe("register.passwordMismatch");
    expect(errors.password).toBeUndefined();
  });

  it("聚焦字段按视觉顺序取第一个错误", () => {
    expect(
      firstErrorField({
        password: "register.error.password",
        studentNumber: "register.error.studentNumber",
        email: "register.error.email",
      }),
    ).toBe("studentNumber");
    expect(firstErrorField({})).toBeNull();
  });
});
