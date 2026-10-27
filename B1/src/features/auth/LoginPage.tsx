import { useRef, useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import { signIn } from "../../auth/sessionStore";
import { DEFAULT_AUTHENTICATED_PATH, safeRedirectTarget } from "../../auth/redirect";
import { Banner, Field } from "../../ui/components";

type FieldErrors = { username?: string; password?: string };
type FormError = { message: string; requestId?: string };

/**
 * 登录（`POST /api/v1/auth/session`）。刷新凭据由 B4 以 HttpOnly cookie 下发。
 *
 * 产品规则：
 * - 只收**用户名 + 密码**；忘记密码走真实的 `/forgot-password` 邮件验证码流程；
 * - 密码框提供自己的显示/隐藏按钮：浏览器原生的"小眼睛"在失焦后会被隐藏，登录失败后
 *   用户就看不到刚输入的密码了，因此这里是页面自己维护的状态；
 * - 认证失败只有一个结论「用户名或密码不正确」，不区分账号是否存在；
 * - `?redirect=` 来自"未登录被拦下"的那次导航，只接受站内路径，登录成功直接回到原页。
 */
export function LoginPage() {
  const navigate = useNavigate();
  const { redirect, registered, reset } = useSearch({ from: "/login" });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<FormError | null>(null);
  const [pending, setPending] = useState(false);
  const submittingRef = useRef(false);
  const usernameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);

  const submit = async () => {
    if (submittingRef.current) return;
    setFormError(null);

    const trimmedUsername = username.trim();
    const errors: FieldErrors = {};
    if (trimmedUsername === "") errors.username = t("login.usernameRequired");
    if (password === "") errors.password = t("login.passwordRequired");
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) {
      (errors.username ? usernameRef : passwordRef).current?.focus();
      return;
    }

    // 输入框显示什么就提交什么：两端空格在提交前被明确去掉，并同步回输入框。
    if (trimmedUsername !== username) setUsername(trimmedUsername);
    submittingRef.current = true;
    setPending(true);
    try {
      await signIn(trimmedUsername, password);
      const target = safeRedirectTarget(redirect) ?? DEFAULT_AUTHENTICATED_PATH;
      await navigate({ href: target, replace: true });
    } catch (error) {
      setFormError(loginError(error));
      // 网络断开时保留用户名、清空密码：用户至少不用重打账号，密码也不会留在一个
      // 连不上服务端的表单里。
      if (!isApiError(error)) setPassword("");
    } finally {
      submittingRef.current = false;
      setPending(false);
    }
  };

  return (
    <section className="auth-card" data-testid="login-panel">
      <h1 className="auth-card__title">{t("login.title")}</h1>
      <form
        className="auth-form"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        {registered ? (
          <Banner tone="success" testId="login-registered">
            {t("login.registered")}
          </Banner>
        ) : null}
        {reset ? (
          <Banner tone="success" testId="login-reset">
            {t("login.resetSuccess")}
          </Banner>
        ) : null}

        <Field label={t("login.username")} htmlFor="login-username" error={fieldErrors.username}>
          <input
            ref={usernameRef}
            id="login-username"
            name="username"
            autoComplete="username"
            autoFocus
            placeholder={t("login.usernamePlaceholder")}
            value={username}
            onChange={(event) => {
              setUsername(event.target.value);
              setFieldErrors((current) => ({ ...current, username: undefined }));
            }}
          />
        </Field>

        <Field
          label={t("login.password")}
          htmlFor="login-password"
          error={fieldErrors.password}
          trailing={
            <button
              type="button"
              className="field__action"
              data-testid="login-password-toggle"
              aria-label={showPassword ? t("login.hidePassword") : t("login.showPassword")}
              aria-pressed={showPassword}
              title={showPassword ? t("login.hidePassword") : t("login.showPassword")}
              onClick={() => setShowPassword((current) => !current)}
            >
              <EyeIcon crossed={showPassword} />
            </button>
          }
        >
          <input
            ref={passwordRef}
            id="login-password"
            name="password"
            type={showPassword ? "text" : "password"}
            autoComplete="current-password"
            placeholder={t("login.passwordPlaceholder")}
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
              setFieldErrors((current) => ({ ...current, password: undefined }));
            }}
          />
        </Field>

        <div className="auth-field-actions">
          <Link to="/forgot-password" data-testid="login-forgot-password">
            {t("login.forgotPassword")}
          </Link>
        </div>

        {formError ? (
          <Banner tone="error" testId="login-error">
            <span>{formError.message}</span>
            {formError.requestId ? (
              <span className="muted">{t("common.requestId", { id: formError.requestId })}</span>
            ) : null}
          </Banner>
        ) : null}

        <button
          type="submit"
          className="button"
          data-testid="login-submit"
          disabled={pending}
        >
          {pending ? t("login.submitting") : t("login.submit")}
        </button>

        <p className="auth-switch">
          {t("login.noAccount")}{" "}
          <Link to="/register" data-testid="login-to-register">
            {t("login.toRegister")}
          </Link>
        </p>
      </form>
    </section>
  );
}

/** 认证失败的文案边界：不暴露服务端细节，但也不把网络故障说成"密码错"。 */
function loginError(error: unknown): FormError {
  if (!isApiError(error)) return { message: t("login.network") };
  if (error.isUnauthorized) return { message: t("login.failed") };
  if (error.status === 429) return { message: t("login.rateLimitedNoWait") };
  if (error.status === 503) {
    return { message: t("login.serviceUnavailable"), requestId: error.requestId };
  }
  return { message: t("login.serviceError"), requestId: error.requestId };
}

/** 密码可见性图标：只做功能提示，按钮本身有可读的 aria-label。 */
function EyeIcon({ crossed }: { crossed: boolean }) {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M3 12s3.5-7 9-7 9 7 9 7-3.5 7-9 7-9-7-9-7z" />
      <circle cx="12" cy="12" r="3" />
      {crossed ? <path d="M4 4l16 16" /> : null}
    </svg>
  );
}
