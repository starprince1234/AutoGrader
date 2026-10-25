import type { Role } from "../../api/types";
import { t } from "../../i18n";

/**
 * 创建用户表单的**规则**（`POST /api/v1/users`）。
 *
 * 放在组件文件之外，是因为这些常量与校验函数要能被单独引用与单独测试：字段上限取自 B4
 * 的常量（`users.MAX_*`），不是前端另立的一套规则。前端校验只负责"别把必然 422 的输入发出去"，
 * 服务端仍是最终判定方。
 */

export const MAX_DISPLAY_NAME = 200;
export const MAX_USERNAME = 200;
export const MAX_STUDENT_NUMBER = 64;
export const MAX_PASSWORD = 400;

export type CreateUserFieldName = "displayName" | "username" | "studentNumber" | "password";

export type CreateUserFieldErrors = Partial<Record<CreateUserFieldName, string>>;

/** 每个字段的 DOM id：既用于 `<label for>`，也用于提交失败时把焦点放到第一个错误字段。 */
export const CREATE_USER_FIELD_IDS: Record<CreateUserFieldName, string> = {
  displayName: "create-user-display-name",
  username: "create-user-username",
  studentNumber: "create-user-student-number",
  password: "create-user-password",
};

export type CreateUserFormState = {
  displayName: string;
  username: string;
  studentNumber: string;
  role: Role;
  password: string;
};

export const EMPTY_CREATE_USER_FORM: CreateUserFormState = {
  displayName: "",
  username: "",
  studentNumber: "",
  role: "student",
  password: "",
};

/** 客户端预检。返回字段错误表；空对象表示可以提交。 */
export function validateCreateUser(form: CreateUserFormState): CreateUserFieldErrors {
  const errors: CreateUserFieldErrors = {};
  const displayName = form.displayName.trim();
  const username = form.username.trim();
  const studentNumber = form.studentNumber.trim();

  if (displayName === "") errors.displayName = t("users.displayNameRequired");
  else if (displayName.length > MAX_DISPLAY_NAME) errors.displayName = t("users.displayNameTooLong");

  if (username === "") errors.username = t("users.usernameRequired");
  else if (username.length > MAX_USERNAME) errors.username = t("users.usernameTooLong");

  if (studentNumber.length > MAX_STUDENT_NUMBER) {
    errors.studentNumber = t("users.studentNumberTooLong");
  }

  if (form.password === "") errors.password = t("users.passwordRequired");
  else if (form.password.length > MAX_PASSWORD) errors.password = t("users.passwordTooLong");

  return errors;
}
