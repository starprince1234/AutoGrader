import type { MessageKey } from "../../i18n";

/**
 * 注册表单的本地校验（纯函数，可单测）。
 *
 * 前端只做**可读性**校验：格式、必填、两次密码一致。唯一性、名单归属、密码策略、
 * 验证码真实性全部由 B4 判定——前端通过不等于注册成功。
 */

export const STUDENT_NUMBER_PATTERN = /^[0-9]{13}$/;
/** 契约 `requestRegistrationCode.username`：3–32 位、小写字母开头、字母数字点下划线连字符。 */
export const USERNAME_PATTERN = /^[a-z][a-z0-9._-]{2,31}$/;
export const CODE_PATTERN = /^[0-9]{6}$/;
export const PASSWORD_MIN_LENGTH = 8;
export const PASSWORD_MAX_LENGTH = 128;
export const DISPLAY_NAME_MAX_LENGTH = 50;
/** 足够拦住明显的笔误；邮箱是否真实可收信由验证码证明。 */
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export type RegistrationValues = {
  displayName: string;
  studentNumber: string;
  username: string;
  email: string;
  code: string;
  password: string;
  confirmPassword: string;
};

export type RegistrationField = keyof RegistrationValues;
export type RegistrationErrors = Partial<Record<RegistrationField, MessageKey>>;

/** 屏幕上的字段顺序：聚焦"第一个错误"必须按视觉顺序，而不是对象键顺序。 */
export const REGISTRATION_FIELD_ORDER: RegistrationField[] = [
  "displayName",
  "studentNumber",
  "username",
  "email",
  "code",
  "password",
  "confirmPassword",
];

export function validateIdentity(values: RegistrationValues): RegistrationErrors {
  const errors: RegistrationErrors = {};
  const displayName = values.displayName.trim();
  if (displayName === "") errors.displayName = "register.error.displayName";
  else if (displayName.length > DISPLAY_NAME_MAX_LENGTH) {
    errors.displayName = "register.error.displayNameTooLong";
  }
  if (!STUDENT_NUMBER_PATTERN.test(values.studentNumber)) {
    errors.studentNumber = "register.error.studentNumber";
  }
  if (!USERNAME_PATTERN.test(values.username)) errors.username = "register.error.username";
  if (!EMAIL_PATTERN.test(values.email.trim())) errors.email = "register.error.email";
  return errors;
}

export function validateRegistration(values: RegistrationValues): RegistrationErrors {
  const errors = validateIdentity(values);
  if (!CODE_PATTERN.test(values.code)) errors.code = "register.error.code";
  if (values.password.length < PASSWORD_MIN_LENGTH) {
    errors.password = "register.error.password";
  } else if (values.password.length > PASSWORD_MAX_LENGTH) {
    errors.password = "register.error.passwordTooLong";
  }
  if (values.confirmPassword !== values.password) {
    errors.confirmPassword = "register.passwordMismatch";
  }
  return errors;
}

/** 第一个错误的字段（按视觉顺序）。焦点与读屏提示都从这里出发。 */
export function firstErrorField(errors: RegistrationErrors): RegistrationField | null {
  return REGISTRATION_FIELD_ORDER.find((field) => errors[field] !== undefined) ?? null;
}
