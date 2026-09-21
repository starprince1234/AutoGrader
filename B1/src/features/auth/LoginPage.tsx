import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import { signIn } from "../../auth/sessionStore";
import { Banner, Field, Panel } from "../../ui/components";

/** 登录（`POST /api/v1/auth/session`）。刷新凭据由 B4 以 HttpOnly cookie 下发。 */
export function LoginPage() {
  const navigate = useNavigate();
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
        </form>
      </Panel>
    </div>
  );
}
