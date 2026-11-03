import { useEffect, useMemo, useRef, useState } from "react";
import { t } from "../../i18n";
import { isApiError } from "../../api/problem";
import { newIdempotencyKey } from "../../api/client";
import { describeApiError } from "../../api/problem";
import { useSession } from "../../auth/sessionContext";
import { useQuestion, useQuestionVersion } from "../questions/useQuestions";
import { SafeMarkdown } from "../questions/SafeMarkdown";
import { useQuestionAsset } from "../questions/useQuestionAsset";
import { Banner, ErrorState, Field, Modal, PageTitle, Panel, Spinner } from "../../ui/components";
import { formatDateTime, shortenId } from "../../ui/format";
import { log, newRequestId } from "../../logging/logger";
import { CodeEditor } from "./CodeEditor";
import type { MonacoLoader } from "./monaco";
import { ProgressPanel } from "./ProgressPanel";
import { clearDraft, loadDraft, saveDraft } from "./draft";
import { useStudentIdentity } from "./identity";
import { useCreateSubmission, useEvaluationRunId, useSubmissionView } from "./useSubmission";
import { useEvaluationStream } from "./useEvaluationStream";
import { buildSubmissionEnvelope, validateSubmission, type SubmissionFile } from "./validation";
import "./submitLayout.css";

/**
 * 作答与提交页（RQ-B-1-50；P10）。
 *
 * 链路：**读题**（公开版本 DTO 的冻结题干）→ 本地草稿 → 提交前校验 → 构造
 * `submission-envelope`（含 `content_sha256`）→ `POST /api/v1/submissions`（带幂等键）→
 * 订阅 SSE 进度 → 终态跳到 P11 查看本次结果。
 *
 * 三条边界：
 * - **题干来自所选 `question_version`**（`stem_markdown` + `stem_assets`），不是题目的当前版本：
 *   版本是不可变快照，替换成最新题干会让历史版本显示错误内容而看起来正常；
 * - **草稿只在本机**，键按 `student_id + question_version_id` 隔离；恢复前先询问，绝不覆盖学生
 *   已经敲进去的内容；
 * - **进度只按服务端事件**（`sequence` 去重），未完成/被拒绝/系统失败都不显示 0 分。
 *
 * `initialSubmissionId` / `initialRunId` 来自 URL：受理后地址栏带上它们，刷新即可恢复状态与进度，
 * 不重复 POST。`onSubmitted` / `onOpenResult` 由路由层负责把这两个真实 id 写回 URL 或跳到 P11。
 */

const DEFAULT_FILE = "solution.py";

/** 单文件上限的**字节**数（契约按 base64 字符限制；页面只展示学生能理解的字节量）。 */
const ENVELOPE_MAX_FILE_BYTES = Math.floor((2_097_152 / 4) * 3);

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

/** 版本里的受控图片：与题干内嵌图片走同一个受控读取（`useQuestionAsset`），不用裸 `<img>` 外链。 */
function StemAsset({
  questionId,
  asset,
}: {
  questionId: string;
  asset: { asset_id: string; content_type: string; alt_text?: string | null };
}) {
  const src = `/api/v1/questions/${questionId}/assets/${asset.asset_id}`;
  const state = useQuestionAsset(src);
  const label = asset.alt_text && asset.alt_text !== "" ? asset.alt_text : t("questions.assetAltFallback");
  if (state.status === "loading") {
    return <span className="muted">{t("common.loading")}</span>;
  }
  if (state.status === "forbidden") {
    return <span className="muted">{t("questions.assetForbidden")}</span>;
  }
  if (state.status === "unavailable" || state.status === "not-asset") {
    return <span className="muted">{t("questions.assetUnavailable")}</span>;
  }
  return <img src={state.url} alt={label} loading="lazy" referrerPolicy="no-referrer" />;
}

