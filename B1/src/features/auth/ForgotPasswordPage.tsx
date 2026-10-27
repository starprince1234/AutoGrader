import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import { Banner, Field } from "../../ui/components";
import { completePasswordReset, requestPasswordResetCode } from "./passwordResetApi";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const CODE_PATTERN = /^[0-9]{6}$/;
const PASSWORD_MIN_LENGTH = 8;
const PASSWORD_MAX_LENGTH = 128;

type FieldErrors = {
  email?: string;
  code?: string;
  password?: string;
  confirmPassword?: string;
};
type FormError = { message: string; requestId?: string };

/**
 * 忘记密码（`POST /api/v1/auth/password-reset/code` → `POST /api/v1/auth/password-reset`）。
 *
 * 安全边界：
 * - 发码接口对"已注册"与"未注册"邮箱返回同一句提示，页面不根据响应推断账号是否存在；
 * - 完成接口的所有验证码失败共用一个原因，不向用户暴露账号或验证码状态细节；
 * - 新密码与验证码只存在于当前表单内存，不写 URL、localStorage 或日志。
 */
export function ForgotPasswordPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});
  const [failure, setFailure] = useState<FormError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const sendingRef = useRef(false);
  const submittingRef = useRef(false);
  const emailRef = useRef<HTMLInputElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const confirmRef = useRef<HTMLInputElement>(null);

  // 重发冷却按秒递减；组件卸载后不再写状态。
  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((current) => current - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  const clearError = (field: keyof FieldErrors) => {
    setErrors((current) => {
      if (current[field] === undefined) return current;
      const next = { ...current };
      delete next[field];
      return next;
    });
  };

  const updateEmail = (value: string) => {
    setEmail(value);
    clearError("email");
    // 验证码绑定的是发码时的邮箱：邮箱一改，旧验证码就不能再用。
    if (sentTo !== null && value.trim().toLowerCase() !== sentTo) {
      setSentTo(null);
      setCode("");
      setNotice(null);
      setCooldown(0);
      setFailure(null);
      clearError("code");
    }
  };

  const validateEmailField = (): string | null => {
    const value = email.trim();
    if (value === "") return t("forgot.emailRequired");
    if (!EMAIL_PATTERN.test(value)) return t("forgot.emailInvalid");
    return null;
  };

  const focusFirst = (nextErrors: FieldErrors) => {
    if (nextErrors.email) emailRef.current?.focus();
    else if (nextErrors.code) codeRef.current?.focus();
    else if (nextErrors.password) passwordRef.current?.focus();
    else if (nextErrors.confirmPassword) confirmRef.current?.focus();
  };

  const sendCode = async () => {
    if (sendingRef.current || cooldown > 0) return;
    setFailure(null);
    setNotice(null);

    const emailError = validateEmailField();
    if (emailError) {
      setErrors({ email: emailError });
      emailRef.current?.focus();
      return;
    }

    setErrors({});
    sendingRef.current = true;
    setSending(true);
    try {
      const accepted = await requestPasswordResetCode(email.trim().toLowerCase());
      setSentTo(email.trim().toLowerCase());
      setCode("");
      setNotice(t("forgot.codeSent"));
      setCooldown(accepted.resend_after_seconds);
      codeRef.current?.focus();
    } catch (error) {
      setFailure(resetRequestError(error));
    } finally {
      sendingRef.current = false;
      setSending(false);
    }
  };

  const submit = async () => {
    if (submittingRef.current) return;
    setFailure(null);
    setNotice(null);

    const nextErrors: FieldErrors = {};
    const emailError = validateEmailField();
    if (emailError) nextErrors.email = emailError;
    if (!CODE_PATTERN.test(code)) nextErrors.code = t("forgot.codeRequired");
    if (password.length < PASSWORD_MIN_LENGTH) nextErrors.password = t("forgot.passwordRequired");
    else if (password.length > PASSWORD_MAX_LENGTH) {
      nextErrors.password = t("forgot.error.password");
    }
    if (confirmPassword !== password) {
      nextErrors.confirmPassword = t("forgot.passwordMismatch");
    }
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) {
      focusFirst(nextErrors);
      return;
    }

    submittingRef.current = true;
    setSubmitting(true);
    try {
      await completePasswordReset({
        email: email.trim().toLowerCase(),
        code,
        new_password: password,
      });
      // 不自动建立会话：回到登录页，用新密码登录一次，顺便验证凭据可用。
      await navigate({ to: "/login", search: { reset: "done" }, replace: true });
    } catch (error) {
      const mapped = resetSubmitError(error);
      setErrors(mapped.fieldErrors);
      setFailure(mapped.formError);
      focusFirst(mapped.fieldErrors);
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  return (
    <section className="auth-card auth-card--wide" data-testid="forgot-panel">
      <h1 className="auth-card__title">{t("forgot.title")}</h1>
      <form
        className="auth-form"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <section className="auth-section" aria-labelledby="forgot-verify-section">
          <h2 className="auth-section__title" id="forgot-verify-section">
            {t("forgot.verifySection")}
          </h2>

          <div className="auth-code-row">
            <Field label={t("forgot.email")} htmlFor="forgot-email" error={errors.email}>
              <input
                ref={emailRef}
                id="forgot-email"
                name="email"
                type="email"
                autoComplete="email"
                autoFocus
                placeholder={t("forgot.emailPlaceholder")}
                value={email}
                onChange={(event) => updateEmail(event.target.value)}
              />
            </Field>
            <div className="auth-code-row__action">
              <button
                type="button"
                className="button button--ghost"
                data-testid="forgot-send-code"
                disabled={sending || cooldown > 0}
                onClick={() => {
                  void sendCode();
                }}
              >
                {cooldown > 0
                  ? t("forgot.resendIn", { seconds: cooldown })
                  : sending
                    ? t("forgot.sendingCode")
                    : t("forgot.sendCode")}
              </button>
            </div>
          </div>

          <p className="muted auth-hint">{t("forgot.hint")}</p>

          <Field label={t("forgot.code")} htmlFor="forgot-code" error={errors.code}>
            <input
              ref={codeRef}
              id="forgot-code"
              name="code"
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder={t("forgot.codePlaceholder")}
              maxLength={6}
              value={code}
              onChange={(event) => {
                setCode(event.target.value.replace(/\D/g, "").slice(0, 6));
                clearError("code");
              }}
            />
          </Field>
        </section>

        <section className="auth-section" aria-labelledby="forgot-password-section">
          <h2 className="auth-section__title" id="forgot-password-section">
            {t("forgot.passwordSection")}
          </h2>

          <Field label={t("forgot.newPassword")} htmlFor="forgot-password" error={errors.password}>
            <input
              ref={passwordRef}
              id="forgot-password"
              name="new-password"
              type="password"
              autoComplete="new-password"
              placeholder={t("forgot.newPasswordPlaceholder")}
              value={password}
              onChange={(event) => {
                setPassword(event.target.value);
                clearError("password");
              }}
            />
          </Field>

          <Field
            label={t("forgot.confirmPassword")}
            htmlFor="forgot-confirm-password"
            error={errors.confirmPassword}
          >
            <input
              ref={confirmRef}
              id="forgot-confirm-password"
              name="confirm-password"
              type="password"
              autoComplete="new-password"
              placeholder={t("forgot.confirmPasswordPlaceholder")}
              value={confirmPassword}
              onChange={(event) => {
                setConfirmPassword(event.target.value);
                clearError("confirmPassword");
              }}
            />
          </Field>
        </section>

        {notice ? (
          <Banner tone="info" testId="forgot-notice">
            {notice}
          </Banner>
        ) : null}
        {failure ? (
          <Banner tone="error" testId="forgot-error">
            <span>{failure.message}</span>
            {failure.requestId ? (
              <span className="muted">{t("common.requestId", { id: failure.requestId })}</span>
            ) : null}
          </Banner>
        ) : null}

        <button type="submit" className="button" data-testid="forgot-submit" disabled={submitting}>
          {submitting ? t("forgot.submitting") : t("forgot.submit")}
        </button>

        <p className="auth-switch">
          <Link to="/login" data-testid="forgot-to-login">
            {t("forgot.backToLogin")}
          </Link>
        </p>
      </form>
    </section>
  );
}

/** 发码失败：503 说明邮件通道不可用；其余保持可读，不推断账号是否存在。 */
function resetRequestError(error: unknown): FormError {
  if (!isApiError(error)) return { message: t("forgot.error.network") };
  if (error.status === 503) {
    return { message: t("forgot.error.mail"), requestId: error.requestId };
  }
  if (error.isUnprocessable) {
    return { message: t("forgot.emailInvalid"), requestId: error.requestId };
  }
  return { message: t("forgot.error.service"), requestId: error.requestId };
}

/** 完成重置失败：验证码与密码错误落到字段；其余保留 request_id。 */
function resetSubmitError(error: unknown): {
  fieldErrors: FieldErrors;
  formError: FormError | null;
} {
  if (!isApiError(error)) {
    return { fieldErrors: {}, formError: { message: t("forgot.error.network") } };
  }
  const detail = error.problem?.detail ?? "";
  if (error.isUnprocessable) {
    if (detail.includes("验证码")) {
      return { fieldErrors: { code: t("forgot.error.code") }, formError: null };
    }
    if (detail.includes("密码")) {
      return { fieldErrors: { password: detail }, formError: null };
    }
    return {
      fieldErrors: {},
      formError: { message: detail || t("forgot.error.generic"), requestId: error.requestId },
    };
  }
  return {
    fieldErrors: {},
    formError: { message: t("forgot.error.generic"), requestId: error.requestId },
  };
}
