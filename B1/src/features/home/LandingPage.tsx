import { useEffect, useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { t } from "../../i18n";
import { useSession } from "../../auth/sessionContext";
import { signOut } from "../../auth/sessionStore";
import { DEFAULT_AUTHENTICATED_PATH, safeRedirectTarget } from "../../auth/redirect";
import { clearPortalIntent, portalAllows, rememberPortalIntent, type Portal } from "../../auth/portal";
import { Modal } from "../../ui/components";
import { LoginForm } from "../auth/LoginForm";

type LandingTarget = "/assignments" | "/questions" | "/grades" | "/roster" | "/review";

type LoginIntent = {
  /** 从某一端入口进来时记录；从页头「登录」或回跳进来时为 null。 */
  portal: Portal | null;
  target: string;
  registered?: boolean;
  reset?: boolean;
};

type Feature = {
  to: LandingTarget;
  labelKey:
    | "landing.teacher.assignments"
    | "landing.teacher.questions"
    | "landing.teacher.grades"
    | "landing.teacher.roster"
    | "landing.teacher.review"
    | "landing.student.assignments"
    | "landing.student.grades";
  hintKey:
    | "landing.teacher.assignmentsHint"
    | "landing.teacher.questionsHint"
    | "landing.teacher.gradesHint"
    | "landing.teacher.rosterHint"
    | "landing.teacher.reviewHint"
    | "landing.student.assignmentsHint"
    | "landing.student.gradesHint";
};

const TEACHER_FEATURES: Feature[] = [
  {
    to: "/assignments",
    labelKey: "landing.teacher.assignments",
    hintKey: "landing.teacher.assignmentsHint",
  },
  {
    to: "/questions",
    labelKey: "landing.teacher.questions",
    hintKey: "landing.teacher.questionsHint",
  },
  {
    to: "/grades",
    labelKey: "landing.teacher.grades",
    hintKey: "landing.teacher.gradesHint",
  },
  {
    to: "/roster",
    labelKey: "landing.teacher.roster",
    hintKey: "landing.teacher.rosterHint",
  },
  {
    to: "/review",
    labelKey: "landing.teacher.review",
    hintKey: "landing.teacher.reviewHint",
  },
];

const STUDENT_FEATURES: Feature[] = [
  {
    to: "/assignments",
    labelKey: "landing.student.assignments",
    hintKey: "landing.student.assignmentsHint",
  },
  {
    to: "/grades",
    labelKey: "landing.student.grades",
    hintKey: "landing.student.gradesHint",
  },
];

/**
 * 导览页（P01 新入口）。
 *
 * 打开网站先看到教师端/学生端两栏；每一栏只列该端的功能，不把教师的功能混进学生端，
 * 反之亦然。点击入口时：
 * - 没有登录：在当前页打开登录浮窗，登录成功后直接进入所点功能；
 * - 最近登录过（会话仍有效）：直接进入；
 * - 已登录但角色不匹配：入口禁用并说明，避免用教师账号打开学生视图。
 *
 * 登录记忆不是前端伪造的：会话是否有效由 AppShell 等待 `restoreSession` 完成后的真实
 * 状态决定；这里只负责展示和跳转意图。
 */
export function LandingPage() {
  const session = useSession();
  const navigate = useNavigate();
  const search = useSearch({ from: "/" });
  const [signingOut, setSigningOut] = useState(false);
  const [loginIntent, setLoginIntent] = useState<LoginIntent | null>(() => {
    if (session.status === "authenticated") return null;
    if (!search.login && !search.redirect && !search.registered && !search.reset) return null;
    return {
      portal: null,
      target: safeRedirectTarget(search.redirect) ?? DEFAULT_AUTHENTICATED_PATH,
      registered: Boolean(search.registered),
      reset: Boolean(search.reset),
    };
  });
  const authenticated = session.status === "authenticated";
  const rolesKnown = session.rolesKnown;
  const teacherAllowed = !authenticated || portalAllows(session, "teacher");
  const studentAllowed = !authenticated || portalAllows(session, "student");
  const hasAuthSearch = Boolean(
    search.login || search.redirect || search.registered || search.reset,
  );

  // 回到导览页意味着重新选择入口：清掉上一次的目标意图，避免旧意图影响新选择。
  useEffect(() => {
    clearPortalIntent();
  }, []);

  const roleText = !rolesKnown
    ? t("nav.roleUnknown")
    : session.roles.length > 0
      ? session.roles.map((role) => t(`role.${role}`)).join(" / ")
      : t("nav.roleUnknown");

  const openLogin = (portal: Portal, target: LandingTarget) => {
    rememberPortalIntent(portal, target);
    setLoginIntent({ portal, target });
  };

  const closeLogin = () => {
    clearPortalIntent();
    setLoginIntent(null);
    if (hasAuthSearch) void navigate({ to: "/", replace: true });
  };

  const completeLogin = async (target: string) => {
    setLoginIntent(null);
    await navigate({ href: target, replace: true });
  };

  return (
    <div className="landing" data-testid="landing-page">
      <header className="landing__head">
        <h1 className="landing__brand">{t("app.brand")}</h1>
        <div className="landing__account">
          {authenticated ? (
            <>
              <span className="landing__identity" data-testid="landing-identity">
                {t("landing.signedIn", {
                  name: session.username ?? t("nav.accountLabel"),
                  roles: roleText,
                })}
              </span>
              <button
                type="button"
                className="button button--ghost"
                data-testid="landing-logout"
                disabled={signingOut}
                onClick={() => {
                  if (signingOut) return;
                  setSigningOut(true);
                  void signOut().finally(() => setSigningOut(false));
                }}
              >
                {signingOut ? t("nav.loggingOut") : t("nav.logout")}
              </button>
            </>
          ) : (
            <button
              type="button"
              className="button button--ghost"
              data-testid="landing-login"
              onClick={() =>
                setLoginIntent({ portal: null, target: DEFAULT_AUTHENTICATED_PATH })
              }
            >
              {t("login.submit")}
            </button>
          )}
        </div>
      </header>

      <main className="landing__main">
        <section
          className="landing__column landing__column--teacher"
          aria-labelledby="landing-teacher-title"
          data-testid="landing-teacher"
        >
          <header className="landing__column-head">
            <p className="landing__eyebrow">{t("landing.teacherEyebrow")}</p>
            <h2 id="landing-teacher-title" className="landing__title">
              {t("landing.teacherTitle")}
            </h2>
            <p className="landing__lead muted">{t("landing.teacherLead")}</p>
          </header>

          <nav aria-label={t("landing.teacherNav")}>
            <FeatureList
              features={TEACHER_FEATURES}
              portal="teacher"
              allow={teacherAllowed}
              authenticated={authenticated}
              onLogin={openLogin}
              testIdPrefix="landing-teacher"
            />
          </nav>

          <p className="landing__note">
            {authenticated && !rolesKnown
              ? t("landing.unknownRoles")
              : authenticated && !teacherAllowed
                ? t("landing.teacherDisabled")
                : t("landing.teacherRoles")}
          </p>
        </section>

        <section
          className="landing__column landing__column--student"
          aria-labelledby="landing-student-title"
          data-testid="landing-student"
        >
          <header className="landing__column-head">
            <p className="landing__eyebrow">{t("landing.studentEyebrow")}</p>
            <h2 id="landing-student-title" className="landing__title">
              {t("landing.studentTitle")}
            </h2>
            <p className="landing__lead muted">{t("landing.studentLead")}</p>
          </header>

          <nav aria-label={t("landing.studentNav")}>
            <FeatureList
              features={STUDENT_FEATURES}
              portal="student"
              allow={studentAllowed}
              authenticated={authenticated}
              onLogin={openLogin}
              testIdPrefix="landing-student"
            />
          </nav>

          <p className="landing__note">
            {authenticated && !rolesKnown
              ? t("landing.unknownRoles")
              : authenticated && !studentAllowed
                ? t("landing.studentDisabled")
                : t("landing.studentRoles")}
          </p>
        </section>
      </main>

      {loginIntent ? (
        <Modal
          title={
            loginIntent.portal === "teacher"
              ? t("landing.teacherLoginTitle")
              : loginIntent.portal === "student"
                ? t("landing.studentLoginTitle")
                : t("landing.loginTitle")
          }
          onClose={closeLogin}
          testId="login-dialog"
          className="modal--login"
          initialFocus="#login-username"
        >
          <p className="login-dialog__lead muted">
            {loginIntent.portal ? t("landing.loginLead") : t("landing.loginGenericLead")}
          </p>
          <LoginForm
            redirect={loginIntent.target}
            registered={loginIntent.registered}
            reset={loginIntent.reset}
            onSuccess={completeLogin}
            onNavigateAway={closeLogin}
          />
        </Modal>
      ) : null}
    </div>
  );
}

function FeatureList({
  features,
  portal,
  allow,
  authenticated,
  onLogin,
  testIdPrefix,
}: {
  features: Feature[];
  portal: Portal;
  allow: boolean;
  authenticated: boolean;
  onLogin: (portal: Portal, target: LandingTarget) => void;
  testIdPrefix: string;
}) {
  return (
    <ul className="landing__list">
      {features.map((feature) => {
        const testId = `${testIdPrefix}-${feature.to.slice(1)}`;
        const content = (
          <>
            <span className="landing__link-main">
              <span className="landing__link-label">{t(feature.labelKey)}</span>
              <span className="landing__link-hint">{t(feature.hintKey)}</span>
            </span>
            <span className="landing__link-arrow" aria-hidden="true">
              ›
            </span>
          </>
        );
        return (
          <li key={feature.to}>
            {!allow ? (
              <span
                className="landing__link landing__link--disabled"
                data-testid={testId}
                aria-disabled="true"
              >
                {content}
              </span>
            ) : authenticated ? (
              <Link
                to={feature.to}
                className="landing__link"
                data-testid={testId}
                onClick={() => {
                  // 总是记录"点了哪一端"：已登录但会话恰好在跳转前失效的窄窗口也需要它；
                  // AppShell 校验后会清除。
                  rememberPortalIntent(portal, feature.to);
                }}
              >
                {content}
              </Link>
            ) : (
              <button
                type="button"
                className="landing__link landing__link--action"
                data-testid={testId}
                aria-haspopup="dialog"
                onClick={() => onLogin(portal, feature.to)}
              >
                {content}
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
