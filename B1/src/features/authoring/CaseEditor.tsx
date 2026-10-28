import { useState } from "react";
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
 * 高级项（命令行参数 / 期望标准错误 / 期望退出码）默认收起：大多数题目只写输入与期望输出，
 * 收起来页面才不会一屏塞满输入框——但字段本身一个都没删。
 */
export function CaseEditor({
  cases,
  onChange,
}: {
  cases: CaseForm[];
  onChange: (next: CaseForm[]) => void;
}) {
  // 展开状态按位置记录；复制/删除/拖拽时同步重排，避免"刚展开的那张卡变成别的卡"。
  const [expanded, setExpanded] = useState<number[]>([]);
  const [confirmIndex, setConfirmIndex] = useState<number | null>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);

  const update = (index: number, patch: Partial<CaseForm>) => {
    onChange(cases.map((item, position) => (position === index ? { ...item, ...patch } : item)));
  };

  const toggleAdvanced = (index: number) => {
    setExpanded((previous) =>
      previous.includes(index) ? previous.filter((item) => item !== index) : [...previous, index],
    );
  };

  const copyCase = (index: number) => {
    const copy = { ...cases[index] };
    const next = [...cases.slice(0, index + 1), copy, ...cases.slice(index + 1)];
    onChange(next);
    setExpanded((previous) =>
      previous.map((item) => (item > index ? item + 1 : item)).concat(index + 1),
    );
  };

  const removeCase = (index: number) => {
    onChange(cases.filter((_item, position) => position !== index));
    setExpanded((previous) =>
      previous.filter((item) => item !== index).map((item) => (item > index ? item - 1 : item)),
    );
    setConfirmIndex(null);
  };

  const moveCase = (from: number, to: number) => {
    if (from === to || from < 0 || to < 0 || from >= cases.length || to >= cases.length) return;
    const next = [...cases];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    onChange(next);
    setExpanded((previous) =>
      previous.map((item) => {
        if (item === from) return to;
        if (from < to && item > from && item <= to) return item - 1;
        if (to < from && item >= to && item < from) return item + 1;
        return item;
      }),
    );
  };

  return (
    <div className="stack" data-testid="case-editor">
      {cases.map((item, index) => {
        const advancedOpen = expanded.includes(index);
        return (
          <fieldset
            className="case-card"
            key={index}
            data-testid={`case-${index}`}
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
                className="button button--ghost"
                data-testid={`case-advanced-${index}`}
                aria-expanded={advancedOpen}
                onClick={() => toggleAdvanced(index)}
              >
                {advancedOpen
                  ? t("authoring.caseAdvancedHide")
                  : t("authoring.caseAdvancedShow")}
              </button>
            </div>

            {advancedOpen ? (
              <div className="stack" data-testid={`case-advanced-fields-${index}`}>
                <Field label={t("authoring.caseArgs")} htmlFor={`case-args-${index}`}>
                  <input
                    id={`case-args-${index}`}
                    value={item.args}
                    onChange={(event) => update(index, { args: event.target.value })}
                  />
                </Field>
                <Field
                  label={t("authoring.caseExpectedStderr")}
                  htmlFor={`case-stderr-${index}`}
                >
                  <textarea
                    id={`case-stderr-${index}`}
                    rows={3}
                    value={item.expectedStderr}
                    onChange={(event) => update(index, { expectedStderr: event.target.value })}
                  />
                </Field>
                <Field
                  label={t("authoring.caseExpectedExitCode")}
                  htmlFor={`case-exit-${index}`}
                >
                  <input
                    id={`case-exit-${index}`}
                    inputMode="numeric"
                    value={item.expectedExitCode}
                    onChange={(event) => update(index, { expectedExitCode: event.target.value })}
                  />
                </Field>
              </div>
            ) : null}

            <div className="inline">
              <button
                type="button"
                className="button button--ghost"
                data-testid={`case-copy-${index}`}
                onClick={() => copyCase(index)}
              >
                {t("authoring.caseCopy")}
              </button>
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
