import type { AppealPackView, Role } from "../api/types";
import type { SessionState } from "./sessionStore";

/**
 * 前端可见性规则（RQ-B-1-20）。
 *
 * **这不是安全边界**：真正的授权判定在 B4/B2/B3。这里的每一条都只决定"是否渲染入口"，
 * 并且一律 **fail-closed**：角色未知（`rolesKnown === false`）时特权入口全部隐藏。
 * 验收要求"学生直接调用编辑接口返回 403"，因此页面还必须原样展示服务端的 403，
 * 不能因为隐藏了按钮就把越权请求当成"没发生"。
 */

function has(state: SessionState, roles: Role[]): boolean {
  if (!state.rolesKnown) return false;
  return state.roles.some((role) => roles.includes(role));
}

/** 名单/课序读取入口（与编辑同集合：读取名单本身也是敏感操作）。 */
export function canViewRoster(state: SessionState): boolean {
  return has(state, ["teacher", "assistant", "admin"]);
}

/** 名单导入（教师、管理员）。 */
export function canImportRoster(state: SessionState): boolean {
  return has(state, ["teacher", "admin"]);
}

/** 用户与角色管理（仅管理员，`RQ-B-4-50` 验收 5）。 */
export function canManageUsers(state: SessionState): boolean {
  return has(state, ["admin"]);
}

/** 成绩导出（教师、助教、管理员）；学生即使直接调用也会得到 403。 */
export function canExportGrades(state: SessionState): boolean {
  return has(state, ["teacher", "assistant", "admin"]);
}

/** 全班成绩视图（教师、助教、管理员）；学生只看本人。 */
export function canViewCohortGrades(state: SessionState): boolean {
  return has(state, ["teacher", "assistant", "admin"]);
}

/** 代码提交（学生）；管理员可用于排障但不作为默认入口。 */
export function canSubmitAnswer(state: SessionState): boolean {
  return has(state, ["student"]);
}

/** 教师视图的证据下钻（可看到可见用例的期望输出）。 */
export function canViewTeacherEvidence(state: SessionState): boolean {
  return has(state, ["teacher", "assistant", "admin"]);
}

/**
 * 出题（题目写路径：新建草稿、修改草稿）。契约把出题辅助端点标注为
 * "Teacher/assistant only, inside the authoring flow"，因此入口与之一致。
 * 与其它可见性一样，这**不是**授权：B3 对越权调用返回 403，页面原样展示。
 */
/**
 * 创建课程/课序的入口（P12）。B4 只允许 teacher/admin 创建课程；助教只能读名单与导入，
 * 因此这里不把 assistant 算进来。这仍是可见性：越权调用由 B4 返回 403。
 */
export function canCreateCourse(state: SessionState): boolean {
  return has(state, ["teacher", "admin"]);
}

export function canAuthorQuestions(state: SessionState): boolean {
  return has(state, ["teacher", "assistant", "admin"]);
}

/**
 * 生成/重新生成教学评语的入口（RQ-T-20）。
 *
 * 契约把 `requestEvaluationCommentary` 描述为 "Teacher/assistant only: generating commentary is
 * an editorial act"，因此入口与之一致；`admin` 与 B1 其它写路径（例如 `canAuthorQuestions`）
 * 保持同一集合。这是**可见性**，不是授权：B2 对越权调用返回 403，页面原样展示。
 */
export function canGenerateCommentary(state: SessionState): boolean {
  return has(state, ["teacher", "assistant", "admin"]);
}

/**
 * 隔离区复核入口（CHG-0014）。契约在 `listIntakeQuarantines` 的 `description` 里写明授权是
 * "teacher/assistant/admin within the course scope"，因此入口与之一致。
 * 与其它可见性一样，这**不是**授权：B2 对越权调用返回 403，页面原样展示。
 */
export function canReviewQuarantine(state: SessionState): boolean {
  return has(state, ["teacher", "assistant", "admin"]);
}

/**
 * 题目质量视图入口（CHG-0015）。契约说它是"an aggregate a teacher reads to decide whether a
 * question is worth keeping"，但**没有**为这个端点单列 Authorization 段；B1 按教师侧处理，
 * 与同族的成绩证据视图（`canViewTeacherEvidence`）保持同一集合。
 */
export function canViewQuestionQuality(state: SessionState): boolean {
  return has(state, ["teacher", "assistant", "admin"]);
}

/**
 * 证据包的 `view` 参数（CHG-0014）。
 *
 * 为什么不能省略它：契约给 `view` 声明了 `default: teacher`——学生若沿用默认值，就是以
 * **教师视图**发请求。B1 因此从**实际角色**推出该值并始终显式发送。
 *
 * 角色未知时返回 `null`（fail-closed：不猜），调用方据此**不发请求**并说明原因。
 * 多重角色（例如既是学生又是助教）取更宽的那个：这只是**请求**，契约原文是
 * "the server decides what a role may see"，服务端仍会按真实权限收窄。
 */
export function appealPackView(state: SessionState): AppealPackView | null {
  if (!state.rolesKnown) return null;
  if (
    state.roles.some(
      (role) => role === "teacher" || role === "assistant" || role === "admin",
    )
  ) {
    return "teacher";
  }
  if (state.roles.some((role) => role === "student")) return "student";
  return null;
}
