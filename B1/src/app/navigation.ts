import type { MessageKey } from "../i18n";
import type { SessionState } from "../auth/sessionStore";

/**
 * 主导航的角色规则（体验层；服务端仍对每个接口独立判权）。
 *
 * 学生只需要两件事：做什么作业、得了多少分。题库与课程属于教学侧，学生按契约不可浏览
 * 全量题库，因此不该看到入口。教师/助教/管理员共用教学侧四项；管理员额外拥有「用户管理」，
 * 它与教学入口同级放在侧栏（账号维护也是管理员的日常入口），不再藏在右上角账户菜单里。
 *
 * 角色未知时 fail-closed：只保留非特权入口（作业、成绩），不闪现题库/课程。
 */

export type MainNavItem = {
  /** 站内绝对路径，同时也是激活态判定的前缀。 */
  to: "/assignments" | "/questions" | "/grades" | "/roster" | "/admin/users";
  labelKey: MessageKey;
};

const TEACHING_ROLES = new Set(["teacher", "assistant", "admin"]);

export function mainNavItems(
  session: Pick<SessionState, "status" | "roles" | "rolesKnown">,
): MainNavItem[] {
  const studentItems: MainNavItem[] = [
    { to: "/assignments", labelKey: "nav.assignments" },
    { to: "/grades", labelKey: "nav.grades" },
  ];
  if (session.status !== "authenticated" || !session.rolesKnown) return studentItems;

  const teaching = session.roles.some((role) => TEACHING_ROLES.has(role));
  if (!teaching) return studentItems;

  const items: MainNavItem[] = [
    { to: "/assignments", labelKey: "nav.assignments" },
    { to: "/questions", labelKey: "nav.questions" },
    { to: "/grades", labelKey: "nav.grades" },
    { to: "/roster", labelKey: "nav.roster" },
  ];
  // 用户管理是管理员专属入口；教师/助教看不到，直接访问仍由 B4 判权。
  if (session.roles.includes("admin")) {
    items.push({ to: "/admin/users", labelKey: "nav.users" });
  }
  return items;
}

/**
 * 当前路径属于哪个主入口。子页面保持所属入口高亮（`/questions/{id}/edit` 仍属于题库），
 * 但 `/roster-import` 这类"同前缀的另一个词"不会被误判为 `/roster`。
 */
export function isMainNavActive(pathname: string, to: MainNavItem["to"]): boolean {
  return pathname === to || pathname.startsWith(`${to}/`);
}
