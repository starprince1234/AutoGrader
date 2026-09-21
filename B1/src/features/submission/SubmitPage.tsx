import { useEffect, useMemo, useRef, useState } from "react";
import { t } from "../../i18n";
import { newIdempotencyKey } from "../../api/client";
import { describeApiError } from "../../api/problem";
import { useSession } from "../../auth/sessionContext";
import { useQuestionVersion } from "../questions/useQuestions";
import { Banner, ErrorState, Field, Panel, Spinner } from "../../ui/components";
import { formatDateTime } from "../../ui/format";
import { log, newRequestId } from "../../logging/logger";
import { CodeEditor } from "./CodeEditor";
import type { MonacoLoader } from "./monaco";
import { ProgressPanel } from "./ProgressPanel";
import { clearDraft, loadDraft, saveDraft } from "./draft";
import { useStudentIdentity } from "./identity";
import { useCreateSubmission, useEvaluationRunId } from "./useSubmission";
import { useEvaluationStream } from "./useEvaluationStream";
import {
  ENVELOPE_MAX_BASE64_LENGTH,
  byteLength,
  buildSubmissionEnvelope,
  validateSubmission,
  type SubmissionFile,
} from "./validation";

/**
 * 作答与提交页（RQ-B-1-50）。
 *
 * 链路：本地草稿 → 提交前校验 → 构造 `submission-envelope`（含 `content_sha256`）
 * → `POST /api/v1/submissions`（带幂等键）→ 订阅 SSE 进度。
 */

const DEFAULT_FILE = "solution.py";

function issueMessage(issue: ReturnType<typeof validateSubmission>[number]): string {
  switch (issue.code) {
    case "no_files":
      return t("submit.validate.noFiles");
    case "empty_path":
      return t("submit.validate.emptyPath", { index: issue.index + 1 });
    case "bad_path":
      return t("submit.validate.badPath", { path: issue.path });
    case "duplicate_path":
      return t("submit.validate.duplicatePath", { path: issue.path });
    case "too_many_files":
      return t("submit.validate.tooManyFiles", { count: issue.count, max: issue.max });
    case "file_too_large":
      return t("submit.validate.fileTooLarge", { path: issue.path, max: issue.max });
    case "total_too_large":
      return t("submit.validate.totalTooLarge", { total: issue.bytes, max: issue.max });
    default:
      return t("common.unknownError");
  }
}

