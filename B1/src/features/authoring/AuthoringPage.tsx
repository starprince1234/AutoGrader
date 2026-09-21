import { useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";
import { t } from "../../i18n";
import { loadedCourses, useCourses } from "../../api/directory";
import { shortenId } from "../../ui/format";
import {
  Banner,
  ErrorState,
  Field,
  LoadMoreActions,
  PageTitle,
  Panel,
  Spinner,
} from "../../ui/components";
import { SafeMarkdown } from "../questions/SafeMarkdown";
import { useQuestion, useQuestionVersion } from "../questions/useQuestions";
import { AuthoringAssistancePanel, ComparisonDiagnosticsPanel } from "./AuthoringSignals";
import { CaseEditor } from "./CaseEditor";
import { ReferenceVerificationPanel } from "./ReferenceVerificationPanel";
import {
  LANGUAGE_OPTIONS,
  SELECTABLE_ADAPTER_KINDS,
  STDERR_COMPARISONS,
  STDOUT_COMPARISONS,
  buildDraftInput,
  draftFormFrom,
  emptyDraftForm,
  parseTags,
  type DraftForm,
  type DraftIssue,
} from "./draft";
import { useCreateQuestion, useUpdateQuestionDraft } from "./useAuthoring";

/**
 * 出题页（RQ-T-10 的"出题流程"入口；契约 `createQuestion` / `updateQuestionDraft`）。
 *
 * 本模块此前**完全没有题目写能力**：平台能建题能发布，界面一条路都没有。这个页面补上写路径，
 * 并把三件不能靠 UI 解决的事说清楚，而不是用按钮遮住：
 *
 * 1. **受保护内容不回显**：`reference_answer`、`cases[]` 与适配器细节（`args`/`stdin`/
 *    `expect_exit_code`/`comparison`/`callable`）都不在任何公开 DTO 里。本页只预填能读回的字段
 *    （题干/标题/语言/标签/`adapter_kind`/`max_files`/`max_total_bytes`），其余必须重新声明；
 *    留空即不提交（问题清单见 `draft.ts`）。
 * 2. **发布是一道真门禁，入口在 `ReferenceVerificationPanel`**：`publish` 要求一份与
 *    `content_digest` 绑定的参考解答验证存证（CHG-0008）。`ADR-0015` 之后这条路是通的：
 *    教师用普通令牌请求平台验证（B2 执行、B3 记录），面板按"通过 / 未通过 / 需要 Linux 沙箱"
 *    三态如实呈现，只有"全通过 ∧ 存证已记录 ∧ 摘要未变"才给出发布动作。
 * 3. **`kind=file` 不可保存**：夹具字节必须经 internal 的 fixtures 端点上传，摘要由服务端计算。
 */
export function AuthoringPage({ questionId }: { questionId: string | null }) {
  const courses = useCourses();
  const courseOptions = loadedCourses(courses.data?.pages);
  const question = useQuestion(questionId ?? "");
  // 最新版本决定两件事：就地改还是新建版本，以及适配器/上限的可读回值。
  const newestVersionId = question.data?.versions?.[0]?.question_version_id ?? null;
  const newestVersion = useQuestionVersion(newestVersionId);
  const createQuestion = useCreateQuestion();
  const updateQuestion = useUpdateQuestionDraft();

  // 课程刻意**不预选**：新建题目会写进某个课程作用域，而下拉默认值会让"点错一下"
  // 变成"在另一门课里建了一道题"。下拉框就在这一屏，选一次比事后无从删除便宜。
  const [courseId, setCourseId] = useState("");
  // 新建路径以 `emptyDraftForm()` 开场（含一行空用例作为邀请）；编辑路径的预填在下面那个
  // effect 里用 `draftFormFrom()` 覆盖它，而后者**不**凭空渲染用例行（读不到就不假装知道）。
  const [form, setForm] = useState<DraftForm>(() => emptyDraftForm());
  const [issues, setIssues] = useState<DraftIssue[]>([]);
  const [savedVersionId, setSavedVersionId] = useState<string | null>(null);

  // 预填只发生一次：数据到齐后填入公开 DTO 里**真的存在**的字段，之后不再覆盖用户的编辑。
  const [seeded, setSeeded] = useState(false);
  useEffect(() => {
    if (seeded || questionId === null || !question.data || !newestVersion.data) return;
    setForm(draftFormFrom(question.data, newestVersion.data));
    setSeeded(true);
  }, [newestVersion.data, question.data, questionId, seeded]);

  // 保存/创建后用来渲染发布状态与信号面板的版本（由**服务端返回的 id** 取回，不拼对象）。
  const createdVersionId = createQuestion.data?.versions?.[0]?.question_version_id ?? null;
  const createdVersion = useQuestionVersion(createdVersionId);
  const savedVersion = useQuestionVersion(savedVersionId);
  const targetQuestionId = questionId ?? createQuestion.data?.question_id ?? null;

  const asNewVersion = question.data?.versions?.[0]?.status === "published";
  const mutation = questionId === null ? createQuestion : updateQuestion;

  const submit = () => {
    const build = buildDraftInput(form, {
      newVersion: questionId !== null && asNewVersion,
      editing: questionId !== null,
    });
    if (!build.ok) {
      setIssues(build.issues);
      return;
    }
    setIssues([]);
    if (questionId === null) {
      createQuestion.mutate({ courseId, input: build.input });
      return;
    }
    updateQuestion.mutate(
      { questionId, input: build.input, asNewVersion },
      {
        // PATCH 返回 `QuestionPublic`（CHG-0016）：新版本标识取自 `versions[0]`，再用它把
        // 版本 DTO 取回来渲染发布门禁（摘要只在版本 DTO 上）。
        onSuccess: (question) =>
          setSavedVersionId(question.versions?.[0]?.question_version_id ?? null),
      },
    );
  };

  if (questionId !== null && question.isPending) return <Spinner />;
  if (questionId !== null && question.isError) return <ErrorState error={question.error} />;

  const parsed = parseTags(form.tags);
  const publishVersion = savedVersion.data ?? createdVersion.data ?? newestVersion.data ?? null;

  return (
    <div className="stack">
      <PageTitle>
        {questionId === null ? t("authoring.titleNew") : t("authoring.titleEdit")}
      </PageTitle>

      <Panel title={t("authoring.target")}>
        <p className="muted">{t("authoring.intro")}</p>
        {questionId === null ? (
          <>
            <div className="form-grid">
              <Field label={t("authoring.course")} htmlFor="authoring-course">
                <select
                  id="authoring-course"
                  value={courseId}
                  onChange={(event) => setCourseId(event.target.value)}
                >
                  <option value="">{t("common.selectPrompt")}</option>
                  {courseOptions.map((course) => (
                    <option key={course.course_id} value={course.course_id}>
                      {course.course_code} · {course.title}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <div className="inline">
              <span className="muted">
                {t("common.coursesLoaded", { count: courseOptions.length })}
              </span>
              <LoadMoreActions
                loadedCount={courseOptions.length}
                hasNextPage={courses.hasNextPage}
                isFetchingNextPage={courses.isFetchingNextPage}
                onLoadMore={() => {
                  void courses.fetchNextPage();
                }}
                testId="authoring-courses"
              />
            </div>
            <p className="muted">{t("authoring.courseHint")}</p>
            {courseId === "" ? (
              <Banner tone="warn" testId="authoring-course-required">
                {t("authoring.courseRequired")}
              </Banner>
            ) : null}
          </>
        ) : (
          <>
            <div className="inline">
              <strong>{question.data?.title}</strong>
              <span className="muted" data-testid="authoring-target-status">
                {t("authoring.targetStatus", {
                  status: t(`questions.status.${question.data?.status ?? "draft"}`),
                  version: question.data?.versions?.[0]?.version ?? 0,
                  versionStatus: t(
                    `questions.status.${question.data?.versions?.[0]?.status ?? "draft"}`,
                  ),
                })}
              </span>
              <Link
                className="button button--ghost"
                to="/questions/$questionId"
                params={{ questionId: questionId }}
                search={{ version: undefined }}
              >
                {t("authoring.openDetail")}
              </Link>
            </div>
            <Banner tone="info" testId="authoring-version-mode">
              {asNewVersion ? t("authoring.newVersionNotice") : t("authoring.draftVersionNotice")}
            </Banner>
          </>
        )}
        {courses.isError ? <ErrorState error={courses.error} /> : null}
        {newestVersion.isError ? <ErrorState error={newestVersion.error} /> : null}
      </Panel>

      <Panel title={t("authoring.basic")}>
        <div className="form-grid">
          <Field label={t("authoring.title")} htmlFor="authoring-title">
            <input
              id="authoring-title"
              value={form.title}
              onChange={(event) => setForm({ ...form, title: event.target.value })}
            />
          </Field>
          <Field label={t("authoring.language")} htmlFor="authoring-language">
            <select
              id="authoring-language"
              value={form.language}
              onChange={(event) =>
                setForm({ ...form, language: event.target.value as DraftForm["language"] })
              }
            >
              {LANGUAGE_OPTIONS.map((language) => (
                <option key={language} value={language}>
                  {t(`questions.language.${language}`)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("authoring.tags")} htmlFor="authoring-tags">
            <input
              id="authoring-tags"
              value={form.tags}
              onChange={(event) => setForm({ ...form, tags: event.target.value })}
            />
          </Field>
        </div>
        <p className="muted">{t("authoring.tagsHint")}</p>
        <div className="inline" data-testid="authoring-tags-preview">
          <span className="muted">{t("authoring.tagsParsed")}</span>
          {parsed.tags.length === 0 ? (
            <span className="muted">{t("common.none")}</span>
          ) : (
            parsed.tags.map((tag) => (
              <span className="pill pill--idle" key={tag}>
                {tag}
              </span>
            ))
          )}
        </div>
        {parsed.invalid.length > 0 ? (
          <Banner tone="warn" testId="authoring-tags-invalid">
            {t("authoring.tagsInvalid", { tags: parsed.invalid.join(", ") })}
          </Banner>
        ) : null}
      </Panel>

      <Panel title={t("authoring.stem")}>
        <Field label={t("authoring.stem")} htmlFor="authoring-stem">
          <textarea
            id="authoring-stem"
            rows={10}
            value={form.stemMarkdown}
            onChange={(event) => setForm({ ...form, stemMarkdown: event.target.value })}
          />
        </Field>
        <p className="muted">{t("authoring.stemPreviewHint")}</p>
        <div data-testid="authoring-stem-preview">
          <h3>{t("authoring.stemPreview")}</h3>
          {form.stemMarkdown.trim() === "" ? (
            <p className="muted">{t("common.empty")}</p>
          ) : (
            <SafeMarkdown source={form.stemMarkdown} testId="authoring-preview-stem" />
          )}
        </div>
      </Panel>

      <Panel title={t("authoring.protected")} testId="authoring-protected">
        <Banner tone="warn" testId="authoring-protected-notice">
          {t("authoring.protectedNotice")}
        </Banner>
        {/* CHG-0017 的核心：留空 = 不声明该字段，服务端保留已存的值。 */}
        <p className="muted" data-testid="authoring-omission-rule">
          {t("authoring.protectedRule")}
        </p>
        <p className="muted" data-testid="authoring-protected-unknown">
          {t("authoring.protectedUnknown")}
        </p>
        {questionId === null ? (
          <p className="muted" data-testid="authoring-new-question-note">
            {t("authoring.protectedNewQuestion")}
          </p>
        ) : null}

        <Field label={t("authoring.referenceAnswer")} htmlFor="authoring-reference">
          <textarea
            id="authoring-reference"
            rows={6}
            value={form.referenceAnswer}
            onChange={(event) => {
              setForm({ ...form, referenceAnswer: event.target.value });
            }}
          />
        </Field>
        <p className="muted">{t("authoring.referenceAnswerHint")}</p>

        <h3>{t("authoring.cases", { count: form.cases.length })}</h3>
        <CaseEditor
          cases={form.cases}
          onChange={(cases) => {
            setForm({ ...form, cases });
          }}
        />
        <p className="muted" data-testid="authoring-cases-hint">
          {t("authoring.casesHint")}
        </p>
        {/* 编辑时提交用例 = 整体替换一份本页读不到的集合。必须由人显式确认一次。 */}
        {questionId !== null && form.cases.length > 0 ? (
          <div className="field" data-testid="authoring-cases-replace">
            <label className="inline" htmlFor="authoring-confirm-cases">
              <input
                id="authoring-confirm-cases"
                type="checkbox"
                checked={form.confirmCaseReplacement}
                onChange={(event) =>
                  setForm({ ...form, confirmCaseReplacement: event.target.checked })
                }
              />
              {t("authoring.casesReplace")}
            </label>
            <span className="muted">{t("authoring.casesReplaceHint")}</span>
          </div>
        ) : null}
      </Panel>

      <Panel title={t("authoring.adapter")}>
        <div className="form-grid">
          <Field label={t("authoring.adapterKind")} htmlFor="authoring-adapter-kind">
            <select
              id="authoring-adapter-kind"
              value={form.adapterKind}
              onChange={(event) =>
                setForm({ ...form, adapterKind: event.target.value as DraftForm["adapterKind"] })
              }
            >
              {SELECTABLE_ADAPTER_KINDS.map((kind) => (
                <option key={kind} value={kind}>
                  {t(`questions.adapter.${kind}`)}
                </option>
              ))}
              {/* 已存在的 file 版本必须如实显示（`adapter_kind` 是可读字段），
                  但它不能被本页保存：夹具摘要只能由服务端计算。 */}
              <option value="file">{t("questions.adapter.file")}</option>
            </select>
          </Field>
          <Field label={t("authoring.maxFiles")} htmlFor="authoring-max-files">
            <input
              id="authoring-max-files"
              inputMode="numeric"
              value={form.maxFiles}
              onChange={(event) => setForm({ ...form, maxFiles: event.target.value })}
            />
          </Field>
          <Field label={t("authoring.maxTotalBytes")} htmlFor="authoring-max-total-bytes">
            <input
              id="authoring-max-total-bytes"
              inputMode="numeric"
              value={form.maxTotalBytes}
              onChange={(event) => setForm({ ...form, maxTotalBytes: event.target.value })}
            />
          </Field>
        </div>
        {form.adapterKind === "file" ? (
          <Banner tone="warn" testId="adapter-file-locked">
            <strong>{t("authoring.adapterFileUnavailable")}</strong>
            <p className="muted">{t("authoring.adapterFileWhy")}</p>
          </Banner>
        ) : null}

        {/* `adapter` 是**必需**字段，但它读不回来（只读得到 adapter_kind 与两个上限）。
            因此本页保存时是**重新声明**整个 manifest，而不是继承已存的那一份——必须说出来。 */}
        <Banner tone="info" testId="adapter-not-readable">
          <p>{t("authoring.adapterNotReadable")}</p>
          <p className="muted">{t("authoring.adapterNotReadableWhy")}</p>
        </Banner>

        <Field label={t("authoring.adapterArgs")} htmlFor="authoring-adapter-args">
          <input
            id="authoring-adapter-args"
            value={form.adapterArgs}
            onChange={(event) => setForm({ ...form, adapterArgs: event.target.value })}
          />
        </Field>
        <div className="grid-2">
          <Field label={t("authoring.adapterStdin")} htmlFor="authoring-adapter-stdin">
            <textarea
              id="authoring-adapter-stdin"
              rows={3}
              value={form.adapterStdin}
              onChange={(event) => setForm({ ...form, adapterStdin: event.target.value })}
            />
          </Field>
          <Field label={t("authoring.adapterExpectExitCode")} htmlFor="authoring-adapter-exit">
            <input
              id="authoring-adapter-exit"
              inputMode="numeric"
              value={form.adapterExpectExitCode}
              onChange={(event) => setForm({ ...form, adapterExpectExitCode: event.target.value })}
            />
          </Field>
        </div>

        {form.adapterKind === "callable" ? (
          <div className="form-grid">
            <Field label={t("authoring.callableModule")} htmlFor="authoring-callable-module">
              <input
                id="authoring-callable-module"
                value={form.callableModule}
                onChange={(event) => setForm({ ...form, callableModule: event.target.value })}
              />
            </Field>
            <Field label={t("authoring.callableAttribute")} htmlFor="authoring-callable-attribute">
              <input
                id="authoring-callable-attribute"
                value={form.callableAttribute}
                onChange={(event) => setForm({ ...form, callableAttribute: event.target.value })}
              />
            </Field>
            <Field
              label={t("authoring.callableArgumentsJson")}
              htmlFor="authoring-callable-arguments"
            >
              <input
                id="authoring-callable-arguments"
                value={form.callableArgumentsJson}
                onChange={(event) =>
                  setForm({ ...form, callableArgumentsJson: event.target.value })
                }
              />
            </Field>
          </div>
        ) : null}

        <h3>{t("authoring.comparison")}</h3>
        <div className="form-grid">
          <Field label={t("authoring.comparisonStdout")} htmlFor="authoring-comparison-stdout">
            <select
              id="authoring-comparison-stdout"
              value={form.comparisonStdout}
              onChange={(event) =>
                setForm({
                  ...form,
                  comparisonStdout: event.target.value as DraftForm["comparisonStdout"],
                })
              }
            >
              <option value="">{t("common.selectPrompt")}</option>
              {STDOUT_COMPARISONS.map((policy) => (
                <option key={policy} value={policy}>
                  {t(`authoring.comparison.${policy}`)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("authoring.comparisonStderr")} htmlFor="authoring-comparison-stderr">
            <select
              id="authoring-comparison-stderr"
              value={form.comparisonStderr}
              onChange={(event) =>
                setForm({
                  ...form,
                  comparisonStderr: event.target.value as DraftForm["comparisonStderr"],
                })
              }
            >
              <option value="">{t("authoring.comparisonStderr.unset")}</option>
              {STDERR_COMPARISONS.map((policy) => (
                <option key={policy} value={policy}>
                  {t(`authoring.comparison.${policy}`)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("authoring.numericTolerance")} htmlFor="authoring-tolerance">
            <input
              id="authoring-tolerance"
              inputMode="decimal"
              value={form.numericTolerance}
              onChange={(event) => setForm({ ...form, numericTolerance: event.target.value })}
            />
          </Field>
        </div>
        <p className="muted">{t("authoring.numericToleranceHint")}</p>
      </Panel>

      {asNewVersion ? (
        <Panel title={t("authoring.revisionNote")}>
          <p className="muted">{t("authoring.revisionNoteHint")}</p>
          <Field label={t("authoring.revisionNote")} htmlFor="authoring-revision-note">
            <textarea
              id="authoring-revision-note"
              rows={2}
              value={form.revisionNote}
              onChange={(event) => setForm({ ...form, revisionNote: event.target.value })}
            />
          </Field>
        </Panel>
      ) : null}

      <Panel title={t("common.save")}>
        {issues.length > 0 ? (
          <Banner tone="error" testId="authoring-issues">
            <strong>{t("authoring.issues")}</strong>
            <ul>
              {issues.map((issue, index) => (
                <li key={`${issue.key}-${index}`}>{t(issue.key, issue.params)}</li>
              ))}
            </ul>
          </Banner>
        ) : null}
        <div className="inline">
          <button
            type="button"
            className="button"
            data-testid="authoring-save"
            disabled={mutation.isPending || (questionId === null && courseId === "")}
            onClick={submit}
          >
            {mutation.isPending
              ? t("authoring.saving")
              : questionId === null
                ? t("authoring.save.create")
                : asNewVersion
                  ? t("authoring.save.newVersion")
                  : t("authoring.save.update")}
          </button>
          {questionId === null && courseId === "" ? (
            <span className="muted">{t("authoring.courseRequired")}</span>
          ) : null}
        </div>
        {mutation.isError ? <ErrorState error={mutation.error} /> : null}
        {savedVersion.data ? (
          <Banner tone="success" testId="authoring-saved">
            {t("authoring.updated", {
              id: savedVersion.data.question_version_id,
              version: savedVersion.data.version,
              status: t(`questions.status.${savedVersion.data.status}`),
            })}
            {" · "}
            {t("authoring.contentDigest", {
              digest: savedVersion.data.content_digest ?? t("common.none"),
            })}
          </Banner>
        ) : null}
        {createQuestion.data ? (
          <Banner tone="success" testId="authoring-created">
            <p>{t("authoring.created", { id: createQuestion.data.question_id })}</p>
            <p className="muted">{t("authoring.createdDraftHint")}</p>
            <div className="inline">
              <Link
                className="button button--ghost"
                to="/questions/$questionId"
                params={{ questionId: createQuestion.data.question_id }}
                search={{ version: undefined }}
              >
                {t("authoring.openDetail")}
              </Link>
              <Link
                className="button button--ghost"
                to="/questions/$questionId/edit"
                params={{ questionId: createQuestion.data.question_id }}
              >
                {t("questions.editDraft")}
              </Link>
            </div>
          </Banner>
        ) : null}
      </Panel>

      {targetQuestionId ? (
        <>
          <ComparisonDiagnosticsPanel questionId={targetQuestionId} />
          <AuthoringAssistancePanel questionId={targetQuestionId} />
        </>
      ) : null}

      {publishVersion ? (
        <ReferenceVerificationPanel
          version={publishVersion}
          questionId={targetQuestionId}
        />
      ) : null}

      {createQuestion.data ? (
        <Panel title={t("authoring.createdId")}>
          <p className="muted mono-id" title={createQuestion.data.question_id}>
            question_id: {shortenId(createQuestion.data.question_id)}
          </p>
        </Panel>
      ) : null}
    </div>
  );
}
