import { t } from "../../i18n";
import type { DraftForm } from "./draft";

/**
 * 出题向导的五个步骤：**顺序、名称与完成判定只有这一份定义**。
 *
 * 页面用 `STEPS` 渲染左侧导航，测试也用同一份数据断言"哪一步算完成"，因此导航里的小圆点
 * 与页面里的校验提示不会各说一套。
 */
export type StepId = "basic" | "stem" | "reference" | "cases" | "comparison";

export type StepDefinition = {
  id: StepId;
  /** 导航与步骤面板共用的标题。 */
  label: () => string;
};

export const STEPS: readonly StepDefinition[] = [
  { id: "basic", label: () => t("authoring.steps.basic") },
  { id: "stem", label: () => t("authoring.steps.stem") },
  { id: "reference", label: () => t("authoring.steps.reference") },
  { id: "cases", label: () => t("authoring.steps.cases") },
  { id: "comparison", label: () => t("authoring.steps.comparison") },
] as const;

/**
 * 一步"已完成"的判据。
 *
 * 判据只看**这一屏能读到的事实**，不猜：
 * - 基本信息：选好课程并写了标题；
 * - 题干：题干非空；
 * - 参考答案：写了答案；编辑既有题目时页面读不到受保护字段（`CHG-0017`），此时视为已完成——
 *   否则这一格会永远停在"未完成"，把一个看不见的值说成没填；
 * - 测试用例：至少有一个用例；
 * - 判分比对规则：已经选了标准输出比对方式（不选就无法编译）。
 */
export function isStepComplete(
  id: StepId,
  form: DraftForm,
  courseId: string,
  options: { editing: boolean },
): boolean {
  if (id === "basic") return courseId !== "" && form.title.trim() !== "";
  if (id === "stem") return form.stemMarkdown.trim() !== "";
  if (id === "reference") return form.referenceAnswer.trim() !== "" || options.editing;
  if (id === "cases") return form.cases.length > 0;
  return form.comparisonStdout !== "";
}
