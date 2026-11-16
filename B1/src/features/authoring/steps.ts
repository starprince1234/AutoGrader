import { t } from "../../i18n";
import type { CaseForm, DraftForm } from "./draft";

/**
 * 出题向导的五个步骤：**顺序、名称与完成判定只有这一份定义**。
 *
 * 页面用 `STEPS` 渲染左侧导航，测试也用同一份数据断言"哪一步算完成"，因此导航里的小圆点
 * 与页面里的校验提示不会各说一套。
 */
export type StepId = "basic" | "stem" | "reference" | "preview";

export type StepDefinition = {
  id: StepId;
  /** 导航与步骤面板共用的标题。 */
  label: () => string;
};

export const STEPS: readonly StepDefinition[] = [
  { id: "basic", label: () => t("authoring.steps.basic") },
  { id: "stem", label: () => t("authoring.steps.stem") },
  { id: "reference", label: () => t("authoring.steps.reference") },
  { id: "preview", label: () => t("authoring.steps.preview") },
] as const;

export function hasReferenceContent(form: DraftForm): boolean {
  return (
    form.referenceAnswer.trim() !== "" ||
    form.referenceAnswers.some((tier) => tier.body.trim() !== "")
  );
}

/**
 * 一步"已完成"的判据。
 *
 * 判据只看**这一屏能读到的事实**：
 * - 基本信息：选好课程并写了标题；
 * - 题干：题干非空；
 * - 参考答案与评分细则：至少填了一份非空参考解法（编辑既有题目且未改时按已完成处理）。
 */
export function isStepComplete(
  id: StepId,
  form: DraftForm,
  courseId: string,
  options: { editing: boolean },
): boolean {
  if (id === "basic") return courseId !== "" && form.title.trim() !== "";
  if (id === "stem") return form.stemMarkdown.trim() !== "";
  if (id === "reference") return hasReferenceContent(form) || options.editing;
  return (
    courseId !== "" &&
    form.title.trim() !== "" &&
    form.stemMarkdown.trim() !== "" &&
    (hasReferenceContent(form) || options.editing)
  );
}

/**
 * 一个用例算不算"填过了"。
 *
 * 只看**教师写进去的内容**：名称、标准输入、命令行参数、期望标准输出、期望标准错误。
 * 刻意不看权值与期望退出码——退出码出厂就是 `0`、权值出厂是空的，两者都不是"教师填了东西"
 * 的证据；否则默认那一个空用例就会把第四步点亮成"已完成"（线上看到过这个问题）。
 */
export function caseHasContent(item: CaseForm): boolean {
  return (
    item.name.trim() !== "" ||
    item.stdin.trim() !== "" ||
    item.args.trim() !== "" ||
    item.expectedStdout.trim() !== "" ||
    item.expectedStderr.trim() !== ""
  );
}
