import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { t } from "../../i18n";
import { Banner, Field, Panel } from "../../ui/components";
import {
  completeRegistration,
  registrationErrorText,
  requestRegistrationCode,
} from "./registrationApi";

const STUDENT_NUMBER_PATTERN = /^[0-9]{13}$/;
const USERNAME_PATTERN = /^[a-z][a-z0-9._-]{2,31}$/;

/**
 * 注册页（`POST /api/v1/auth/registrations/code` → `POST /api/v1/auth/registrations`）。
 *
 * 两步式是刻意的：验证码必须与"这次注册意图"绑定，所以先发码、再带码提交。
 * 前端只做**可读性**校验（学号 13 位、用户名形状、两次口令一致），
 * 唯一性、名单归属、口令策略、邮箱真实性全部由服务端判定。
 */
export function RegisterPage() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [studentNumber, setStudentNumber] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [sending, setSending] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, []);

  const startCooldown = (seconds: number) => {
    setCooldown(seconds);
    if (timer.current) clearInterval(timer.current);
    timer.current = setInterval(() => {
      setCooldown((current) => {
        if (current <= 1) {
          if (timer.current) clearInterval(timer.current);
          return 0;
        }
        return current - 1;
      });
    }, 1000);
  };

  /**
   * 请求验证码只需要这四项（服务端的 `requestRegistrationCode` 也只收这四项）。
   * 口令与邮箱验证码是两件独立的事：把发码按钮绑在"整张表单合法"上，
   * 会让用户在还没想好密码时看到一个永远点不动的灰按钮，且没有任何原因提示。
   */
  const canRequestCode =
    USERNAME_PATTERN.test(username) &&
    displayName.trim() !== "" &&
    STUDENT_NUMBER_PATTERN.test(studentNumber) &&
    email.trim() !== "";

  /** 提交注册才需要口令齐全。 */
  const identityReady = canRequestCode && password.length >= 8 && password === confirmPassword;

  const sendCode = async () => {
    setError(null);
    setNotice(null);
    if (!USERNAME_PATTERN.test(username)) {
      setError(t("register.usernameHint"));
      return;
    }
    if (displayName.trim() === "") {
      setError(t("register.displayNameRequired"));
      return;
    }
    if (!STUDENT_NUMBER_PATTERN.test(studentNumber)) {
      setError(t("register.studentNumberHint"));
      return;
    }
    if (email.trim() === "") {
      setError(t("register.emailRequired"));
      return;
    }
    setSending(true);
    try {
      const accepted = await requestRegistrationCode({
        username,
        email: email.trim(),
        student_number: studentNumber,
        display_name: displayName.trim(),
      });
      setNotice(t("register.codeSent", { seconds: accepted.expires_in_seconds }));
      startCooldown(accepted.resend_after_seconds);
    } catch (sendError) {
      setError(registrationErrorText(sendError));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="auth-gate">
      <Panel title={t("register.title")} testId="register-panel" titleLevel={1}>
        <form
          className="stack"
          onSubmit={async (event) => {
            event.preventDefault();
            setError(null);
            setNotice(null);
            if (password !== confirmPassword) {
              setError(t("register.passwordMismatch"));
              return;
            }
            setPending(true);
            try {
              await completeRegistration({
                email: email.trim(),
                code,
                password,
              });
              // 注册成功即跳登录页：一次显式的"用刚设的口令登录"顺便验证了凭据可用。
              await navigate({ to: "/login", search: { registered: username } });
            } catch (registrationError) {
              setError(registrationErrorText(registrationError));
            } finally {
              setPending(false);
            }
          }}
        >
          <Field label={t("register.username")} htmlFor="register-username">
            <input
              id="register-username"
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value.toLowerCase())}
            />
          </Field>
          <p className="muted">{t("register.usernameHint")}</p>

          {/* 凭据三件套紧挨着用户名：它们是一组，和下面的"身份 + 邮箱验证"分开。 */}
          <Field label={t("register.password")} htmlFor="register-password">
            <input
              id="register-password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </Field>
          <Field label={t("register.confirmPassword")} htmlFor="register-confirm-password">
            <input
              id="register-confirm-password"
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
            />
          </Field>

          <Field label={t("register.displayName")} htmlFor="register-display-name">
            <input
              id="register-display-name"
              autoComplete="name"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
            />
          </Field>

          <Field label={t("register.studentNumber")} htmlFor="register-student-number">
            <input
              id="register-student-number"
              inputMode="numeric"
              value={studentNumber}
              onChange={(event) => setStudentNumber(event.target.value.replace(/\s/g, ""))}
            />
          </Field>
          <p className="muted">{t("register.studentNumberHint")}</p>

          <Field label={t("register.email")} htmlFor="register-email">
            <input
              id="register-email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </Field>

          <Field label={t("register.code")} htmlFor="register-code">
            <input
              id="register-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
            />
          </Field>
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


          {notice ? (
            <Banner tone="info" testId="register-notice">
              {notice}
            </Banner>
          ) : null}
          {error ? (
            <Banner tone="error" testId="register-error">
              {error}
            </Banner>
          ) : null}

          <button
            type="submit"
            className="button"
            data-testid="register-submit"
            disabled={pending || !identityReady || code.length !== 6}
          >
            {pending ? t("register.submitting") : t("register.submit")}
          </button>

          <p className="muted">
            {t("register.haveAccount")}{" "}
            <Link to="/login" data-testid="register-to-login">
              {t("register.toLogin")}
            </Link>
          </p>
        </form>
      </Panel>
    </div>
  );
}
