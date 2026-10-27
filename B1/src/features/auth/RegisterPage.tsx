import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { t, type MessageKey } from "../../i18n";
import { Banner, Field } from "../../ui/components";
import { completeRegistration, requestRegistrationCode } from "./registrationApi";
import { describeRegistrationFailure } from "./registrationErrors";
import {
  firstErrorField,
  validateIdentity,
  validateRegistration,
  type RegistrationErrors,
  type RegistrationField,
  type RegistrationValues,
} from "./registrationValidation";

const EMPTY_VALUES: RegistrationValues = {
  displayName: "",
  studentNumber: "",
  username: "",
  email: "",
  code: "",
  password: "",
  confirmPassword: "",
};

type Failure = { message: string; requestId: string | null };

/**
 * 注册页（`POST /api/v1/auth/registrations/code` → `POST /api/v1/auth/registrations`）。
 *
 * 两步式是刻意的：验证码与"这次注册意图"绑定，先发码、再带码提交。
 * 前端只做格式校验（学号 13 位、用户名形状、两次密码一致），唯一性、名单归属、
 * 口令策略与验证码真实性全部由 B4 判定——**服务端成功响应之前不宣称注册成功**。
 *
 * 失败处理：身份字段（姓名/学号/用户名/邮箱）在失败后保留，密码与验证码清空并要求
 * 重新验证；验证码不写日志、URL 或浏览器存储。
 */