export function SubmitPage({
  questionVersionId,
  editorLoader,
}: {
  questionVersionId: string;
  /** 测试注入的编辑器加载器；生产环境使用真实 Monaco。 */
  editorLoader?: MonacoLoader;
}) {
  const session = useSession();
  const version = useQuestionVersion(questionVersionId);

  const scope = session.scope[0] ?? null;
  const courseId = scope?.course_id ?? null;
  const sectionId = scope?.section_id ?? null;
  const identity = useStudentIdentity(courseId, sectionId);

  const [files, setFiles] = useState<SubmissionFile[]>([
    { path: DEFAULT_FILE, content: "" },
  ]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [issues, setIssues] = useState<string[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [draftInfo, setDraftInfo] = useState<{ savedAt: string } | null>(null);
  const [restored, setRestored] = useState<string | null>(null);
  const idempotencyKeyRef = useRef<string>(newIdempotencyKey());
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const draftRestoreAttempted = useRef(false);

  const stream = useEvaluationStream(runId);
  const submit = useCreateSubmission();

  // 202 里的 run id 合法为 null（运行行在受理后才创建），因此轮询提交视图把它取回来，
  // 拿到后进度流才能真正订阅。终态仍无 id 说明这次提交不会产生运行。
  const terminalNoRun =
    submit.isSuccess &&
    submit.data.evaluation_run_id === null &&
    ["completed", "rejected", "failed"].includes(submit.data.state);
  const runLookup = useEvaluationRunId(
    submit.isSuccess ? submit.data.submission_id : null,
    submit.data?.evaluation_run_id ?? null,
    terminalNoRun,
  );
  const resolvedRunId = submit.data?.evaluation_run_id ?? runLookup.data?.evaluation_run_id ?? null;
  useEffect(() => {
    if (resolvedRunId && resolvedRunId !== runId) setRunId(resolvedRunId);
  }, [resolvedRunId, runId]);

  // 恢复本地未提交草稿（草稿≠已提交）。草稿键含用户标识，因此要等身份解析出来再读：
  // 用未知身份去读只会读到"没有"，而等到身份就绪后再覆盖用户已经敲进去的代码更糟。
  useEffect(() => {
    const ownerId = identity.studentId;
    if (ownerId === null || draftRestoreAttempted.current) return;
    draftRestoreAttempted.current = true;
    const draft = loadDraft(ownerId, questionVersionId);
    if (draft && draft.files.length > 0) {
      setFiles(draft.files);
      setRestored(draft.savedAt);
    }
  }, [identity.studentId, questionVersionId]);

  // 自动保存草稿（防抖）。拿不到身份（既没有令牌 sub、也没有本人成绩行）时不写：
  // 未提交代码不能落在"下一个人也能读到"的键上。
  useEffect(() => {
    const ownerId = identity.studentId;
    if (!version.data || ownerId === null) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      const savedAt = new Date().toISOString();
      saveDraft({
        ownerId,
        questionVersionId,
        language: version.data?.language ?? "python",
        files,
        savedAt,
      });
      setDraftInfo({ savedAt });
    }, 800);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [files, identity.studentId, questionVersionId, version.data]);

  const limits = useMemo(
    () => ({
      maxFiles: version.data?.max_files ?? null,
      maxTotalBytes: version.data?.max_total_bytes ?? null,
    }),
    [version.data],
  );

  const totalBytes = useMemo(
    () => files.reduce((sum, file) => sum + byteLength(file.content), 0),
    [files],
  );

  const canSubmit =
    version.data !== undefined &&
    identity.studentId !== null &&
    courseId !== null &&
    sectionId !== null &&
    !submit.isPending;

  return (
    <div className="stack">
      <Panel title={t("submit.title")} titleLevel={1}>
        {version.isPending ? (
          <Spinner />
        ) : version.isError ? (
          <ErrorState error={version.error} />
        ) : version.data ? (
          <div className="stack">
            <p className="muted">
              {t("questions.version", { version: version.data.version })} ·{" "}
              {t("questions.language")}：{t(`questions.language.${version.data.language}`)} ·{" "}
              {t("submit.limits", {
                files: version.data.max_files ?? "—",
                // 单文件上限来自信封 schema 的 `content_base64.maxLength`，这里**只**用
                // 那个常量：同一页曾同时写着 1048576 与 2097152 两个"单文件上限"。
                bytes: ENVELOPE_MAX_BASE64_LENGTH,
                total: version.data.max_total_bytes ?? "—",
              })}
            </p>

            {restored ? (
              <Banner tone="info" testId="draft-restored">
                {t("submit.draftRestored", {
                  time: formatDateTime(restored, t("common.none")),
                })}{" "}
                {t("submit.draftNotSubmitted")}
              </Banner>
            ) : null}

            {identity.studentId === null ? (
              <Banner tone="error" testId="identity-unavailable">
                {t("submit.identityUnavailable")}
              </Banner>
            ) : null}
            {courseId === null || sectionId === null ? (
              <Banner tone="error" testId="scope-unavailable">
                {t("submit.scopeUnavailable")}
              </Banner>
            ) : null}
          </div>
        ) : null}
      </Panel>

      {version.data ? (
        <Panel
          title={t("submit.fileTab")}
          actions={
            <button
              type="button"
              className="button button--ghost"
              data-testid="add-file"
              onClick={() => {
                setFiles((previous) => [...previous, { path: `file${previous.length + 1}.txt`, content: "" }]);
                setActiveIndex(files.length);
              }}
            >
              {t("submit.addFile")}
            </button>
          }
        >
          <div className="inline" role="tablist">
            {files.map((file, index) => (
              <button
                key={`${file.path}-${index}`}
                type="button"
                role="tab"
                aria-selected={index === activeIndex}
                className={`button ${index === activeIndex ? "" : "button--ghost"}`}
                onClick={() => setActiveIndex(index)}
              >
                {file.path === "" ? t("submit.pathLabel") : file.path}
              </button>
            ))}
          </div>

          {files[activeIndex] ? (
            <div className="stack">
              <Field label={t("submit.pathLabel")} htmlFor="active-file-path">
                <input
                  id="active-file-path"
                  data-testid="active-file-path"
                  value={files[activeIndex].path}
                  onChange={(event) => {
                    const next = event.target.value;
                    setFiles((previous) =>
                      previous.map((file, index) =>
                        index === activeIndex ? { ...file, path: next } : file,
                      ),
                    );
                  }}
                />
              </Field>
              <CodeEditor
                value={files[activeIndex].content}
                language={version.data.language}
                loader={editorLoader}
                onChange={(next) => {
                  setFiles((previous) =>
                    previous.map((file, index) =>
                      index === activeIndex ? { ...file, content: next } : file,
                    ),
                  );
                }}
              />
              {files.length > 1 ? (
                <button
                  type="button"
                  className="button button--danger"
                  onClick={() => {
                    setFiles((previous) => previous.filter((_, index) => index !== activeIndex));
                    setActiveIndex(0);
                  }}
                >
                  {t("submit.removeFile")}
                </button>
              ) : null}
            </div>
          ) : null}

          <p className="muted">
            {t("submit.limits", {
              files: limits.maxFiles ?? "—",
              bytes: ENVELOPE_MAX_BASE64_LENGTH,
              total: limits.maxTotalBytes ?? "—",
            })}
            {" · "}
            {totalBytes} B
          </p>

          {draftInfo ? (
            <p className="muted" data-testid="draft-saved">
              {t("submit.draftSaved", {
                time: formatDateTime(draftInfo.savedAt, t("common.none")),
              })}{" "}
              {t("submit.draftNotSubmitted")} {t("submit.draftClearedOnSignOut")}
            </p>
          ) : null}

          {issues.length > 0 ? (
            <Banner tone="error" testId="validation-issues">
              <ul>
                {issues.map((issue) => (
                  <li key={issue}>{issue}</li>
                ))}
              </ul>
            </Banner>
          ) : null}

          <div className="inline">
            <button
              type="button"
              className="button"
              data-testid="submit-answer"
              disabled={!canSubmit}
              onClick={async () => {
                const localIssues = validateSubmission(files, limits).map(issueMessage);
                if (localIssues.length > 0) {
                  setIssues(localIssues);
                  return;
                }
                setIssues([]);

                const requestId = newRequestId();
                const ownerId = identity.studentId as string;
                const envelope = await buildSubmissionEnvelope({
                  submissionId: newIdempotencyKey(),
                  studentId: ownerId,
                  courseId: courseId as string,
                  sectionId: sectionId as string,
                  questionVersionId,
                  // 同一份草稿重复提交复用同一幂等键：服务端返回同一 submission_id。
                  idempotencyKey: idempotencyKeyRef.current,
                  files,
                });
                log({
                  event: "submission_attempt",
                  request_id: requestId,
                  question_version_id: questionVersionId,
                  file_count: files.length,
                });
                submit.mutate(envelope, {
                  onSuccess: (accepted) => {
                    clearDraft(ownerId, questionVersionId);
                    setDraftInfo(null);
                    setRunId(accepted.evaluation_run_id ?? null);
                    // 新的提交重新生成幂等键：下一次点击代表另一次提交意图。
                    idempotencyKeyRef.current = newIdempotencyKey();
                  },
                });
              }}
            >
              {submit.isPending ? t("submit.submitting") : t("submit.button")}
            </button>
            <span className="muted">{t("submit.draftNotSubmitted")}</span>
          </div>

          {submit.isError ? (
            <Banner tone="error" testId="submit-error">
              {describeApiError(submit.error)}
            </Banner>
          ) : null}
          {submit.isSuccess ? (
            <Banner tone="success" testId="submit-accepted">
              {t("submit.accepted", { id: submit.data.submission_id })}
              {submit.data.idempotent_replay ? ` · ${t("submit.idempotentReplay")}` : ""}
            </Banner>
          ) : null}
          {submit.isSuccess && resolvedRunId === null ? (
            <Banner tone="warn" testId="no-run-id">
              {terminalNoRun ? t("submit.noRunId") : t("submit.resolvingRunId")}
            </Banner>
          ) : null}
        </Panel>
      ) : null}

      {runId ? (
        <Panel title={t("submit.progressTitle")}>
          <ProgressPanel progress={stream.progress} status={stream.status} error={stream.error} />
        </Panel>
      ) : null}
    </div>
  );
}
