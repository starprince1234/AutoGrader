import { t } from "../i18n";
import { useCurrentUser } from "../api/currentUser";
import { useSession } from "../auth/sessionContext";
import { GooeyButton } from "../ui/components";

/**
 * 右上角账户区：显示账号、学生所在课程/课序/班级信息，以及退出登录按钮。
 */
export function AccountMenu({
  onSignOut,
  signingOut,
}: {
  onSignOut: () => void;
  signingOut: boolean;
}) {
  const session = useSession();
  const isStudentOnly =
    session.status === "authenticated" &&
    session.roles.includes("student") &&
    !session.roles.includes("admin") &&
    !session.roles.includes("teacher");
  const currentUser = useCurrentUser({ enabled: isStudentOnly });
  const memberships = currentUser.data?.memberships ?? [];
  const membershipText = memberships
    .map((m) => `${m.course_title} · 课序 ${m.section_code} · ${m.class_name}`)
    .join(" / ");

  return (
    <div className="account" data-testid="account-bar">
      <span className="account__name" data-testid="session-identity">
        {session.username ?? t("nav.accountLabel")}
        {isStudentOnly && currentUser.data?.student_number
          ? ` (${currentUser.data.student_number})`
          : ""}
      </span>
      {isStudentOnly && membershipText ? (
        <span className="account__meta" data-testid="account-memberships" title={membershipText}>
          {membershipText}
        </span>
      ) : null}
      <GooeyButton
        className="account__logout"
        data-testid="account-logout"
        disabled={signingOut}
        onClick={onSignOut}
      >
        {signingOut ? t("nav.loggingOut") : t("nav.logout")}
      </GooeyButton>
    </div>
  );
}
