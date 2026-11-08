import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { t } from "../../i18n";
import { describeApiError, isApiError } from "../../api/problem";
import { newIdempotencyKey } from "../../api/client";
import type { AssignmentPublic } from "../../api/types";
import { useSections } from "../../api/directory";
import { useCurrentUser } from "../../api/currentUser";
import { useSession } from "../../auth/sessionContext";
import { log, newRequestId } from "../../logging/logger";
import {
  Banner,
  ErrorState,
  InlineLoader,
  Modal,
  Panel,
  StatusPill,
} from "../../ui/components";
import { formatDateTime } from "../../ui/format";
import {
  overallStateLabel,
  overallStateTone,
  questionStateLabel,
  questionStateTone,
} from "../assignments/progressDisplay";
import { useStudentAssignmentProgress } from "../assignments/useAssignmentProgress";
import { SafeMarkdown } from "../questions/SafeMarkdown";
import { useQuestionAsset } from "../questions/useQuestionAsset";
import { useQuestionVersionRows } from "../questions/useQuestions";
import { EmailSubmissionPanel } from "./EmailSubmissionPanel";
import { clearDraft, loadDraft, saveDraft } from "./draft";
import { useStudentIdentity } from "./identity";
import { ProgressPanel } from "./ProgressPanel";
import { useEvaluationStream } from "./useEvaluationStream";
import { useCreateSubmission, useEvaluationRunId } from "./useSubmission";
import {
  answerFileName,
  buildSubmissionEnvelope,
  validateAnswer,
  type AnswerValidationIssue,
} from "./validation";
import "./workspaceLayout.css";

/**
 * 学生作业工作区（CHG-0032 §7.2，替换旧的单题作答页 `/submit/{question_version_id}`）。
 *
 * 结构：
 * - 左侧：题目列表（1 基序号、标题、每题状态：待提交/处理中/已完成/失败）；
 * - 右侧当前题：冻结题干（受控图片）→ 答案区（textarea 粘贴或选择 `.txt`）→ 提交本题；
 * - 顶部：作业标题、截止时间、总体进度、邮件提交入口。
 *
 * 三条边界：
 * - **一次提交 = 一道题、一个 `N.txt`**（N 是作业里的 1 基题号）；
 * - **草稿只在本机**，键含 owner + assignment + question version；恢复前先询问；
 * - **状态只来自服务端成绩行**：成绩没取全时每题状态显示"加载中"，绝不显示"待提交"；
 *   系统故障（5xx/网络）显示"系统未完成"，不显示 0 分或未交。
 */

/** 版本里的受控图片：与题干内嵌图片走同一个受控读取，不用裸 `<img>` 外链。 */
function StemAsset({
  questionId,
  asset,
}: {
  questionId: string;
  asset: { asset_id: string; content_type: string; alt_text?: string | null };
}) {
  const src = `/api/v1/questions/${questionId}/assets/${asset.asset_id}`;
  const state = useQuestionAsset(src);
  const label =
    asset.alt_text && asset.alt_text !== ""
      ? asset.alt_text
      : t("questions.assetAltFallback");
  if (state.status === "loading") {
    return (
      <span className="muted">
        <InlineLoader />
      </span>
    );
  }
  if (state.status === "forbidden") {
    return <span className="muted">{t("questions.assetForbidden")}</span>;
  }
  if (state.status === "unavailable" || state.status === "not-asset") {
    return <span className="muted">{t("questions.assetUnavailable")}</span>;
  }
  return <img src={state.url} alt={label} loading="lazy" referrerPolicy="no-referrer" />;
}

function issueMessage(issue: AnswerValidationIssue): string {
  switch (issue.code) {
    case "empty":
      return t("workspace.validate.empty");
    case "not_txt":
      return t("workspace.validate.notTxt", { name: issue.fileName });
    case "too_large":
      return t("workspace.validate.tooLarge", { max: issue.max });
  }
}

function ApiErrorBanner({ error, testId }: { error: unknown; testId: string }) {
  const message =
    isApiError(error) && error.status === 401
      ? t("submit.sessionExpired")
      : isApiError(error) && error.status === 403
        ? t("workspace.submitForbidden")
        : isApiError(error) && error.status === 422
          ? (error.problem?.detail ?? describeApiError(error))
          : isApiError(error) && error.status >= 500
            ? t("workspace.systemFailure")
            : describeApiError(error);
  return (
    <Banner tone="error" testId={testId}>
      {message}
    </Banner>
  );
}

