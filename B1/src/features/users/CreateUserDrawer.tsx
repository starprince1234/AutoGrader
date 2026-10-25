import { useState, type FormEvent } from "react";
import { describeApiError, isApiError } from "../../api/problem";
import type { CreateUserRequest, Role } from "../../api/types";
import { t } from "../../i18n";
import { Banner, ErrorState, Field, Modal } from "../../ui/components";
import { ASSIGNABLE_ROLES, useCreateUser } from "./useUsers";
import {
  CREATE_USER_FIELD_IDS,
  EMPTY_CREATE_USER_FORM,
  validateCreateUser,
  type CreateUserFieldErrors,
  type CreateUserFieldName,
  type CreateUserFormState,
} from "./userForm";

/**
 * 创建用户（P14 的 Drawer 表单，`POST /api/v1/users`）。
 *
 * 三条纪律：
 * 1. **服务端是最终判定方**。前端校验只负责"别把必然 422 的输入发出去"，长度上限取自
 *    B4 的常量（`userForm.ts`），不另立规则。
 * 2. **初始密码不进任何可持久化的位置**：它只活在这个组件的内存里，提交后立即清空，
 *    创建响应也不回显它（响应只有 `user_id`）。
 * 3. 失败后**保留非秘密字段、清空密码**：重新输入密码比重新填姓名/学号便宜，而把密码留在
 *    输入框里只会在下一次提交时把它带到一个可能已经不同意的请求上。
 */
export function CreateUserDrawer({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (userId: string) => void;
}) {
  const [form, setForm] = useState<CreateUserFormState>(EMPTY_CREATE_USER_FORM);
  const [errors, setErrors] = useState<CreateUserFieldErrors>({});
  const createUser = useCreateUser();

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (createUser.isPending) return;
    const found = validateCreateUser(form);
    setErrors(found);
    const firstInvalid = (Object.keys(CREATE_USER_FIELD_IDS) as CreateUserFieldName[]).find(
      (name) => found[name],
    );
    if (firstInvalid) {
      document.getElementById(CREATE_USER_FIELD_IDS[firstInvalid])?.focus();
      return;
    }
    const body: CreateUserRequest = {
      display_name: form.displayName.trim(),
      username: form.username.trim(),
      student_number: form.studentNumber.trim() === "" ? null : form.studentNumber.trim(),
      roles: [form.role],
      password: form.password,
    };
    createUser.mutate(body, {
      onSuccess: (created) => {
        onCreated(created.user_id);
      },
      onError: (error) => {
        // 密码一律清空；其他字段保留，用户可以只改冲突项再提交一次。
        setForm((current) => ({ ...current, password: "" }));
        const conflict = conflictField(error);
        if (conflict === "username") setErrors({ username: t("users.conflictUsername") });
        else if (conflict === "studentNumber") {
          setErrors({ studentNumber: t("users.conflictStudentNumber") });
        }
      },
    });
  };

  return (
    <Modal title={t("users.create")} onClose={onClose} testId="create-user-modal">
      <form className="stack" data-testid="create-user-form" onSubmit={submit} noValidate>
        <p className="muted">{t("users.createHint")}</p>

        <Field
          label={t("users.displayName")}
          htmlFor={CREATE_USER_FIELD_IDS.displayName}
          error={errors.displayName}
        >
          <input
            id={CREATE_USER_FIELD_IDS.displayName}
            data-testid={CREATE_USER_FIELD_IDS.displayName}
            value={form.displayName}
            autoComplete="off"
            aria-invalid={errors.displayName ? true : undefined}
            onChange={(event) => setForm({ ...form, displayName: event.target.value })}
          />
        </Field>

        <Field
          label={t("users.username")}
          htmlFor={CREATE_USER_FIELD_IDS.username}
          error={errors.username}
        >
          <input
            id={CREATE_USER_FIELD_IDS.username}
            data-testid={CREATE_USER_FIELD_IDS.username}
            value={form.username}
            autoComplete="off"
            aria-invalid={errors.username ? true : undefined}
            onChange={(event) => setForm({ ...form, username: event.target.value })}
          />
        </Field>

        <Field
          label={t("users.studentNumberOptional")}
          htmlFor={CREATE_USER_FIELD_IDS.studentNumber}
          error={errors.studentNumber}
        >
          <input
            id={CREATE_USER_FIELD_IDS.studentNumber}
            data-testid={CREATE_USER_FIELD_IDS.studentNumber}
            value={form.studentNumber}
            autoComplete="off"
            aria-invalid={errors.studentNumber ? true : undefined}
            onChange={(event) => setForm({ ...form, studentNumber: event.target.value })}
          />
        </Field>

        <Field label={t("users.roles")} htmlFor="create-user-role">
          <select
            id="create-user-role"
            data-testid="create-user-role"
            value={form.role}
            onChange={(event) => setForm({ ...form, role: event.target.value as Role })}
          >
            {ASSIGNABLE_ROLES.map((role) => (
              <option key={role} value={role}>
                {t(`role.${role}`)}
              </option>
            ))}
          </select>
        </Field>

        <Field
          label={t("users.initialPassword")}
          htmlFor={CREATE_USER_FIELD_IDS.password}
          error={errors.password}
        >
          <input
            id={CREATE_USER_FIELD_IDS.password}
            data-testid={CREATE_USER_FIELD_IDS.password}
            type="password"
            value={form.password}
            autoComplete="new-password"
            aria-invalid={errors.password ? true : undefined}
            onChange={(event) => setForm({ ...form, password: event.target.value })}
          />
        </Field>

        {createUser.isError ? <CreateError error={createUser.error} /> : null}

        <div className="inline">
          <button
            type="submit"
            className="button"
            data-testid="create-user-submit"
            disabled={createUser.isPending}
          >
            {createUser.isPending ? t("users.creating") : t("users.create")}
          </button>
          <button type="button" className="button button--ghost" onClick={onClose}>
            {t("common.cancel")}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/**
 * 把 409 的 `detail` 归到具体字段。
 *
 * B4 用同一状态码表达两种唯一约束（用户名、学号），两者的修复动作不同（改用户名还是改学号），
 * 所以不能只弹一条笼统的"冲突"。匹配的是 B4 现有文案；匹配不上就退回整表提示，不猜。
 */
function conflictField(error: unknown): CreateUserFieldName | null {
  if (!isApiError(error) || !error.isConflict) return null;
  const detail = (error.problem?.detail ?? "").toLowerCase();
  if (detail.includes("username")) return "username";
  if (detail.includes("student number") || detail.includes("student_number")) return "studentNumber";
  return null;
}

/** 创建失败的可读反馈：403 与 409 各有专属文案，其余交给 `ErrorState`（带 request_id）。 */
function CreateError({ error }: { error: unknown }) {
  if (!isApiError(error)) return <ErrorState error={error} testId="create-user-error" />;
  if (error.isForbidden) {
    return (
      <Banner tone="error" testId="create-user-forbidden">
        {t("users.createForbidden")}
      </Banner>
    );
  }
  if (error.isConflict) {
    return (
      <Banner tone="error" testId="create-user-conflict">
        {describeApiError(error)}
      </Banner>
    );
  }
  if (error.isUnprocessable) {
    return (
      <Banner tone="error" testId="create-user-invalid">
        {t("users.createRejected", { detail: describeApiError(error) })}
      </Banner>
    );
  }
  return <ErrorState error={error} testId="create-user-error" />;
}
