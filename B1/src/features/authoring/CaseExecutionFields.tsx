import { useEffect, useRef, useState } from "react";
import { t } from "../../i18n";
import { Field } from "../../ui/components";
import { Select } from "../../ui/Select";
import { sha256Hex } from "../submission/validation";
import { emptyFileCheck, type CaseForm, type FileCheckForm } from "./draft";
import {
  EMAIL_CHECKS,
  MAX_FILE_CHECKS_PER_CASE,
  SSH_CHECKS,
  type ExecutionMode,
} from "./execution";

/**
 * 用例级「执行声明」编辑器（`TestCaseInput` 的 `segment` / `file_checks` / `ssh_check` /
 * `email_check`）。
 *
 * 只按当前评分方式渲染**该模式会读取**的字段：`per_case` 与 `single_run` 显示文件校验，
 * `single_run` 额外提供「按分段评分」开关（开 / 关互斥，对应后端 `segment` XOR `file_checks`
 * 的规则），`ssh_session` / `email_delivery` 显示判定项下拉。看不见的字段不会进请求。
 *
 * 文件校验的期望内容**只在本机**用 WebCrypto 算 SHA-256：请求里只有摘要，内容原文不上传。
 */
export function CaseExecutionFields({
  ordinal,
  mode,
  item,
  onChange,
}: {
  ordinal: number;
  /** 当前评分方式（来自版本级执行声明，不是从标题/标签推断出来的）。 */
  mode: ExecutionMode;
  item: CaseForm;
  onChange: (patch: Partial<CaseForm>) => void;
}) {
  if (mode === "ssh_session") {
    return (
      <Field
        label={t("authoring.execution.caseSshCheck")}
        htmlFor={`case-ssh-check-${ordinal}`}
        hint={t("authoring.execution.caseSshCheckHint")}
      >
        <Select
          id={`case-ssh-check-${ordinal}`}
          data-testid={`case-ssh-check-${ordinal}`}
          value={item.sshCheck}
          onChange={(event) =>
            onChange({ sshCheck: event.target.value as CaseForm["sshCheck"] })
          }
        >
          <option value="">{t("common.selectPrompt")}</option>
          {SSH_CHECKS.map((check) => (
            <option key={check} value={check}>
              {t(`authoring.execution.sshCheck.${check}`)}
            </option>
          ))}
        </Select>
      </Field>
    );
  }

  if (mode === "email_delivery") {
    return (
      <Field
        label={t("authoring.execution.caseEmailCheck")}
        htmlFor={`case-email-check-${ordinal}`}
        hint={t("authoring.execution.caseEmailCheckHint")}
      >
        <Select
          id={`case-email-check-${ordinal}`}
          data-testid={`case-email-check-${ordinal}`}
          value={item.emailCheck}
          onChange={(event) =>
            onChange({ emailCheck: event.target.value as CaseForm["emailCheck"] })
          }
        >
          <option value="">{t("common.selectPrompt")}</option>
          {EMAIL_CHECKS.map((check) => (
            <option key={check} value={check}>
              {t(`authoring.execution.emailCheck.${check}`)}
            </option>
          ))}
        </Select>
      </Field>
    );
  }

  if (mode === "single_run") {
    return (
      <div className="stack" data-testid={`case-execution-${ordinal}`}>
        <label className="inline" htmlFor={`case-segment-scored-${ordinal}`}>
          <input
            id={`case-segment-scored-${ordinal}`}
            type="checkbox"
            data-testid={`case-segment-scored-${ordinal}`}
            checked={item.segmentScored}
            onChange={(event) => onChange({ segmentScored: event.target.checked })}
          />
          {t("authoring.execution.caseSegmentScored")}
        </label>
        {item.segmentScored ? (
          <Field
            label={t("authoring.execution.caseSegment")}
            htmlFor={`case-segment-${ordinal}`}
            hint={t("authoring.execution.caseSegmentHint")}
          >
            <input
              id={`case-segment-${ordinal}`}
              data-testid={`case-segment-${ordinal}`}
              inputMode="numeric"
              value={item.segment}
              onChange={(event) => onChange({ segment: event.target.value })}
            />
          </Field>
        ) : (
          <FileChecksEditor
            ordinal={ordinal}
            checks={item.fileChecks}
            onChange={(fileChecks) => onChange({ fileChecks })}
          />
        )}
      </div>
    );
  }

  // per_case：每个用例各跑一次，运行结束后可以检查工作区文件（契约允许 file_checks 可选）。
  return (
    <FileChecksEditor
      ordinal={ordinal}
      checks={item.fileChecks}
      onChange={(fileChecks) => onChange({ fileChecks })}
    />
  );
}

/**
 * 文件校验列表编辑器。
 *
 * 摘要计算是异步的（WebCrypto），因此表单里 `contentSha256` 为空**且内容非空**只表示
 * "还在算"，不是"不校验内容"；提交校验会拒绝这种中间态（见 `draft.ts` 的
 * `fileCheckDigestPending`）。
 */
