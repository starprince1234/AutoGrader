import { useEffect, useRef, useState } from "react";
import { t } from "../../i18n";
import { Field, Modal } from "../../ui/components";
import { autoCaseName, emptyCase, type CaseForm } from "./draft";

/**
 * 用例编辑器（`QuestionDraftInput.cases[]`）。
 *
 * 每个用例是契约 `TestCaseInput` 的一份声明：`stdin`/`args`/`expected_stdout`/
 * `expected_stderr`/`expected_exit_code` 全部受保护，从不经公开 DTO 回显——因此这些输入框
 * 的内容在进入页面时一定是空的，**空不是加载失败**，而是"必须由你重新声明"。
 *
 * `ordinal` 不在这里编辑：它由数组顺序决定（0 基），拖拽排序就是重新排 `ordinal`，
 * 因此不会出现"序号与顺序不一致"的用例集合。
 *
 * 页面上只保留教师真正会填的几项：用例名（选填）、分值、标准输入、期望标准输出、学生可见。
 * 命令行参数 / 期望标准错误 / 期望退出码不在界面上出现（2.0 意见），但**数据里保留**：
 * 期望退出码默认 `0` 随用例一起提交，其余留空即"不声明"（`CHG-0017`）。
 */
export function CaseEditor({
  cases,
  onChange,
  focusOrdinal = null,
  onFocusHandled,
}: {
  cases: CaseForm[];
  onChange: (next: CaseForm[]) => void;
  /**
   * 要定位到的那条用例（0 基序号）。
   *
   * 从「检验并发布」点未通过的用例跳过来时由页面给：滚到卡片、把它标记为选中、
   * 光标放进它的输入框，老师不必在一屏用例里自己找。定位只发生一次（上方回调把它清掉），
   * 之后继续编辑不会再被拉回去。
   */
  focusOrdinal?: number | null;
  /** 定位完成后通知页面把 `focusOrdinal` 清空。 */
  onFocusHandled?: () => void;
}) {
  const [confirmIndex, setConfirmIndex] = useState<number | null>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [selectedOrdinal, setSelectedOrdinal] = useState<number | null>(null);
  const cardRefs = useRef<Array<HTMLFieldSetElement | null>>([]);

  useEffect(() => {
    if (focusOrdinal === null || focusOrdinal === undefined) return;
    const card = cardRefs.current[focusOrdinal];
    if (!card) return;
    setSelectedOrdinal(focusOrdinal);
    // `scrollIntoView` 在 jsdom 里不存在：没有就只做选中与聚焦，不假装滚动过。
    card.scrollIntoView?.({ block: "center", behavior: "smooth" });
    const stdin = card.querySelector<HTMLTextAreaElement>(`#case-stdin-${focusOrdinal}`);
    const name = card.querySelector<HTMLInputElement>(`#case-name-${focusOrdinal}`);
    (stdin ?? name ?? card).focus();
    onFocusHandled?.();
  }, [focusOrdinal, onFocusHandled]);

  const update = (index: number, patch: Partial<CaseForm>) => {
    onChange(cases.map((item, position) => (position === index ? { ...item, ...patch } : item)));
  };

  const removeCase = (index: number) => {
    onChange(cases.filter((_item, position) => position !== index));
    setConfirmIndex(null);
  };

  const moveCase = (from: number, to: number) => {
    if (from === to || from < 0 || to < 0 || from >= cases.length || to >= cases.length) return;
    const next = [...cases];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    onChange(next);
  };

  return (
    <div className="stack" data-testid="case-editor">
      {cases.map((item, index) => {
        return (
          <fieldset
            className={
              selectedOrdinal === index ? "case-card case-card--selected" : "case-card"
            }
            key={index}
            data-testid={`case-${index}`}
            data-selected={selectedOrdinal === index ? "true" : undefined}
            ref={(element) => {
              cardRefs.current[index] = element;
            }}
            onFocus={() => setSelectedOrdinal(index)}
            onDragOver={(event) => {
              // 允许放置：HTML5 拖拽只有在 dragover 里阻止默认行为才会触发 drop。
              if (dragIndex !== null) event.preventDefault();
            }}
            onDrop={(event) => {
              event.preventDefault();
              if (dragIndex !== null) moveCase(dragIndex, index);
              setDragIndex(null);
            }}
          >
            <legend
              className="case-card__legend"
              draggable
              data-testid={`case-drag-${index}`}
              title={t("authoring.caseDragHint")}
              onDragStart={() => setDragIndex(index)}
              onDragEnd={() => setDragIndex(null)}
            >
              {item.name.trim() === "" ? autoCaseName(index) : item.name.trim()}
            </legend>

            <div className="form-grid">
              <Field label={t("authoring.caseName")} htmlFor={`case-name-${index}`}>
                <input
                  id={`case-name-${index}`}
                  value={item.name}
                  placeholder={t("authoring.caseNamePlaceholder", { ordinal: index + 1 })}
                  // 浏览器会把以前输入过的词当「自动填充建议」弹出来（截图里那个 mixed_lines），
                  // 对老师来说就是一段看不懂的浮层：关掉它。
                  autoComplete="off"
                  onChange={(event) => update(index, { name: event.target.value })}
                />
              </Field>
              <Field label={t("authoring.caseWeight")} htmlFor={`case-weight-${index}`}>
                <input
                  id={`case-weight-${index}`}
                  inputMode="decimal"
                  value={item.weight}
                  onChange={(event) => update(index, { weight: event.target.value })}
                />
              </Field>
            </div>

            <Field
              label={t("authoring.caseStdin")}
              htmlFor={`case-stdin-${index}`}
              trailing={
                <span title={t("authoring.caseStdinHelp")} data-testid={`case-stdin-help-${index}`}>
                  ⓘ
                </span>
              }
            >
              <textarea
                id={`case-stdin-${index}`}
                rows={3}
                value={item.stdin}
                onChange={(event) => update(index, { stdin: event.target.value })}
              />
            </Field>

            <Field label={t("authoring.caseExpectedStdout")} htmlFor={`case-stdout-${index}`}>
              <textarea
                id={`case-stdout-${index}`}
                rows={4}
                value={item.expectedStdout}
                onChange={(event) => update(index, { expectedStdout: event.target.value })}
              />
            </Field>

            <div className="inline">
              <label className="inline" htmlFor={`case-visible-${index}`}>
                <input
                  type="checkbox"
                  id={`case-visible-${index}`}
                  checked={item.visibleToStudent}
                  onChange={(event) => update(index, { visibleToStudent: event.target.checked })}
                />
                {t("authoring.caseVisible")}
              </label>
            </div>

            <div className="inline">
              <button
                type="button"
                className="button button--danger"
                data-testid={`case-remove-${index}`}
                onClick={() => setConfirmIndex(index)}
              >
                {t("authoring.caseRemove")}
              </button>
            </div>
          </fieldset>
        );
      })}

      <div className="inline">
        <button
          type="button"
          className="button button--ghost"
          data-testid="case-add"
          onClick={() => onChange([...cases, emptyCase()])}
        >
          {t("authoring.caseAdd")}
        </button>
      </div>

      {confirmIndex !== null ? (
        <Modal
          title={t("authoring.caseDeleteTitle")}
          onClose={() => setConfirmIndex(null)}
          testId="case-delete-confirm"
        >
          <p>{t("authoring.caseDeleteConfirm")}</p>
          <div className="inline">
            <button
              type="button"
              className="button button--danger"
              data-testid="case-delete-confirm-action"
              onClick={() => removeCase(confirmIndex)}
            >
              {t("authoring.caseDeleteAction")}
            </button>
            <button
              type="button"
              className="button button--ghost"
              data-testid="case-delete-cancel"
              onClick={() => setConfirmIndex(null)}
            >
              {t("common.cancel")}
            </button>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