export function SubmitPage({
  questionVersionId,
  editorLoader,
  initialSubmissionId = null,
  initialRunId = null,
  onSubmitted,
  onOpenResult,
  onOpenGrades,
  onOpenQuestion,
  onBackToAssignments,
}: {
  questionVersionId: string;
  /** 测试注入的编辑器加载器；生产环境使用真实 Monaco。 */
  editorLoader?: MonacoLoader;
  /** URL 里的 `submission_id`（受理后写入；刷新时恢复状态）。 */
  initialSubmissionId?: string | null;
  /** URL 里的 `evaluation_run_id`（终态结果深链）。 */
  initialRunId?: string | null;
  onSubmitted?: (ids: { submissionId: string; evaluationRunId: string | null }) => void;
  onOpenResult?: (evaluationRunId: string) => void;
  onOpenGrades?: () => void;
  onOpenQuestion?: (questionId: string, questionVersionId: string) => void;
  onBackToAssignments?: () => void;
}) {
  const session = useSession();
  const version = useQuestionVersion(questionVersionId);
  const question = useQuestion(version.data?.question_id ?? "");

  const scope = session.scope[0] ?? null;
  const courseId = scope?.course_id ?? null;
  const sectionId = scope?.section_id ?? null;
  const identity = useStudentIdentity(courseId, sectionId);

  const [files, setFiles] = useState<SubmissionFile[]>([
    { path: DEFAULT_FILE, content: "" },
  ]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [issues, setIssues] = useState<string[]>([]);
  const [runId, setRunId] = useState<string | null>(initialRunId);
  const [draftInfo, setDraftInfo] = useState<{ savedAt: string } | null>(null);
  const [restoredAt, setRestoredAt] = useState<string | null>(null);
  const [draftCandidate, setDraftCandidate] = useState<{
    files: SubmissionFile[];
    savedAt: string;
  } | null>(null);
  const [pendingRemove, setPendingRemove] = useState<number | null>(null);
  const idempotencyKeyRef = useRef<string>(newIdempotencyKey());
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const draftRestoreAttempted = useRef(false);

  const stream = useEvaluationStream(runId);
  const submit = useCreateSubmission();
  const restoredSubmission = useSubmissionView(initialSubmissionId);

  // 从 URL 恢复受理状态：拿到 run id 就订阅真实进度，绝不在刷新后重复 POST。
  const restoredRunId = restoredSubmission.data?.evaluation_run_id ?? null;
  useEffect(() => {
    if (restoredRunId && restoredRunId !== runId) setRunId(restoredRunId);
  }, [restoredRunId, runId]);

  // 202 里的 run id 合法为 null（运行行在受理后才创建），因此轮询提交视图把它取回来。
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

  // 202 里没有 run id 时，受理后先写入 submission_id；轮询拿到真实 run id 后**再补一次** URL，
  // 这样刷新/分享的地址同时带着两个真实 id（而不是只恢复一半状态）。
  const reportedRunId = useRef<string | null>(null);
  useEffect(() => {
    if (!submit.isSuccess || !resolvedRunId) return;
    if (reportedRunId.current === resolvedRunId) return;
    reportedRunId.current = resolvedRunId;
    onSubmitted?.({
      submissionId: submit.data.submission_id,
      evaluationRunId: resolvedRunId,
    });
  }, [submit.isSuccess, submit.data, resolvedRunId, onSubmitted]);

  // 恢复本地未提交草稿（草稿≠已提交）。**先问再恢复**：直接覆盖会丢掉学生刚敲的内容。
  useEffect(() => {
    const ownerId = identity.studentId;
    if (ownerId === null || draftRestoreAttempted.current) return;
    draftRestoreAttempted.current = true;
    const draft = loadDraft(ownerId, questionVersionId);
    if (draft && draft.files.length > 0) {
      setDraftCandidate({ files: draft.files, savedAt: draft.savedAt });
    }
  }, [identity.studentId, questionVersionId]);

  // 自动保存草稿（防抖）。恢复询问未决时不写：否则会把待恢复的那份草稿覆盖成空编辑器。
  useEffect(() => {
    const ownerId = identity.studentId;
    if (!version.data || ownerId === null || draftCandidate !== null) return;
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
  }, [files, identity.studentId, questionVersionId, version.data, draftCandidate]);

  const limits = useMemo(
    () => ({
      maxFiles: version.data?.max_files ?? null,
      maxTotalBytes: version.data?.max_total_bytes ?? null,
    }),
    [version.data],
  );

  const totalBytes = useMemo(
    () => files.reduce((sum, file) => sum + new TextEncoder().encode(file.content).length, 0),
    [files],
  );

  const canSubmit =
    version.data !== undefined &&
    version.data.status === "published" &&
    identity.studentId !== null &&
    courseId !== null &&
    sectionId !== null &&
    !submit.isPending;

  const terminalState =
    stream.progress.terminal ?? restoredSubmission.data?.state ?? submit.data?.state ?? null;
  const terminal = terminalState === "completed" || terminalState === "rejected" || terminalState === "failed";

  const addFile = () => {
    const max = limits.maxFiles ?? 100;
    if (files.length >= max) {
      setIssues([t("submit.validate.tooManyFiles", { count: files.length + 1, max })]);
      return;
    }
    setIssues([]);
    setFiles((previous) => [...previous, { path: `file${previous.length + 1}.txt`, content: "" }]);
    setActiveIndex(files.length);
  };

  return (
    <div className="stack">
      <PageTitle>{question.data?.title ?? t("submit.title")}</PageTitle>
      <p className="muted" data-testid="submit-meta">
        {version.data ? (
          <>
            {t("questions.version", { version: version.data.version })} · {t("questions.language")}：
            {t(`questions.language.${version.data.language}`)} ·{" "}
            {t("submit.limitsDynamic", {
              files: version.data.max_files ?? "—",
              bytes: ENVELOPE_MAX_FILE_BYTES,
              total: version.data.max_total_bytes ?? "—",
            })}
          </>
        ) : (
          t("common.loading")
        )}
      </p>

      {version.isPending ? <Spinner /> : null}
      {version.isError ? <ErrorState error={version.error} /> : null}

      {version.data && version.data.status !== "published" ? (
        <Banner tone="warn" testId="version-not-published">
          {t("submit.versionNotPublished")}
        </Banner>
      ) : null}

      {version.data && version.data.status === "published" ? (
        <>
          {restoredSubmission.data ? (
            <Banner tone="info" testId="submission-restored">
              {t("submit.submissionRestored", {
                id: shortenId(restoredSubmission.data.submission_id),
                state: t(`grades.state.${restoredSubmission.data.state}`),
              })}
            </Banner>
          ) : null}

          {restoredAt ? (
            <Banner tone="info" testId="draft-restored">
              {t("submit.draftRestored", {
                time: formatDateTime(restoredAt, t("common.none")),
              })}{" "}
              {t("submit.draftNotSubmitted")}
            </Banner>
          ) : null}

          <div className="submit-layout">
            <Panel
              title={t("submit.stemTitle")}
              testId="submit-stem"
              actions={
                <button
                  type="button"
                  className="button button--ghost"
                  data-testid="open-question"
                  onClick={() =>
                    version.data
                      ? onOpenQuestion?.(version.data.question_id, questionVersionId)
                      : undefined
                  }
                >
                  {t("submit.openQuestion")}
                </button>
              }
            >
              <div className="submit-stem" data-testid="stem">
                <SafeMarkdown source={version.data.stem_markdown} testId="stem-markdown" />
                {(version.data.stem_assets ?? []).map((asset) => (
                  <div key={asset.asset_id} className="stack">
                    <StemAsset questionId={version.data?.question_id ?? ""} asset={asset} />
                  </div>
                ))}
              </div>
            </Panel>

            <Panel title={t("submit.editorTitle")} testId="submit-editor">
              <div className="stack submit-editor">
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
                  <button
                    type="button"
                    className="button button--ghost"
                    data-testid="add-file"
                    onClick={addFile}
                  >
                    {t("submit.addFile")}
                  </button>
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
                        data-testid="remove-file"
                        onClick={() => setPendingRemove(activeIndex)}
                      >
                        {t("submit.removeFile")}
                      </button>
                    ) : null}
                  </div>
                ) : null}

                <p className="muted" data-testid="submit-limits">
                  {t("submit.limitsDynamic", {
                    files: limits.maxFiles ?? "—",
                    bytes: ENVELOPE_MAX_FILE_BYTES,
                    total: limits.maxTotalBytes ?? "—",
                  })}
                  {" · "}
                  {t("submit.totalBytes", { bytes: totalBytes })}
                </p>

                {draftInfo ? (
                  <p className="muted" data-testid="draft-saved">
                    {t("submit.draftSaved", {
                      time: formatDateTime(draftInfo.savedAt, t("common.none")),
                    })}{" "}
                    {t("submit.draftNotSubmitted")}
                  </p>
                ) : (
                  <p className="muted">{t("submit.draftNotSubmitted")}</p>
                )}

                {issues.length > 0 ? (
                  <Banner tone="error" testId="validation-issues">
                    <ul>
                      {issues.map((issue) => (
                        <li key={issue}>{issue}</li>
                      ))}
                    </ul>
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
                          idempotencyKeyRef.current = newIdempotencyKey();
                          onSubmitted?.({
                            submissionId: accepted.submission_id,
                            evaluationRunId: accepted.evaluation_run_id ?? null,
                          });
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
                    {isApiError(submit.error) && submit.error.status === 401
                      ? t("submit.sessionExpired")
                      : describeApiError(submit.error)}
                  </Banner>
                ) : null}
                {submit.isSuccess ? (
                  <Banner tone="success" testId="submit-accepted">
                    {t("submit.accepted", {
                      id: shortenId(submit.data.submission_id),
                      state: t(`grades.state.${submit.data.state}`),
                    })}
                    {submit.data.idempotent_replay ? ` · ${t("submit.idempotentReplay")}` : ""}
                  </Banner>
                ) : null}
                {submit.isSuccess && resolvedRunId === null && !terminalNoRun ? (
                  <Banner tone="warn" testId="no-run-id">
                    {t("submit.resolvingRunId")}
                  </Banner>
                ) : null}
              </div>
            </Panel>
          </div>

          <div className="inline">
            <button
              type="button"
              className="button button--ghost"
              data-testid="back-to-assignments"
              onClick={() => onBackToAssignments?.()}
            >
              {t("submit.backToAssignments")}
            </button>
            <span className="muted">{t("submit.stemFrozenNotice")}</span>
          </div>
        </>
      ) : null}

      {runId ? (
        <Panel title={t("submit.progressTitle")} testId="progress-section">
          <ProgressPanel progress={stream.progress} status={stream.status} error={stream.error} />
          <div className="inline">
            {terminal && runId && terminalState === "completed" ? (
              <button
                type="button"
                className="button"
                data-testid="open-result"
                onClick={() => onOpenResult?.(runId)}
              >
                {t("submit.openResult")}
              </button>
            ) : null}
          </div>
        </Panel>
      ) : null}

      {terminal && !runId && (terminalState === "rejected" || terminalState === "failed") ? (
        <Banner tone={terminalState === "rejected" ? "warn" : "error"} testId={`terminal-${terminalState}`}>
          {t(`submit.terminal.${terminalState}`)}
          <span className="muted"> {t("submit.noRunId")}</span>
          <button
            type="button"
            className="button button--ghost"
            data-testid="open-grades"
            onClick={() => onOpenGrades?.()}
          >
            {t("submit.openGrades")}
          </button>
        </Banner>
      ) : null}

      {draftCandidate ? (
        <Modal title={t("submit.restoreDraftTitle")} testId="restore-draft" onClose={declineDraft}>
          <p>
            {t("submit.restoreDraftBody", {
              time: formatDateTime(draftCandidate.savedAt, t("common.none")),
            })}
          </p>
          <div className="inline">
            <button
              type="button"
              className="button"
              data-testid="restore-draft-confirm"
              onClick={() => {
                setFiles(draftCandidate.files);
                setRestoredAt(draftCandidate.savedAt);
                setDraftCandidate(null);
              }}
            >
              {t("submit.restoreDraftButton")}
            </button>
            <button
              type="button"
              className="button button--ghost"
              data-testid="restore-draft-decline"
              onClick={declineDraft}
            >
              {t("submit.declineDraftButton")}
            </button>
          </div>
          <p className="muted">{t("submit.draftNotSubmitted")}</p>
        </Modal>
      ) : null}

      {pendingRemove !== null && files[pendingRemove] ? (
        <Modal
          title={t("submit.removeFileTitle")}
          testId="remove-file-confirm"
          onClose={() => setPendingRemove(null)}
        >
          <p>{t("submit.removeFileBody", { path: files[pendingRemove].path })}</p>
          <div className="inline">
            <button
              type="button"
              className="button button--danger"
              data-testid="remove-file-confirm-button"
              onClick={() => {
                setFiles((previous) => previous.filter((_, index) => index !== pendingRemove));
                setActiveIndex(0);
                setPendingRemove(null);
              }}
            >
              {t("submit.removeFileConfirm")}
            </button>
            <button
              type="button"
              className="button button--ghost"
              onClick={() => setPendingRemove(null)}
            >
              {t("common.cancel")}
            </button>
          </div>
        </Modal>
      ) : null}
    </div>
  );

  function declineDraft() {
    const ownerId = identity.studentId;
    if (ownerId !== null) clearDraft(ownerId, questionVersionId);
    setDraftCandidate(null);
  }
}
