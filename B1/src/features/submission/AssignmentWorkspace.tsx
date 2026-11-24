import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { Link } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { t } from "../../i18n";
import { describeApiError, isApiError } from "../../api/problem";
import { newIdempotencyKey } from "../../api/client";
import type {
  AssignmentPublic,
  DraftAiAssistantResponse,
} from "../../api/types";
import { useCurrentUser } from "../../api/currentUser";
import { log, newRequestId } from "../../logging/logger";
import {
  Banner,
  ErrorState,
  InlineLoader,
  Modal,
  Panel,
  StatusPill,
  Table,
} from "../../ui/components";
import { formatDateTime } from "../../ui/format";
import {
  overallStateLabel,
  overallStateTone,
  questionStateLabel,
  questionStateTone,
} from "../assignments/progressDisplay";
import { useStudentAssignmentProgress } from "../assignments/useAssignmentProgress";
import { CommentarySection } from "../commentary/CommentarySection";
import { SafeMarkdown } from "../questions/SafeMarkdown";
import { IoSpecPanel, IoTypeBadge } from "../questions/IoTypeBadge";
import { useQuestionAsset } from "../questions/useQuestionAsset";
import { useQuestionVersionRows } from "../questions/useQuestions";
import { EmailSubmissionPanel } from "./EmailSubmissionPanel";
import { EmailSubmissionStatus } from "./EmailSubmissionStatus";
import { clearDraft, loadDraft, saveDraft } from "./draft";
import { ProgressPanel } from "./ProgressPanel";
import type { GradeRow } from "../../api/types";

const STATE_KEY: Record<string, Parameters<typeof t>[0]> = {
  completed: "grades.state.completed",
  failed: "grades.state.failed",
  rejected: "grades.state.rejected",
  running: "grades.state.running",
  queued: "grades.state.queued",
  received: "grades.state.received",
  validated: "grades.state.validated",
};
import { useEvaluationStream } from "./useEvaluationStream";
import {
  useCreateSubmission,
  useDraftAiAssistant,
  useEvaluationRunId,
} from "./useSubmission";
import { useSubmissionFileText } from "./useSubmissionFile";
import { useSubmissionHistory } from "../grades/useSubmissionHistory";
import {
  answerFileName,
  buildSubmissionEnvelope,
  validateAnswer,
  type AnswerValidationIssue,
} from "./validation";
import "./workspaceLayout.css";

