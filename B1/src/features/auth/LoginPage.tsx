import { useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import { signIn } from "../../auth/sessionStore";
import { Banner, Field, Panel } from "../../ui/components";

/**
 * 登录（`POST /api/v1/auth/session`）。刷新凭据由 B4 以 HttpOnly cookie 下发。
 *
 * 登录只收**用户名 + 密码**。`?redirect=` 来自"未登录被拦下"的那次导航，登录成功后回到原页；
 * `?registered=` 来自注册页，用来显示"注册成功，请登录"。
 */
export function LoginPage() {
  const navigate = useNavigate();
  const { redirect, registered } = useSearch({ from: "/login" });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  return (
    <div className="auth-gate">
      <Panel title={t("login.title")} testId="login-panel" titleLevel={1}>
        <form
          className="stack"
          onSubmit={async (event) => {
            event.preventDefault();
            setError(null);
            setPending(true);
            try {
              await signIn(username, password);
              if (redirect) {
                // 回到被拦下的页面。用整页导航而不是 `navigate({to})`：`redirect` 是运行期字符串，
                // 而路由表的 `to` 是编译期联合类型；整页加载会重新走一次会话恢复，结果等价。
                window.location.assign(`/app${redirect}`);
                return;
              }
              await navigate({ to: "/" });
            } catch (signInError) {
              setError(
                isApiError(signInError) && signInError.isUnauthorized
                  ? t("login.failed")
                  : t("common.unknownError"),
              );
            } finally {
              setPending(false);
            }
          }}
        >
          {registered ? (
            <Banner tone="success" testId="login-registered">
              {t("login.registeredAs", { name: registered })}
            </Banner>
          ) : null}
          <Field label={t("login.username")} htmlFor="login-username">
            <input
              id="login-username"
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </Field>
          <Field label={t("login.password")} htmlFor="login-password">
            <input
              id="login-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </Field>
          {error ? (
            <Banner tone="error" testId="login-error">
              {error}
            </Banner>
          ) : null}
          <button
            type="submit"
            className="button"
            data-testid="login-submit"
            disabled={pending || username === "" || password === ""}
          >
            {pending ? t("login.submitting") : t("login.submit")}
          </button>
          <p className="muted">
            {t("login.noAccount")}{" "}
            <Link to="/register" data-testid="login-to-register">
              {t("login.toRegister")}
            </Link>
          </p>
        </form>
      </Panel>
    </div>
  );
}
