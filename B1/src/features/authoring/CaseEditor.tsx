import { t } from "../../i18n";
import { Field } from "../../ui/components";
import { emptyCase, type CaseForm } from "./draft";

/**
 * 用例编辑器（`QuestionDraftInput.cases[]`）。
 *
 * 每个用例是契约 `TestCaseInput` 的一份声明：`stdin`/`args`/`expected_stdout`/
 * `expected_stderr`/`expected_exit_code` 全部受保护，从不经公开 DTO 回显——因此这些输入框
 * 的内容在进入页面时一定是空的，**空不是加载失败**，而是"必须由你重新声明"。
 *
 * `ordinal` 不在这里编辑：它由数组顺序决定（0 基），避免出现"序号与顺序不一致"的用例集合。
 */
export function CaseEditor({
  cases,
  onChange,
}: {
  cases: CaseForm[];
  onChange: (next: CaseForm[]) => void;
}) {
  const update = (index: number, patch: Partial<CaseForm>) => {
    onChange(cases.map((item, position) => (position === index ? { ...item, ...patch } : item)));
  };

  return (
    <div className="stack" data-testid="case-editor">
      {cases.map((item, index) => (
        <fieldset className="case-card" key={index} data-testid={`case-${index}`}>
          <legend className="case-card__legend">
            {t("authoring.caseOrdinal")} {index + 1}
          </legend>
          <div className="form-grid">
            <Field label={t("authoring.caseName")} htmlFor={`case-name-${index}`}>
              <input
                id={`case-name-${index}`}
                value={item.name}
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

          <Field label={t("authoring.caseArgs")} htmlFor={`case-args-${index}`}>
            <input
              id={`case-args-${index}`}
              value={item.args}
              onChange={(event) => update(index, { args: event.target.value })}
            />
          </Field>
          <Field label={t("authoring.caseStdin")} htmlFor={`case-stdin-${index}`}>
            <textarea
              id={`case-stdin-${index}`}
              rows={3}
              value={item.stdin}
              onChange={(event) => update(index, { stdin: event.target.value })}
            />
          </Field>
          <div className="grid-2">
            <Field
              label={t("authoring.caseExpectedStdout")}
              htmlFor={`case-stdout-${index}`}
            >
              <textarea
                id={`case-stdout-${index}`}
                rows={4}
                value={item.expectedStdout}
                onChange={(event) => update(index, { expectedStdout: event.target.value })}
              />
            </Field>
            <Field
              label={t("authoring.caseExpectedStderr")}
              htmlFor={`case-stderr-${index}`}
            >
              <textarea
                id={`case-stderr-${index}`}
                rows={4}
                value={item.expectedStderr}
                onChange={(event) => update(index, { expectedStderr: event.target.value })}
              />
            </Field>
          </div>

          <div className="inline">
            <label className="inline">
              <input
                type="checkbox"
                id={`case-visible-${index}`}
                checked={item.visibleToStudent}
                onChange={(event) => update(index, { visibleToStudent: event.target.checked })}
              />
              {t("authoring.caseVisible")}
            </label>
            <span className="muted">{t("authoring.caseVisibleHint")}</span>
          </div>
          <p className="muted">{t("authoring.caseExpectedHint")}</p>

          <div className="inline">
            <button
              type="button"
              className="button button--danger"
              data-testid={`case-remove-${index}`}
              onClick={() => onChange(cases.filter((_item, position) => position !== index))}
            >
              {t("authoring.caseRemove")}
            </button>
          </div>
        </fieldset>
      ))}

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
    </div>
  );
}