/**
 * LeetCode 风格学生作业工作台：
 * - 顶部：作业信息、整体进度、邮件提交入口、所有题目横向切换标签；
 * - 左侧：当前题目题干说明（独立滚动）+ 题目语言与受控图片；
 * - 右上：带行号的等宽代码编辑器（支持自动回填邮件/历史提交代码、`.txt` 导入与本地草稿）+「AI 助教」/「提交本题」；
 * - 右下：双标签反馈区（「AI 评价与评测结果」实时 SSE + AI 评语，「AI 助教」实时启发式提示）。
 */

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
  const queryClient = useQueryClient();
  const currentUser = useCurrentUser();
  const rows = useQuestionVersionRows(assignment.question_version_ids);
  const submit = useCreateSubmission();
  const assistant = useDraftAiAssistant();

  const membership = useMemo(
    () =>
      currentUser.data?.memberships.find(
        (row) => row.course_id === assignment.course_id,
      ) ?? null,
    [currentUser.data?.memberships, assignment.course_id],
  );
  const courseId = membership?.course_id ?? assignment.course_id;
  const mySectionId = membership?.section_id ?? null;
  const sectionLabel = membership?.section_code ?? null;

  const ownerId = currentUser.data?.user_id ?? null;
  const progress = useStudentAssignmentProgress(assignment, courseId, null);
  const progressByQuestion = useMemo(
    () => new Map(progress.questions.map((question) => [question.questionVersionId, question])),
    [progress.questions],
  );

  const versions = assignment.question_version_ids;
  const assignmentEnded =
    assignment.status !== "open" ||
    (assignment.due_at !== null &&
      assignment.due_at !== undefined &&
      Date.parse(assignment.due_at) <= Date.now());
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
  const currentOrdinal = currentRow?.ordinal ?? 1;
  const currentProgress = currentRow
    ? (progressByQuestion.get(currentRow.questionVersionId) ?? null)
    : null;
  const currentSubmission = currentProgress?.submission ?? null;

  // 拉取当前题最近一次提交的 `{ordinal}.txt` 内容（邮件提交与网页提交均会自动回填至编辑器）。
  const submittedFile = useSubmissionFileText(
    currentSubmission?.submission_id ?? null,
    answerFileName(currentOrdinal),
  );

  // ---- 编辑器与草稿状态 -------------------------------------------------
  const [content, setContent] = useState("");
  const [uploadedFileName, setUploadedFileName] = useState<string | null>(null);
  const [issues, setIssues] = useState<string[]>([]);
  const [draftSavedAt, setDraftSavedAt] = useState<string | null>(null);
  const [restoredAt, setRestoredAt] = useState<string | null>(null);
  const [syncedSubmissionId, setSyncedSubmissionId] = useState<string | null>(null);
  const [hasUserEdited, setHasUserEdited] = useState(false);
  const [draftCandidate, setDraftCandidate] = useState<{
    content: string;
    updatedAt: string;
  } | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loadedDraftKey = useRef<string | null>(null);
  const syncedSubmissionRef = useRef<{ versionId: string; submissionId: string } | null>(
    null,
  );
  const idempotencyKeys = useRef<Map<string, string>>(new Map());
  const gutterRef = useRef<HTMLDivElement | null>(null);

  // ---- 右下角双标签（AI 评价 vs AI 助教） ----------------------------------
  const [bottomTab, setBottomTab] = useState<"evaluation" | "assistant">("evaluation");
  const [assistantQuestion, setAssistantQuestion] = useState("");
  const [assistantReplies, setAssistantReplies] = useState<
    Record<string, DraftAiAssistantResponse>
  >({});

  // 切题时读取该题的本地草稿：先问再恢复，绝不静默覆盖尚未决策的内容。
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
    setHasUserEdited(false);
    setSyncedSubmissionId(null);
    syncedSubmissionRef.current = null;
    setAssistantQuestion("");
    assistant.reset();
    setDraftCandidate(
      draft !== null && draft.content.trim() !== ""
        ? { content: draft.content, updatedAt: draft.updatedAt }
        : null,
    );
  }, [ownerId, assignment.assignment_id, selectedVersionId, assistant]);

  // 自动回填：当存在服务端最新提交且未与用户当前编辑冲突时，将提交代码/邮件附件填入编辑器。
  useEffect(() => {
    if (
      selectedVersionId === null ||
      currentSubmission === null ||
      draftCandidate !== null ||
      hasUserEdited ||
      typeof submittedFile.data !== "string"
    ) {
      return;
    }
    const alreadySynced =
      syncedSubmissionRef.current?.versionId === selectedVersionId &&
      syncedSubmissionRef.current?.submissionId === currentSubmission.submission_id;
    if (alreadySynced) return;
    setContent(submittedFile.data);
    setUploadedFileName(answerFileName(currentOrdinal));
    setSyncedSubmissionId(currentSubmission.submission_id);
    syncedSubmissionRef.current = {
      versionId: selectedVersionId,
      submissionId: currentSubmission.submission_id,
    };
  }, [
    selectedVersionId,
    currentSubmission,
    draftCandidate,
    hasUserEdited,
    submittedFile.data,
    currentOrdinal,
  ]);

  const flushDraft = useCallback(() => {
    if (ownerId === null || selectedVersionId === null) return;
    if (!hasUserEdited || content.trim() === "") return;
    saveDraft({
      ownerId,
      assignmentId: assignment.assignment_id,
      questionVersionId: selectedVersionId,
      content,
      updatedAt: new Date().toISOString(),
    });
    setDraftSavedAt(new Date().toISOString());
  }, [ownerId, selectedVersionId, hasUserEdited, content, assignment.assignment_id]);

  // 自动保存（800ms 防抖）：只有学生手动编辑过的内容才落本地草稿，避免把服务端回填代码当成未交草稿。
  useEffect(() => {
    if (
      ownerId === null ||
      selectedVersionId === null ||
      draftCandidate !== null ||
      !hasUserEdited
    ) {
      return;
    }
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
  }, [content, ownerId, assignment.assignment_id, selectedVersionId, draftCandidate, hasUserEdited]);

  function selectQuestion(nextVersionId: string) {
    flushDraft();
    if (saveTimer.current) clearTimeout(saveTimer.current);
    submit.reset();
    setSelectedVersionId(nextVersionId);
  }

  // ---- 提交与实时评测流 --------------------------------------------------
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

  const refreshGradesAndFiles = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["grades"] });
    void queryClient.invalidateQueries({ queryKey: ["submission-file"] });
    void queryClient.invalidateQueries({ queryKey: ["evaluation-commentary"] });
  }, [queryClient]);

  useEffect(() => {
    if (terminal !== null) {
      refreshGradesAndFiles();
    }
  }, [terminal, refreshGradesAndFiles]);

  useEffect(() => {
    if (pendingSubmission === null) return;
    const data = runLookup.data;
    if (data?.evaluation_run_id) {
      setRunInfo({ versionId: pendingSubmission.versionId, runId: data.evaluation_run_id });
      setPendingSubmission(null);
      refreshGradesAndFiles();
      return;
    }
    if (data && ["completed", "rejected", "failed"].includes(data.state)) {
      setPendingSubmission(null);
      refreshGradesAndFiles();
    }
  }, [pendingSubmission, runLookup.data, refreshGradesAndFiles]);

  const currentRunId = runInfo?.versionId === selectedVersionId ? runInfo.runId : null;
  // 历史作答：选中的是"某一次提交"，选中后代码区与评测区都切到那一次。
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyPick, setHistoryPick] = useState<{
    submissionId: string;
    evaluationRunId: string | null;
    submittedAt: string | null;
    score: number | null;
    maxScore: number | null;
    state: string;
    isPass: boolean | null;
  } | null>(null);
  // 历史要"全部"：按 (作业, 题目, 本人) 精确查询并翻到末页，而不是从受分页上限约束的成绩行里筛。
  const history = useSubmissionHistory({
    courseId,
    assignmentId: assignment.assignment_id,
    questionVersionId: selectedVersionId,
    studentId: ownerId,
  });
  const historyRows = history.rows;
  const historyFile = useSubmissionFileText(
    historyPick?.submissionId ?? null,
    answerFileName(currentOrdinal),
  );

    // 「本题当前结果」必须与切换后的代码、AI 评价同源：有历史选中就用历史，否则用当前提交。
  const resultScore = historyPick?.score ?? currentSubmission?.score ?? null;
  const resultMaxScore = historyPick?.maxScore ?? currentSubmission?.max_score ?? null;
  const resultState = historyPick?.state ?? currentSubmission?.state ?? null;
  const resultSubmittedAt = historyPick?.submittedAt ?? currentSubmission?.submitted_at ?? null;
  const resultQuestionState =
    historyPick !== null
      ? historyPick.state === "completed"
        ? "completed"
        : historyPick.state === "failed" || historyPick.state === "rejected"
          ? "failed"
          : "evaluating"
      : (currentProgress?.state ?? "not_submitted");

  // 历史文本到位后写进编辑器：只影响显示，不会把历史内容当成新提交发出去。
  useEffect(() => {
    if (historyPick !== null && typeof historyFile.data === "string") {
      setContent(historyFile.data);
      setHasUserEdited(false);
    }
  }, [historyPick, historyFile.data]);

  const knownRunId = historyPick?.evaluationRunId ?? currentSubmission?.evaluation_run_id ?? null;
  const displayRunId = knownRunId ?? (terminal === "completed" ? currentRunId : null);

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
      setHasUserEdited(true);
      setSyncedSubmissionId(null);
      setUploadedFileName(file.name);
    } catch {
      setIssues([t("workspace.validate.readFailed")]);
    }
  };

  const handleEditorKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Tab") return;
    event.preventDefault();
    const el = event.currentTarget;
    const start = el.selectionStart;
    const end = el.selectionEnd;
    const next = `${content.slice(0, start)}    ${content.slice(end)}`;
    setContent(next);
    setHasUserEdited(true);
    setUploadedFileName(null);
    setSyncedSubmissionId(null);
    requestAnimationFrame(() => {
      el.selectionStart = el.selectionEnd = start + 4;
    });
  };

  const submitCurrent = async () => {
    if (currentRow === null || ownerId === null || mySectionId === null) return;
    const found = validateAnswer({ fileName: uploadedFileName, content }).map(issueMessage);
    if (found.length > 0) {
      setIssues(found);
      return;
    }
    setIssues([]);
    setBottomTab("evaluation");
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
        if (saveTimer.current) clearTimeout(saveTimer.current);
        clearDraft(ownerId, assignment.assignment_id, questionVersionId);
        setDraftSavedAt(null);
        setRestoredAt(null);
        setHasUserEdited(false);
        syncedSubmissionRef.current = {
          versionId: questionVersionId,
          submissionId: accepted.submission_id,
        };
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
        refreshGradesAndFiles();
      },
    });
  };

  const askAiAssistant = () => {
    if (selectedVersionId === null || ownerId === null) return;
    setBottomTab("assistant");
    assistant.mutate(
      {
        assignment_id: assignment.assignment_id,
        question_version_id: selectedVersionId,
        draft_code: content,
        student_question: assistantQuestion.trim() ? assistantQuestion.trim() : null,
      },
      {
        onSuccess: (reply) => {
          setAssistantReplies((prev) => ({
            ...prev,
            [selectedVersionId]: reply,
          }));
        },
      },
    );
  };

  const version = currentRow?.version ?? null;
  const versionsForMeta = assignment.question_version_ids.length;
  const overall = progress.overall;
  const completedCount = progress.questions.filter(
    (question) => question.state === "completed",
  ).length;
  const lineCount = Math.max(14, content.split("\n").length);
  const currentReply = selectedVersionId ? (assistantReplies[selectedVersionId] ?? null) : null;

  return (
    <div className="lc-workspace">
      {/* 顶部概览栏 */}
      <div className="lc-topbar" data-testid="workspace-overview">
        <div className="lc-topbar__meta">
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

        <div className="lc-topbar__actions">
          {/* 最近一次邮件提交的状态常驻在这里：关掉邮件面板/刷新页面后仍然看得到成败与原因。 */}
          <EmailSubmissionStatus assignmentId={assignment.assignment_id} />
          <button
            type="button"
            className="button button--ghost"
            data-testid="workspace-email-entry"
            onClick={() => setEmailOpen(true)}
          >
            {t("submit.email.entry")}
          </button>
        </div>
      </div>

      {progress.truncated ? (
        <Banner tone="warn" testId="workspace-progress-truncated">
          {t("assignments.gradesTruncated")}
        </Banner>
      ) : null}
      {progress.isError ? (
        <ErrorState error={progress.error} testId="workspace-grades-error" />
      ) : null}
      {mySectionId === null ? (
        <Banner tone="error" testId="workspace-scope-missing">
          {t("workspace.scopeMissing")}
        </Banner>
      ) : null}

      {/* 横向排列的作业题目切换栏 */}
      <nav
        className="lc-question-tabs-wrap"
        aria-label={t("workspace.questionsTitle")}
        data-testid="workspace-question-list"
      >
        <ul className="lc-question-tabs">
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
                      ? "lc-question-tab lc-question-tab--active"
                      : "lc-question-tab"
                  }
                  aria-current={active ? "true" : undefined}
                  data-testid={`workspace-question-${row.ordinal}`}
                  onClick={() => selectQuestion(row.questionVersionId)}
                >
                  <span className="lc-question-tab__ordinal">{row.ordinal}.</span>
                  <span className="lc-question-tab__title">{title}</span>
                  <span className="lc-question-tab__status">
                    {progress.complete ? (
                      <StatusPill
                        tone={questionStateTone(questionProgress?.state ?? "not_submitted")}
                        testId={`workspace-question-state-${row.ordinal}`}
                      >
                        {questionStateLabel(questionProgress?.state ?? "not_submitted")}
                      </StatusPill>
                    ) : (
                      <span
                        className="muted"
                        data-testid={`workspace-question-state-${row.ordinal}`}
                      >
                        <InlineLoader />
                      </span>
                    )}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* LeetCode 风格分屏区：左侧题干 + 右侧代码编辑与评价/AI助教 */}
      <div className="lc-split-pane">
        <div className="lc-left-pane">
          <Panel title={t("submit.stemTitle")} testId="workspace-stem">
            <div className="lc-left-pane__scroll stack">
              {currentRow === null ? (
                <p className="muted">{t("assignments.noVersions")}</p>
              ) : currentRow.error ? (
                isApiError(currentRow.error) && currentRow.error.status === 403 ? (
                  <Banner tone="warn" testId="workspace-version-unpublished">
                    {assignmentEnded
                      ? t("workspace.assignmentEnded")
                      : t("submit.versionNotPublished")}
                  </Banner>
                ) : (
                  <ErrorState
                    error={currentRow.error}
                    testId={`workspace-version-error-${currentOrdinal}`}
                  />
                )
              ) : currentRow.isPending || version === null ? (
                <InlineLoader />
              ) : (
                <div className="stack">
                  <div className="lc-stem-header">
                    <h2 className="panel__title" data-testid="workspace-question-title">
                      {currentOrdinal}.{" "}
                      {currentRow.question?.title ?? t("questions.titleUnavailable")}
                    </h2>
                    <span className="lc-lang-badge">{version.language}</span>
                    {version.io_type ? (
                      <IoTypeBadge
                        ioType={version.io_type}
                        testId="workspace-question-io-type"
                      />
                    ) : null}
                  </div>
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
                  {version.io_type ? (
                    <IoSpecPanel
                      ioType={version.io_type}
                      language={version.language}
                      questionVersionId={version.question_version_id}
                      ioSpec={version.io_spec}
                      visibleFixtures={version.visible_fixtures}
                      testId="workspace-io-spec"
                    />
                  ) : null}
                </div>
              )}
            </div>
          </Panel>
        </div>

        {historyOpen ? (
          <Modal
            title={t("workspace.historyTitle")}
            testId="workspace-history-modal"
            onClose={() => setHistoryOpen(false)}
          >
            <div className="stack">
              <p className="muted" data-testid="workspace-history-count">
                {history.isPending
                  ? t("common.loading")
                  : history.complete
                    ? t("workspace.historyTotal", { count: historyRows.length })
                    : t("workspace.historyPartial", { count: historyRows.length })}
              </p>
              {historyRows.length === 0 ? (
                <p className="muted">{t("workspace.historyEmpty")}</p>
              ) : (
                <Table<GradeRow>
                  testId="workspace-history-table"
                  label={t("workspace.historyTitle")}
                  rows={historyRows}
                  getRowKey={(row) => row.submission_id}
                  columns={[
                    {
                      key: "submitted_at",
                      header: t("grades.submittedAt"),
                      render: (row) => formatDateTime(row.submitted_at, t("common.none")),
                    },
                    {
                      key: "state",
                      header: t("grades.state"),
                      render: (row) => t(STATE_KEY[row.state] ?? "grades.state.completed"),
                    },
                    {
                      key: "score",
                      header: t("grades.scoreSlashMax"),
                      align: "right",
                      render: (row) =>
                        typeof row.score === "number"
                          ? `${row.score} / ${row.max_score ?? t("common.none")}`
                          : t("grades.unscored"),
                    },
                    {
                      key: "result",
                      header: t("grades.result"),
                      render: (row) =>
                        row.state === "completed"
                          ? row.is_pass === true
                            ? t("grades.pass")
                            : t("grades.fail")
                          : t("grades.notScored"),
                    },
                    {
                      key: "pick",
                      header: t("grades.colDetail"),
                      render: (row) => (
                        <button
                          type="button"
                          className="button button--ghost"
                          data-testid={`workspace-history-pick-${row.submission_id}`}
                          onClick={() => {
                            setHistoryPick({
                              submissionId: row.submission_id,
                              evaluationRunId: row.evaluation_run_id ?? null,
                              submittedAt: row.submitted_at ?? null,
                              score: typeof row.score === "number" ? row.score : null,
                              maxScore:
                                typeof row.max_score === "number" ? row.max_score : null,
                              state: row.state,
                              isPass: row.is_pass ?? null,
                            });
                            setHistoryOpen(false);
                          }}
                        >
                          {t("workspace.historyPick")}
                        </button>
                      ),
                    },
                  ]}
                />
              )}
            </div>
          </Modal>
        ) : null}

        <div className="lc-right-pane">
          {/* 右上：代码编辑器 */}
          <Panel title={t("workspace.editorTitle")} testId="workspace-answer">
            <div className="stack">
              <div className="lc-editor-toolbar">
                <div className="lc-editor-toolbar__file">
                  <span
                    className="muted workspace-answer__mapping"
                    data-testid="workspace-file-mapping"
                  >
                    {t("workspace.fileMapping", {
                      ordinal: currentOrdinal,
                      fileName: answerFileName(currentOrdinal),
                    })}
                  </span>
                </div>

                <button
                  type="button"
                  className="button button--ghost"
                  data-testid="workspace-history-open"
                  disabled={historyRows.length === 0}
                  onClick={() => setHistoryOpen(true)}
                >
                  {t("workspace.historyOpen", { count: historyRows.length })}
                </button>

                <label className="lc-editor-upload-btn">
                  <input
                    type="file"
                    accept=".txt,text/plain"
                    data-testid="answer-file-input"
                    disabled={submit.isPending}
                    onClick={(event) => {
                      event.currentTarget.value = "";
                    }}
                    onChange={(event) => {
                      void handleFile(event.target.files?.[0]);
                    }}
                  />
                </label>
              </div>

              {historyPick !== null ? (
                <Banner tone="warn" testId="workspace-history-banner">
                  <span>
                    {t("workspace.historyViewing", {
                      at: historyPick.submittedAt ?? t("common.none"),
                    })}
                  </span>
                  <button
                    type="button"
                    className="button button--ghost"
                    data-testid="workspace-history-exit"
                    onClick={() => {
                      setHistoryPick(null);
                      setContent(submittedFile.data ?? "");
                      setHasUserEdited(false);
                    }}
                  >
                    {t("workspace.historyExit")}
                  </button>
                </Banner>
              ) : null}

              {syncedSubmissionId !== null ? (
                <Banner tone="info" testId="workspace-synced-submission">
                  {t("workspace.syncedFromSubmission")}（{answerFileName(currentOrdinal)}）
                </Banner>
              ) : null}

              {typeof submittedFile.data === "string" &&
              content !== submittedFile.data &&
              currentSubmission !== null ? (
                <div className="inline">
                  <button
                    type="button"
                    className="button button--ghost"
                    data-testid="workspace-restore-submitted"
                    onClick={() => {
                      setContent(submittedFile.data ?? "");
                      setHasUserEdited(false);
                      setSyncedSubmissionId(currentSubmission.submission_id);
                      if (selectedVersionId) {
                        syncedSubmissionRef.current = {
                          versionId: selectedVersionId,
                          submissionId: currentSubmission.submission_id,
                        };
                      }
                    }}
                  >
                    {t("workspace.restoreSubmittedCode")}
                  </button>
                </div>
              ) : null}

              <label className="field" htmlFor="workspace-answer-text">
                <span className="sr-only">{t("workspace.answerLabel")}</span>
                <div className="lc-code-shell">
                  <div className="lc-code-gutter" ref={gutterRef} aria-hidden="true">
                    {Array.from({ length: lineCount }, (_, i) => (
                      <span key={i} className="lc-code-gutter__line">
                        {i + 1}
                      </span>
                    ))}
                  </div>
                  <textarea
                    id="workspace-answer-text"
                    className="workspace-answer__textarea"
                    data-testid="workspace-answer-text"
                    disabled={submit.isPending}
                    value={content}
                    placeholder={t("workspace.answerPlaceholder")}
                    onKeyDown={handleEditorKeyDown}
                    onScroll={(event) => {
                      if (gutterRef.current) {
                        gutterRef.current.scrollTop = event.currentTarget.scrollTop;
                      }
                    }}
                    onChange={(event) => {
                      setContent(event.target.value);
                      setHasUserEdited(true);
                      setUploadedFileName(null);
                      setSyncedSubmissionId(null);
                    }}
                  />
                </div>
              </label>

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

              {submit.isError ? (
                <ApiErrorBanner error={submit.error} testId="workspace-submit-error" />
              ) : null}

              <div className="lc-editor-footer">
                <div className="inline">
                  {draftSavedAt !== null ? (
                    <span className="muted" data-testid="workspace-draft-saved">
                      {t("submit.draftSaved", {
                        time: formatDateTime(draftSavedAt, t("common.none")),
                      })}
                    </span>
                  ) : null}
                  {restoredAt !== null ? (
                    <Banner tone="info" testId="workspace-draft-restored">
                      {t("submit.draftRestored", {
                        time: formatDateTime(restoredAt, t("common.none")),
                      })}
                    </Banner>
                  ) : null}
                </div>

                <div className="lc-editor-footer__actions">
                  <button
                    type="button"
                    className="button button--ghost lc-ai-button"
                    data-testid="workspace-ai-assistant"
                    disabled={
                      assistant.isPending ||
                      ownerId === null ||
                      version === null ||
                      selectedVersionId === null
                    }
                    onClick={askAiAssistant}
                  >
                    {assistant.isPending
                      ? t("workspace.aiAssistantGenerating")
                      : `✨ ${t("workspace.aiAssistantButton")}`}
                  </button>

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
                    {submit.isPending ? t("submit.submitting") : t("workspace.submitButton")}
                  </button>
                </div>
              </div>
            </div>
          </Panel>

          {/* 右下：AI 评价与评测结果 / AI 助教双标签面板 */}
          <Panel testId="workspace-bottom-panel">
            <div className="lc-bottom-tabs" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={bottomTab === "evaluation"}
                className={
                  bottomTab === "evaluation"
                    ? "lc-bottom-tab lc-bottom-tab--active"
                    : "lc-bottom-tab"
                }
                data-testid="workspace-tab-evaluation"
                onClick={() => setBottomTab("evaluation")}
              >
                {t("workspace.tabEvaluation")}
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={bottomTab === "assistant"}
                className={
                  bottomTab === "assistant"
                    ? "lc-bottom-tab lc-bottom-tab--active"
                    : "lc-bottom-tab"
                }
                data-testid="workspace-tab-assistant"
                onClick={() => setBottomTab("assistant")}
              >
                {t("workspace.tabAssistant")}
              </button>
            </div>

            {bottomTab === "evaluation" ? (
              <div className="stack">
                {submit.isSuccess && currentSubmission === null && terminal === null ? (
                  <Banner tone="success" testId="workspace-submit-accepted">
                    {t("workspace.submitted")}
                    {submit.data.idempotent_replay
                      ? ` · ${t("submit.idempotentReplay")}`
                      : ""}
                  </Banner>
                ) : null}

                {pendingSubmission !== null &&
                pendingSubmission.versionId === selectedVersionId ? (
                  <Banner tone="info" testId="workspace-run-pending">
                    {t("workspace.preparingSubmission")}
                  </Banner>
                ) : null}

                {currentRunId !== null ? (
                  <div className="stack" data-testid="progress-section">
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
                  </div>
                ) : null}

                {progress.complete && (historyPick !== null || currentSubmission !== null) ? (
                  <div className="stack" data-testid="workspace-current-result">
                    <p>
                      {t("workspace.currentResult")}：
                      {resultState === "completed" ? (
                        typeof resultScore === "number" ? (
                          <span className="muted">
                            {t("grades.score")}：{resultScore} /{" "}
                            {resultMaxScore ?? "—"}
                          </span>
                        ) : (
                          <span className="muted">{t("grades.notScored")}</span>
                        )
                      ) : null}{" "}
                      <StatusPill
                        tone={questionStateTone(
                          resultQuestionState,
                        )}
                      >
                        {questionStateLabel(resultQuestionState)}
                      </StatusPill>
                    </p>
                    <div className="inline">
                      <span className="muted">
                        {t("grades.submittedAt")}：
                        {formatDateTime(resultSubmittedAt, t("common.none"))}
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

                {displayRunId !== null ? (
                  <CommentarySection runId={displayRunId} showAdvisory={false} />
                ) : null}

                {currentRunId === null &&
                currentSubmission === null &&
                !submit.isSuccess &&
                pendingSubmission === null ? (
                  <p className="muted" data-testid="workspace-no-evaluation">
                    {t("workspace.noEvaluationYet")}
                  </p>
                ) : null}
              </div>
            ) : (
              <div className="lc-assistant-card" data-testid="workspace-ai-assistant-panel">
                <div className="lc-assistant-ask">
                  <input
                    type="text"
                    className="input"
                    data-testid="workspace-ai-question-input"
                    placeholder={t("workspace.aiAssistantPlaceholder")}
                    value={assistantQuestion}
                    disabled={assistant.isPending}
                    onChange={(event) => setAssistantQuestion(event.target.value)}
                  />
                  <button
                    type="button"
                    className="button"
                    data-testid="workspace-ai-assistant-refresh"
                    disabled={
                      assistant.isPending ||
                      ownerId === null ||
                      version === null ||
                      selectedVersionId === null
                    }
                    onClick={askAiAssistant}
                  >
                    {assistant.isPending
                      ? t("workspace.aiAssistantGenerating")
                      : t("workspace.aiAssistantRefresh")}
                  </button>
                </div>

                {assistant.isPending ? <InlineLoader /> : null}
                {assistant.isError ? (
                  <ApiErrorBanner error={assistant.error} testId="workspace-ai-error" />
                ) : null}

                {currentReply ? (
                  <div className="stack" data-testid="workspace-ai-reply">
                    <div className="lc-assistant-section">
                      <h4>{t("workspace.aiAssistantDiagnosis")}</h4>
                      <p>{currentReply.stage_diagnosis}</p>
                    </div>

                    <div className="lc-assistant-section">
                      <h4>{t("workspace.aiAssistantNextSteps")}</h4>
                      <ol>
                        {currentReply.next_steps.map((step, idx) => (
                          <li key={idx}>{step}</li>
                        ))}
                      </ol>
                    </div>

                    {currentReply.key_concepts.length > 0 ? (
                      <div className="lc-assistant-section">
                        <h4>{t("workspace.aiAssistantConcepts")}</h4>
                        <div className="lc-assistant-chips">
                          {currentReply.key_concepts.map((concept, idx) => (
                            <span key={idx} className="lc-assistant-chip">
                              {concept}
                            </span>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {currentReply.check_points.length > 0 ? (
                      <div className="lc-assistant-section">
                        <h4>{t("workspace.aiAssistantCheckpoints")}</h4>
                        <ul>
                          {currentReply.check_points.map((cp, idx) => (
                            <li key={idx}>{cp}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}

                    {currentReply.disclaimers.length > 0 ? (
                      <p className="muted">
                        {currentReply.disclaimers.join(" · ")}
                      </p>
                    ) : null}
                  </div>
                ) : !assistant.isPending ? (
                  <p className="muted">{t("workspace.aiAssistantEmpty")}</p>
                ) : null}
              </div>
            )}
          </Panel>
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
                setHasUserEdited(true);
                setUploadedFileName(null);
                setSyncedSubmissionId(null);
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
          onClose={() => {
            setEmailOpen(false);
            refreshGradesAndFiles();
          }}
        >
          <EmailSubmissionPanel
            assignment={assignment}
            courseId={courseId}
            sectionId={mySectionId}
            sectionLabel={sectionLabel}
            currentUser={currentUser.data}
            questionRows={rows}
            onReceived={refreshGradesAndFiles}
          />
        </Modal>
      ) : null}
    </div>
  );
}
