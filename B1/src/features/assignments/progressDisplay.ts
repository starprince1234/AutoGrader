import { t } from "../../i18n";
import type { OverallState, QuestionState } from "./useAssignmentProgress";

/**
 * 作业进度的展示文案与状态色（学生工作区与教师详情共用）。
 *
 * 硬规则（DESIGN.md §4.1）：**未提交 / 未测量一律中性色**，red 只给真实失败；
 * 处理中是中性 idle 而不是成功；未完成不显示 0 分。
 */

export function questionStateLabel(state: QuestionState): string {
  switch (state) {
    case "completed":
      return t("workspace.question.completed");
    case "evaluating":
      return t("workspace.question.evaluating");
    case "failed":
      return t("workspace.question.failed");
    case "not_submitted":
      return t("workspace.question.pending");
  }
}

export function questionStateTone(
  state: QuestionState,
): "ok" | "warn" | "bad" | "idle" {
  if (state === "completed") return "ok";
  if (state === "failed") return "bad";
  return "idle";
}

export function overallStateLabel(state: OverallState): string {
  switch (state) {
    case "submitted":
      return t("workspace.overall.submitted");
    case "partial":
      return t("workspace.overall.partial");
    case "evaluating":
      return t("workspace.overall.evaluating");
    case "failed":
      return t("workspace.overall.failed");
    case "not_submitted":
      return t("workspace.overall.notSubmitted");
    case "unbound":
      return t("grades.missing.statusUnbound");
  }
}

export function overallStateTone(
  state: OverallState,
): "ok" | "warn" | "bad" | "idle" {
  if (state === "submitted") return "ok";
  if (state === "partial") return "warn";
  if (state === "failed") return "bad";
  return "idle";
}
