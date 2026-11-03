import { useNavigate, useSearch } from "@tanstack/react-router";
import { t } from "../../i18n";
import { LoginForm } from "./LoginForm";

/**
 * 登录页（`POST /api/v1/auth/session`）。刷新凭据由 B4 以 HttpOnly cookie 下发。
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

  return (
    <section className="auth-card" data-testid="login-panel">
      <h1 className="auth-card__title">{t("login.title")}</h1>
      <LoginForm
        redirect={redirect}
        registered={Boolean(registered)}
        reset={Boolean(reset)}
        onSuccess={(target) => navigate({ href: target, replace: true })}
      />
    </section>
  );
}