export function RegisterPage() {
  const navigate = useNavigate();
  const [values, setValues] = useState<RegistrationValues>(EMPTY_VALUES);
  const [errors, setErrors] = useState<RegistrationErrors>({});
  const [failure, setFailure] = useState<Failure | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const fieldRefs = useRef(new Map<RegistrationField, HTMLInputElement | null>());
  const submittingRef = useRef(false);
  const sendingRef = useRef(false);

  // 重发冷却按秒递减；组件卸载后不再写状态。
  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((current) => current - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  const update = (field: RegistrationField, value: string) => {
    setValues((current) => ({ ...current, [field]: value }));
    setErrors((current) => {
      if (current[field] === undefined) return current;
      const next = { ...current };
      delete next[field];
      return next;
    });
  };

  const focusFirstError = (nextErrors: RegistrationErrors) => {
    const field = firstErrorField(nextErrors);
    if (field) fieldRefs.current.get(field)?.focus();
  };

  const applyFailure = (nextErrors: RegistrationErrors, failureView: Failure | null) => {
    setErrors(nextErrors);
    setFailure(failureView);
    focusFirstError(nextErrors);
  };

  const sendCode = async () => {
    if (sendingRef.current || cooldown > 0) return;
    setFailure(null);
    setNotice(null);
    const identityErrors = validateIdentity(values);
    if (Object.keys(identityErrors).length > 0) {
      focusFirstError(identityErrors);
      setErrors(identityErrors);
      return;
    }
    setErrors({});
    sendingRef.current = true;
    setSending(true);
    try {
      const accepted = await requestRegistrationCode({
        username: values.username,
        email: values.email.trim(),
        student_number: values.studentNumber,
        display_name: values.displayName.trim(),
      });
      setNotice(t("register.codeSent", { seconds: accepted.expires_in_seconds }));
      setCooldown(accepted.resend_after_seconds);
    } catch (error) {
      const view = describeRegistrationFailure(error, "code");
      // 字段级原因已经贴在对应输入框下；只有映射不到字段的原因才需要表单级横幅。
      applyFailure(
        view.fieldErrors,
        view.message ? { message: view.message, requestId: view.requestId } : null,
      );
    } finally {
      sendingRef.current = false;
      setSending(false);
    }
  };

  const submit = async () => {
    if (submittingRef.current) return;
    setFailure(null);
    setNotice(null);
    const nextErrors = validateRegistration(values);
    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors);
      focusFirstError(nextErrors);
      return;
    }
    setErrors({});
    submittingRef.current = true;
    setSubmitting(true);
    try {
      await completeRegistration({
        email: values.email.trim(),
        code: values.code,
        password: values.password,
      });
      // 注册成功后不自动建立会话：回到登录页，用刚设置的口令登录一次。
      await navigate({ to: "/login", search: { registered: "created" }, replace: true });
    } catch (error) {
      const view = describeRegistrationFailure(error, "register");
      if (view.clearSecrets) {
        setValues((current) => ({
          ...current,
          code: "",
          password: "",
          confirmPassword: "",
        }));
      }
      applyFailure(view.fieldErrors, {
        message: view.message ?? t("register.error.unconfirmed"),
        requestId: view.requestId,
      });
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  const ref = (field: RegistrationField) => (element: HTMLInputElement | null) => {
    fieldRefs.current.set(field, element);
  };

  return (
    <section className="auth-card auth-card--wide" data-testid="register-panel">
      <h1 className="auth-card__title">{t("register.title")}</h1>
      <form
        className="auth-form"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <section className="auth-section" aria-labelledby="register-identity-section">
          <h2 className="auth-section__title" id="register-identity-section">
            {t("register.identitySection")}
          </h2>

          <Field label={t("register.displayName")} htmlFor="register-display-name" error={fieldErrorMessage(errors.displayName)}>
            <input
              ref={ref("displayName")}
              id="register-display-name"
              name="name"
              autoComplete="name"
              autoFocus
              placeholder={t("register.displayNamePlaceholder")}
              value={values.displayName}
              onChange={(event) => update("displayName", event.target.value)}
            />
          </Field>

          <Field
            label={t("register.studentNumber")}
            htmlFor="register-student-number"
            error={fieldErrorMessage(errors.studentNumber)}
            hint={t("register.studentNumberHint")}
          >
            <input
              ref={ref("studentNumber")}
              id="register-student-number"
              name="student-number"
              inputMode="numeric"
              placeholder={t("register.studentNumberPlaceholder")}
              value={values.studentNumber}
              onChange={(event) => update("studentNumber", event.target.value.replace(/\s/g, ""))}
            />
          </Field>

          <Field label={t("register.username")} htmlFor="register-username" error={fieldErrorMessage(errors.username)}>
            <input
              ref={ref("username")}
              id="register-username"
              name="username"
              autoComplete="username"
              placeholder={t("register.usernamePlaceholder")}
              value={values.username}
              onChange={(event) => update("username", event.target.value.toLowerCase())}
            />
          </Field>

          <Field label={t("register.email")} htmlFor="register-email" error={fieldErrorMessage(errors.email)}>
            <input
              ref={ref("email")}
              id="register-email"
              name="email"
              type="email"
              autoComplete="email"
              placeholder={t("register.emailPlaceholder")}
              value={values.email}
              onChange={(event) => update("email", event.target.value)}
            />
          </Field>

          <div className="auth-code-row">
            <Field label={t("register.code")} htmlFor="register-code" error={fieldErrorMessage(errors.code)}>
              <input
                ref={ref("code")}
                id="register-code"
                name="code"
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder={t("register.codePlaceholder")}
                maxLength={6}
                value={values.code}
                onChange={(event) =>
                  update("code", event.target.value.replace(/\D/g, "").slice(0, 6))
                }
              />
            </Field>
            <div className="auth-code-row__action">
              <button
                type="button"
                className="button button--ghost"
                data-testid="register-send-code"
                disabled={sending || cooldown > 0}
                onClick={() => {
                  void sendCode();
                }}
              >
                {cooldown > 0
                  ? t("register.resendIn", { seconds: cooldown })
                  : sending
                    ? t("register.sendingCode")
                    : t("register.sendCode")}
              </button>
            </div>
          </div>
        </section>

        <section className="auth-section" aria-labelledby="register-password-section">
          <h2 className="auth-section__title" id="register-password-section">
            {t("register.passwordSection")}
          </h2>

          <Field label={t("register.password")} htmlFor="register-password" error={fieldErrorMessage(errors.password)}>
            <input
              ref={ref("password")}
              id="register-password"
              name="new-password"
              type="password"
              autoComplete="new-password"
              placeholder={t("register.passwordPlaceholder")}
              value={values.password}
              onChange={(event) => update("password", event.target.value)}
            />
          </Field>

          <Field
            label={t("register.confirmPassword")}
            htmlFor="register-confirm-password"
            error={fieldErrorMessage(errors.confirmPassword)}
          >
            <input
              ref={ref("confirmPassword")}
              id="register-confirm-password"
              name="confirm-password"
              type="password"
              autoComplete="new-password"
              placeholder={t("register.confirmPasswordPlaceholder")}
              value={values.confirmPassword}
              onChange={(event) => update("confirmPassword", event.target.value)}
            />
          </Field>
        </section>

        {notice ? (
          <Banner tone="info" testId="register-notice">
            {notice}
          </Banner>
        ) : null}
        {failure ? (
          <Banner tone="error" testId="register-error">
            <span>{failure.message}</span>
            {failure.requestId ? (
              <span className="muted">{t("common.requestId", { id: failure.requestId })}</span>
            ) : null}
          </Banner>
        ) : null}

        <button type="submit" className="button" data-testid="register-submit" disabled={submitting}>
          {submitting ? t("register.submitting") : t("register.submit")}
        </button>

        <p className="auth-switch">
          {t("register.haveAccount")}{" "}
          <Link to="/login" data-testid="register-to-login">
            {t("register.toLogin")}
          </Link>
        </p>
      </form>
    </section>
  );
}

function fieldErrorMessage(key: MessageKey | undefined): string | null {
  return key ? t(key) : null;
}