function FileChecksEditor({
  ordinal,
  checks,
  onChange,
}: {
  ordinal: number;
  checks: FileCheckForm[];
  onChange: (next: FileCheckForm[]) => void;
}) {
  // 异步摘要回来时以**最新**的表单值为准：计算期间教师还可能改别的字段。
  const latest = useRef(checks);
  latest.current = checks;
  // 摘要计算失败（例如浏览器没有 WebCrypto）时按内容记录，改了内容就自动重试。
  const [failedContents, setFailedContents] = useState<Record<number, string>>({});

  useEffect(() => {
    const pending = checks
      .map((check, index) => ({ check, index }))
      .filter(({ check }) => check.content !== "" && check.contentSha256 === "");
    if (pending.length === 0) return undefined;

    let cancelled = false;
    void (async () => {
      for (const { check, index } of pending) {
        try {
          const digest = await sha256Hex(check.content);
          if (cancelled) return;
          const current = latest.current[index];
          // 计算期间内容又被改过：这次结果作废，下一轮 effect 会按新内容重新计算。
          if (
            !current ||
            current.content !== check.content ||
            current.contentSha256 !== ""
          ) {
            continue;
          }
          onChange(
            latest.current.map((entry, position) =>
              position === index ? { ...entry, contentSha256: digest } : entry,
            ),
          );
        } catch {
          if (cancelled) return;
          setFailedContents((previous) => ({ ...previous, [index]: check.content }));
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [checks, onChange]);

  const updateCheck = (index: number, patch: Partial<FileCheckForm>) => {
    onChange(checks.map((entry, position) => (position === index ? { ...entry, ...patch } : entry)));
  };

  return (
    <div className="stack" data-testid={`case-file-checks-${ordinal}`}>
      <div className="inline">
        <h4>{t("authoring.execution.fileChecks")}</h4>
      </div>
      <p className="muted" data-testid={`case-file-checks-hint-${ordinal}`}>
        {t("authoring.execution.fileChecksHint")}
      </p>
      {checks.map((check, index) => {
        const pending = check.content !== "" && check.contentSha256 === "";
        const failed =
          check.content !== "" && failedContents[index] === check.content;
        return (
          <div
            className="stack"
            key={index}
            data-testid={`case-file-check-${ordinal}-${index}`}
          >
            <div className="form-grid">
              <Field
                label={t("authoring.execution.fileCheckPath")}
                htmlFor={`case-file-check-path-${ordinal}-${index}`}
                hint={t("authoring.execution.fileCheckPathHint")}
              >
                <input
                  id={`case-file-check-path-${ordinal}-${index}`}
                  data-testid={`case-file-check-path-${ordinal}-${index}`}
                  value={check.path}
                  onChange={(event) => updateCheck(index, { path: event.target.value })}
                />
              </Field>
              <Field
                label={t("authoring.execution.fileCheckMode")}
                htmlFor={`case-file-check-mode-${ordinal}-${index}`}
                hint={t("authoring.execution.fileCheckModeHint")}
              >
                <input
                  id={`case-file-check-mode-${ordinal}-${index}`}
                  data-testid={`case-file-check-mode-${ordinal}-${index}`}
                  inputMode="numeric"
                  value={check.mode}
                  onChange={(event) => updateCheck(index, { mode: event.target.value })}
                />
              </Field>
            </div>
            <Field
              label={t("authoring.execution.fileCheckContent")}
              htmlFor={`case-file-check-content-${ordinal}-${index}`}
              hint={t("authoring.execution.fileCheckContentHint")}
            >
              <textarea
                id={`case-file-check-content-${ordinal}-${index}`}
                data-testid={`case-file-check-content-${ordinal}-${index}`}
                rows={3}
                value={check.content}
                onChange={(event) =>
                  // 内容一改，旧摘要立即失效：宁可让保存等一次计算，也不拿旧摘要冒充新内容。
                  updateCheck(index, { content: event.target.value, contentSha256: "" })
                }
              />
            </Field>
            <div className="inline">
              <span className="muted">{t("authoring.execution.fileCheckDigest")}：</span>
              {check.contentSha256 !== "" ? (
                <code
                  className="mono-id"
                  data-testid={`case-file-check-digest-${ordinal}-${index}`}
                  title={check.contentSha256}
                >
                  {check.contentSha256}
                </code>
              ) : pending ? (
                <span
                  className="muted"
                  data-testid={`case-file-check-digest-pending-${ordinal}-${index}`}
                >
                  {t("authoring.execution.fileCheckDigestPending")}
                </span>
              ) : failed ? (
                <span
                  className="muted"
                  data-testid={`case-file-check-digest-failed-${ordinal}-${index}`}
                >
                  {t("authoring.execution.fileCheckDigestFailed")}
                </span>
              ) : (
                <span
                  className="muted"
                  data-testid={`case-file-check-digest-empty-${ordinal}-${index}`}
                >
                  {t("authoring.execution.fileCheckDigestEmpty")}
                </span>
              )}
              <button
                type="button"
                className="button button--danger"
                data-testid={`case-file-check-remove-${ordinal}-${index}`}
                onClick={() =>
                  onChange(checks.filter((_entry, position) => position !== index))
                }
              >
                {t("authoring.execution.fileCheckRemove")}
              </button>
            </div>
          </div>
        );
      })}
      <div className="inline">
        <button
          type="button"
          className="button button--ghost"
          data-testid={`case-file-check-add-${ordinal}`}
          disabled={checks.length >= MAX_FILE_CHECKS_PER_CASE}
          onClick={() => onChange([...checks, emptyFileCheck()])}
        >
          {t("authoring.execution.fileCheckAdd")}
        </button>
      </div>
    </div>
  );
}
