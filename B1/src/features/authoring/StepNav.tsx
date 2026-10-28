import { t } from "../../i18n";
import { STEPS, type StepId } from "./steps";

/**
 * 左侧步骤导航：完成的步骤是蓝色实心圆点 + 对勾，未完成的是蓝色空心圆圈。
 *
 * 用 `<ol>` + `<button>`：读屏软件会读出"第 2 步，共 5 步"，点击直接切换（一次只显示一步，
 * 所以切换本身就是导航，不需要"上一步/下一步"）。
 */
export function StepNav({
  current,
  completed,
  onSelect,
}: {
  current: StepId;
  completed: Record<StepId, boolean>;
  onSelect: (next: StepId) => void;
}) {
  return (
    <nav className="wizard__nav" aria-label={t("authoring.steps.nav")} data-testid="wizard-nav">
      <ol className="wizard__steps">
        {STEPS.map((step, index) => {
          const done = completed[step.id];
          const active = step.id === current;
          return (
            <li key={step.id}>
              <button
                type="button"
                className={`wizard__step${active ? " is-active" : ""}`}
                data-testid={`wizard-step-${step.id}`}
                aria-current={active ? "step" : undefined}
                onClick={() => onSelect(step.id)}
              >
                <span
                  className={`step-dot${done ? " step-dot--done" : ""}`}
                  data-testid={`wizard-dot-${step.id}`}
                  data-done={done ? "true" : "false"}
                  aria-hidden="true"
                >
                  {done ? "✓" : ""}
                </span>
                <span className="wizard__step-label">
                  {index + 1}. {step.label()}
                </span>
                <span className="wizard__step-state">
                  {done ? t("authoring.steps.done") : t("authoring.steps.pending")}
                </span>
              </button>
              {/*
                步骤之间的连接线：已完成的那一步下面是蓝色实线，未完成的是灰色虚线。
                它把"从上往下的顺序 + 走到哪一步了"画出来，因此纯装饰（对读屏隐藏）。
              */}
              {index < STEPS.length - 1 ? (
                <span
                  className="wizard__line"
                  data-done={done ? "true" : "false"}
                  data-testid={`wizard-line-${step.id}`}
                  aria-hidden="true"
                />
              ) : null}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