export function AssignmentWorkspace({
  assignment,
  initialQuestionVersionId = null,
}: {
  assignment: AssignmentPublic;
  initialQuestionVersionId?: string | null;
}) {
  const session = useSession();
  const queryClient = useQueryClient();
  const currentUser = useCurrentUser();
  const sections = useSections(assignment.course_id);
  const rows = useQuestionVersionRows(assignment.question_version_ids);
  const submit = useCreateSubmission();

  const courseId = assignment.course_id;
  const mySectionId = useMemo(() => {
    const match = session.scope.find(
      (scope) =>
        scope.course_id === courseId &&
        (scope.section_id ?? null) !== null &&
        assignment.section_ids.includes(scope.section_id as string),
    );
    return match?.section_id ?? null;
  }, [session.scope, courseId, assignment.section_ids]);
  const sectionLabel = useMemo(
    () => sections.data?.find((option) => option.sectionId === mySectionId)?.label ?? null,
    [sections.data, mySectionId],
  );

  const identity = useStudentIdentity(courseId, mySectionId);
  const progress = useStudentAssignmentProgress(assignment, courseId, mySectionId);
  const progressByQuestion = useMemo(
    () => new Map(progress.questions.map((question) => [question.questionVersionId, question])),
    [progress.questions],
  );

  const versions = assignment.question_version_ids;
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(() => {
    if (initialQuestionVersionId !== null && versions.includes(initialQuestionVersionId)) {
      return initialQuestionVersionId;
    }
    return versions[0] ?? null;
  });
  useEffect(() => {
    if (selectedVersionId !== null && versions.includes(selectedVersionId)) return;
    setSelectedVersionId(
      initialQuestionVersionId !== null && versions.includes(initialQuestionVersionId)
        ? initialQuestionVersionId
        : (versions[0] ?? null),
    );
  }, [versions, selectedVersionId, initialQuestionVersionId]);

  const currentRow = rows.find((row) => row.questionVersionId === selectedVersionId) ?? null;
  const currentProgress = currentRow
    ? (progressByQuestion.get(currentRow.questionVersionId) ?? null)
    : null;
  const currentSubmission = currentProgress?.submission ?? null;
  // 终态后不再挂着"正在准备评测"：成绩行已经给出结论，继续显示会让用户以为还在跑。
  const currentSettled =
    currentSubmission !== null &&
    ["completed", "rejected", "failed"].includes(currentSubmission.state);

  // ---- 本地草稿 v2 -------------------------------------------------------
  const [content, setContent] = useState("");
  const [uploadedFileName, setUploadedFileName] = useState<string | null>(null);
  const [issues, setIssues] = useState<string[]>([]);
  const [draftSavedAt, setDraftSavedAt] = useState<string | null>(null);
  const [restoredAt, setRestoredAt] = useState<string | null>(null);
  const [draftCandidate, setDraftCandidate] = useState<{
    content: string;
    updatedAt: string;
  } | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loadedDraftKey = useRef<string | null>(null);
  const idempotencyKeys = useRef<Map<string, string>>(new Map());
  const ownerId = identity.studentId;

  // 切题时读取该题的草稿：先问再恢复，绝不覆盖学生已经敲进去的内容。
  useEffect(() => {
    if (ownerId === null || selectedVersionId === null) return;
    const key = `${ownerId}:${assignment.assignment_id}:${selectedVersionId}`;
    if (loadedDraftKey.current === key) return;
    loadedDraftKey.current = key;
    const draft = loadDraft(ownerId, assignment.assignment_id, selectedVersionId);
    setContent("");
    setUploadedFileName(null);
    setIssues([]);
    setDraftSavedAt(null);
    setRestoredAt(null);
    setDraftCandidate(
      draft !== null && draft.content.trim() !== ""
        ? { content: draft.content, updatedAt: draft.updatedAt }
        : null,
    );
  }, [ownerId, assignment.assignment_id, selectedVersionId]);

  const flushDraft = useCallback(() => {
    if (ownerId === null || selectedVersionId === null) return;
    if (content.trim() === "") return;
    saveDraft({
      ownerId,
      assignmentId: assignment.assignment_id,
      questionVersionId: selectedVersionId,
      content,
      updatedAt: new Date().toISOString(),
    });
    setDraftSavedAt(new Date().toISOString());
  }, [ownerId, selectedVersionId, content, assignment.assignment_id]);

  // 自动保存（800ms 防抖）。恢复询问未决时不写：否则会覆盖待恢复的那份草稿。
  useEffect(() => {
    if (ownerId === null || selectedVersionId === null || draftCandidate !== null) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      saveDraft({
        ownerId,
        assignmentId: assignment.assignment_id,
        questionVersionId: selectedVersionId,
        content,
        updatedAt: new Date().toISOString(),
      });
      setDraftSavedAt(new Date().toISOString());
    }, 800);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [content, ownerId, assignment.assignment_id, selectedVersionId, draftCandidate]);

  function selectQuestion(nextVersionId: string) {
    flushDraft();
    if (saveTimer.current) clearTimeout(saveTimer.current);
    // 换题时清掉上一题的提交结果横幅（mutation 状态是跨渲染保留的）。
    submit.reset();
    setSelectedVersionId(nextVersionId);
  }

  // ---- 提交与进度 -------------------------------------------------------
  const [runInfo, setRunInfo] = useState<{ versionId: string; runId: string } | null>(null);
  const [pendingSubmission, setPendingSubmission] = useState<{
    versionId: string;
    submissionId: string;
    acceptedRunId: string | null;
  } | null>(null);
  const runLookup = useEvaluationRunId(
    pendingSubmission?.submissionId ?? null,
    pendingSubmission?.acceptedRunId ?? null,
    false,
  );
  const stream = useEvaluationStream(runInfo?.runId ?? null);
  const terminal = stream.progress.terminal;
  const refreshGrades = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["grades"] });
  }, [queryClient]);

  useEffect(() => {
    if (terminal !== null) refreshGrades();
  }, [terminal, refreshGrades]);
  useEffect(() => {
    if (pendingSubmission === null) return;
    const data = runLookup.data;
    if (data?.evaluation_run_id) {
      setRunInfo({ versionId: pendingSubmission.versionId, runId: data.evaluation_run_id });
      setPendingSubmission(null);
      refreshGrades();
      return;
    }
    if (data && ["completed", "rejected", "failed"].includes(data.state)) {
      setPendingSubmission(null);
      refreshGrades();
    }
  }, [pendingSubmission, runLookup.data, refreshGrades]);

  const currentRunId = runInfo?.versionId === selectedVersionId ? runInfo.runId : null;
  const knownRunId = currentSubmission?.evaluation_run_id ?? null;

  const [emailOpen, setEmailOpen] = useState(false);

  const handleFile = async (file: File | undefined) => {
    if (!file) return;
    setIssues([]);
    if (!file.name.endsWith(".txt")) {
      setIssues([t("workspace.validate.notTxt", { name: file.name })]);
      return;
    }
    try {
      const text = await file.text();
      const found = validateAnswer({ fileName: file.name, content: text }).map(issueMessage);
      if (found.length > 0) {
        setIssues(found);
        return;
      }
      setContent(text);
      setUploadedFileName(file.name);
    } catch {
      setIssues([t("workspace.validate.readFailed")]);
    }
  };

  const currentOrdinal = currentRow?.ordinal ?? 1;
  const submitCurrent = async () => {
    if (currentRow === null || ownerId === null || mySectionId === null) return;
    const found = validateAnswer({ fileName: uploadedFileName, content }).map(issueMessage);
    if (found.length > 0) {
      setIssues(found);
      return;
    }
    setIssues([]);
    const questionVersionId = currentRow.questionVersionId;
    const idempotencyKey =
      idempotencyKeys.current.get(questionVersionId) ?? newIdempotencyKey();
    idempotencyKeys.current.set(questionVersionId, idempotencyKey);
    const requestId = newRequestId();
    const envelope = await buildSubmissionEnvelope({
      submissionId: newIdempotencyKey(),
      assignmentId: assignment.assignment_id,
      studentId: ownerId,
      courseId,
      sectionId: mySectionId,
      questionVersionId,
      ordinal: currentRow.ordinal,
      content,
      idempotencyKey,
    });
    log({
      event: "submission_attempt",
      request_id: requestId,
      assignment_id: assignment.assignment_id,
      question_version_id: questionVersionId,
      file_path: answerFileName(currentRow.ordinal),
    });
    submit.mutate(envelope, {
      onSuccess: (accepted) => {
        // 先停掉尚未触发的自动保存：否则提交成功后 800ms 的定时器会把刚清掉的草稿写回来。
        if (saveTimer.current) clearTimeout(saveTimer.current);
        clearDraft(ownerId, assignment.assignment_id, questionVersionId);
        setDraftSavedAt(null);
        setRestoredAt(null);
        idempotencyKeys.current.set(questionVersionId, newIdempotencyKey());
        if (accepted.evaluation_run_id) {
          setRunInfo({ versionId: questionVersionId, runId: accepted.evaluation_run_id });
        } else {
          setPendingSubmission({
            versionId: questionVersionId,
            submissionId: accepted.submission_id,
            acceptedRunId: null,
          });
        }
        refreshGrades();
      },
    });
  };

  const version = currentRow?.version ?? null;
  const versionsForMeta = assignment.question_version_ids.length;
  const overall = progress.overall;
  const completedCount = progress.questions.filter(
    (question) => question.state === "completed",
  ).length;

  return (
    <div className="stack">
      <Panel
        title={t("assignments.detailOverview")}
        testId="workspace-overview"
        actions={
          <button
            type="button"
            className="button button--ghost"
            data-testid="workspace-email-entry"
            onClick={() => setEmailOpen(true)}
          >
            {t("submit.email.entry")}
          </button>
        }
      >
        <div className="inline">
          <StatusPill tone={assignment.status === "open" ? "ok" : "idle"}>
            {t(`assignments.status.${assignment.status}`)}
          </StatusPill>
          <span className="muted" data-testid="workspace-due">
            {t("assignments.due")}：
            {assignment.due_at
              ? formatDateTime(assignment.due_at, t("common.none"))
              : t("assignments.noDue")}
          </span>
          <span className="muted">
            {t("assignments.versionCount")}：{versionsForMeta}
          </span>
          {progress.complete ? (
            <span className="inline" data-testid="workspace-overall">
              <span className="muted">
                {t("assignments.progressDone", {
                  done: completedCount,
                  total: progress.questions.length,
                })}
              </span>
              <StatusPill tone={overallStateTone(overall)}>
                {overallStateLabel(overall)}
              </StatusPill>
            </span>
          ) : progress.isError ? (
            <span className="muted" data-testid="workspace-progress-error">
              {t("workspace.progressUnavailable")}
            </span>
          ) : (
            <span className="muted" data-testid="workspace-progress-loading">
              <InlineLoader />
            </span>
          )}
        </div>
        {progress.truncated ? (
          <Banner tone="warn" testId="workspace-progress-truncated">
            {t("assignments.gradesTruncated")}
          </Banner>
        ) : null}
        {progress.isError ? <ErrorState error={progress.error} testId="workspace-grades-error" /> : null}
        {mySectionId === null ? (
          <Banner tone="error" testId="workspace-scope-missing">
            {t("workspace.scopeMissing")}
          </Banner>
        ) : null}
      </Panel>

      <div className="assignment-workspace">
        <Panel
          title={t("workspace.questionsTitle")}
          testId="workspace-question-list"
          className="assignment-workspace__questions"
        >
          <ul className="workspace-question-list">
            {rows.map((row) => {
              const questionProgress = progressByQuestion.get(row.questionVersionId) ?? null;
              const active = row.questionVersionId === selectedVersionId;
              const title =
                row.question?.title ??
                (row.isPending ? "—" : t("questions.titleUnavailable"));
              return (
                <li key={row.questionVersionId}>
                  <button
                    type="button"
                    className={
                      active
                        ? "workspace-question workspace-question--active"
                        : "workspace-question"
                    }
                    aria-current={active ? "true" : undefined}
                    data-testid={`workspace-question-${row.ordinal}`}
                    onClick={() => selectQuestion(row.questionVersionId)}
                  >
                    <span className="workspace-question__ordinal">{row.ordinal}.</span>
                    <span className="workspace-question__title">{title}</span>
                    <span className="workspace-question__status">
                      {progress.complete ? (
                        <StatusPill
                          tone={questionStateTone(questionProgress?.state ?? "not_submitted")}
                          testId={`workspace-question-state-${row.ordinal}`}
                        >
                          {questionStateLabel(questionProgress?.state ?? "not_submitted")}
                        </StatusPill>
                      ) : (
                        // 成绩未取全：不显示"待提交"，那是没有依据的结论。
                        <span className="muted" data-testid={`workspace-question-state-${row.ordinal}`}>
                          <InlineLoader />
                        </span>
                      )}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </Panel>

        <div className="stack">
          <Panel title={t("submit.stemTitle")} testId="workspace-stem">
            {currentRow === null ? (
              <p className="muted">{t("assignments.noVersions")}</p>
            ) : currentRow.error ? (
              <ErrorState error={currentRow.error} testId={`workspace-version-error-${currentOrdinal}`} />
            ) : currentRow.isPending || version === null ? (
              <InlineLoader />
            ) : (
              <div className="stack">
                <h2 className="panel__title" data-testid="workspace-question-title">
                  {currentOrdinal}. {currentRow.question?.title ?? t("questions.titleUnavailable")}
                </h2>
                {version.status !== "published" ? (
                  <Banner tone="warn" testId="workspace-version-not-published">
                    {t("submit.versionNotPublished")}
                  </Banner>
                ) : null}
                <SafeMarkdown source={version.stem_markdown} testId="stem-markdown" />
                {(version.stem_assets ?? []).map((asset) => (
                  <div key={asset.asset_id} className="stack">
                    <StemAsset questionId={version.question_id} asset={asset} />
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel title={t("workspace.answerTitle")} testId="workspace-answer">
            <div className="stack">
              <p className="muted workspace-answer__mapping" data-testid="workspace-file-mapping">
                {t("workspace.fileMapping", {
                  ordinal: currentOrdinal,
                  fileName: answerFileName(currentOrdinal),
                })}
              </p>

              <div className="workspace-answer__file-row">
                <label className="field">
                  <span>{t("workspace.chooseFile")}</span>
                  <input
                    type="file"
                    accept=".txt,text/plain"
                    data-testid="answer-file-input"
                    disabled={submit.isPending}
                    onClick={(event) => {
                      // 允许重复选择同一个文件；文件名仍由原生控件显示在框内。
                      event.currentTarget.value = "";
                    }}
                    onChange={(event) => {
                      void handleFile(event.target.files?.[0]);
                    }}
                  />
                </label>
              </div>

              <label className="field" htmlFor="workspace-answer-text">
                <span>{t("workspace.answerLabel")}</span>
                <textarea
                  id="workspace-answer-text"
                  className="input workspace-answer__textarea"
                  data-testid="workspace-answer-text"
                  value={content}
                  disabled={submit.isPending}
                  placeholder={t("workspace.answerPlaceholder")}
                  onChange={(event) => {
                    setContent(event.target.value);
                    setUploadedFileName(null);
                  }}
                />
              </label>

              {draftSavedAt !== null ? (
                <p className="muted" data-testid="workspace-draft-saved">
                  {t("submit.draftSaved", {
                    time: formatDateTime(draftSavedAt, t("common.none")),
                  })}
                </p>
              ) : null}

              {restoredAt !== null ? (
                <Banner tone="info" testId="workspace-draft-restored">
                  {t("submit.draftRestored", {
                    time: formatDateTime(restoredAt, t("common.none")),
                  })}
                </Banner>
              ) : null}

              {issues.length > 0 ? (
                <Banner tone="error" testId="workspace-validation-issues">
                  <ul>
                    {issues.map((issue) => (
                      <li key={issue}>{issue}</li>
                    ))}
                  </ul>
                </Banner>
              ) : null}

              {ownerId === null ? (
                <Banner tone="error" testId="workspace-identity-unavailable">
                  {t("submit.identityUnavailable")}
                </Banner>
              ) : null}

              <div className="inline">
                <button
                  type="button"
                  className="button"
                  data-testid="workspace-submit"
                  disabled={
                    submit.isPending ||
                    ownerId === null ||
                    mySectionId === null ||
                    version === null ||
                    version.status !== "published" ||
                    content.trim() === ""
                  }
                  onClick={() => void submitCurrent()}
                >
                  {submit.isPending
                    ? t("submit.submitting")
                    : t("workspace.submitButton")}
                </button>
              </div>

              {submit.isError ? (
                <ApiErrorBanner error={submit.error} testId="workspace-submit-error" />
              ) : null}
              {submit.isSuccess && !currentSettled && terminal === null ? (
                <Banner tone="success" testId="workspace-submit-accepted">
                  {t("workspace.submitted")}
                  {submit.data.idempotent_replay ? ` · ${t("submit.idempotentReplay")}` : ""}
                </Banner>
              ) : null}
              {pendingSubmission !== null &&
              pendingSubmission.versionId === selectedVersionId ? (
                <Banner tone="info" testId="workspace-run-pending">
                  {t("submit.resolvingRunId")}
                </Banner>
              ) : null}

              {progress.complete && currentSubmission !== null ? (
                <div className="stack" data-testid="workspace-current-result">
                  <p>
                    {t("workspace.currentResult")}：
                    {currentSubmission.state === "completed" ? (
                      typeof currentSubmission.score === "number" ? (
                        <span className="muted">
                          {t("grades.score")}：{currentSubmission.score} /{" "}
                          {currentSubmission.max_score ?? "—"}
                          {" · "}
                        </span>
                      ) : (
                        <span className="muted">
                          {t("grades.notScored")}
                          {" · "}
                        </span>
                      )
                    ) : null}
                    <StatusPill
                      tone={questionStateTone(currentProgress?.state ?? "not_submitted")}
                    >
                      {questionStateLabel(currentProgress?.state ?? "not_submitted")}
                    </StatusPill>
                  </p>
                  <div className="inline">
                    <span className="muted">
                      {t("grades.submittedAt")}：
                      {formatDateTime(currentSubmission.submitted_at, t("common.none"))}
                    </span>
                    {knownRunId !== null ? (
                      <Link
                        className="button button--ghost"
                        to="/grades"
                        search={{ evaluation_run_id: knownRunId }}
                        data-testid="workspace-open-result"
                      >
                        {t("workspace.viewDetail")}
                      </Link>
                    ) : null}
                  </div>
                </div>
              ) : null}
            </div>
          </Panel>

          {currentRunId !== null ? (
            <Panel title={t("submit.progressTitle")} testId="progress-section">
              <ProgressPanel
                progress={stream.progress}
                status={stream.status}
                error={stream.error}
              />
              {terminal === "completed" ? (
                <div className="inline">
                  <Link
                    className="button"
                    to="/grades"
                    search={{ evaluation_run_id: currentRunId }}
                    data-testid="open-result"
                  >
                    {t("submit.openResult")}
                  </Link>
                </div>
              ) : null}
            </Panel>
          ) : null}
        </div>
      </div>

      {draftCandidate !== null ? (
        <Modal
          title={t("submit.restoreDraftTitle")}
          testId="restore-draft"
          onClose={() => {
            if (ownerId !== null && selectedVersionId !== null) {
              clearDraft(ownerId, assignment.assignment_id, selectedVersionId);
            }
            setDraftCandidate(null);
          }}
        >
          <p>
            {t("submit.restoreDraftBody", {
              time: formatDateTime(draftCandidate.updatedAt, t("common.none")),
            })}
          </p>
          <div className="inline">
            <button
              type="button"
              className="button"
              data-testid="restore-draft-confirm"
              onClick={() => {
                setContent(draftCandidate.content);
                setRestoredAt(draftCandidate.updatedAt);
                setUploadedFileName(null);
                setDraftCandidate(null);
              }}
            >
              {t("submit.restoreDraftButton")}
            </button>
            <button
              type="button"
              className="button button--ghost"
              data-testid="restore-draft-decline"
              onClick={() => {
                if (ownerId !== null && selectedVersionId !== null) {
                  clearDraft(ownerId, assignment.assignment_id, selectedVersionId);
                }
                setDraftCandidate(null);
              }}
            >
              {t("submit.declineDraftButton")}
            </button>
          </div>
        </Modal>
      ) : null}

      {emailOpen ? (
        <Modal
          title={t("submit.email.title")}
          testId="email-modal"
          onClose={() => setEmailOpen(false)}
        >
          <EmailSubmissionPanel
            assignment={assignment}
            courseId={courseId}
            sectionId={mySectionId}
            sectionLabel={sectionLabel}
            currentUser={currentUser.data}
            questionRows={rows}
            onReceived={refreshGrades}
          />
        </Modal>
      ) : null}
    </div>
  );
}
