import { t } from "../i18n";
import { useCurrentUser } from "../api/currentUser";
import { useSession } from "../auth/sessionContext";
import { GooeyButton } from "../ui/components";

/**
 * 右上角账户区：只显示用户名、姓名、学号，以及退出登录按钮。
 * 课程/课序/班级属于页面内容，不再占用全局顶栏。
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
  const displayName = currentUser.data?.display_name ?? "";
  const studentNumber = currentUser.data?.student_number ?? "";

  return (
    <div className="account" data-testid="account-bar">
      <span className="account__name" data-testid="session-identity">
        {session.username ?? t("nav.accountLabel")}
        {isStudentOnly && displayName ? ` ${displayName}` : ""}
        {isStudentOnly && studentNumber ? ` (${studentNumber})` : ""}
      </span>
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
