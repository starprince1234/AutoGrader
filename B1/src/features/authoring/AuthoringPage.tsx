import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { t } from "../../i18n";
import { loadedCourses, useCourses } from "../../api/directory";
import type { QuestionDraftProposal } from "../../api/types";
import {
  Banner,
  ErrorState,
  Field,
  LoadMoreActions,
  Modal,
  PageTitle,
  Panel,
  Spinner,
} from "../../ui/components";
import { SafeMarkdown } from "../questions/SafeMarkdown";
import { useQuestion, useQuestionVersion } from "../questions/useQuestions";
import { AuthoringAssistancePanel, ComparisonDiagnosticsPanel } from "./AuthoringSignals";
import { CaseEditor } from "./CaseEditor";
import { FixtureEditor } from "./FixtureEditor";
import { GenerationPanel } from "./GenerationPanel";
import { ImageUploader } from "./ImageUploader";
import { ReferenceVerificationPanel } from "./ReferenceVerificationPanel";
import {
  LANGUAGE_OPTIONS,
  SELECTABLE_ADAPTER_KINDS,
  STDERR_COMPARISONS,
  STDOUT_COMPARISONS,
  TITLE_MAX_LENGTH,
  applyProposal,
  buildDraftInput,
  draftFormFrom,
  emptyDraftForm,
  insertIntoStem,
  isDirty,
  parseTags,
  type DraftForm,
  type DraftIssue,
} from "./draft";
import { useCreateQuestion, useUpdateQuestionDraft } from "./useAuthoring";

/**
 * 出题页（P08 新建 / P09 编辑，共用一个编辑器与同一条序列化路径）。
 *
 * 页面结构按"教师实际要做的事"排，而不是按 API 字段排：基本信息 → 题干（含图片与预览）→
 * 参考答案 → 测试用例 → 比较规则 → 适配器与夹具 → 版本说明 → 保存/验证发布。
 * 每个章节都是独立的 `Panel`，所以长表单可以分段阅读，而不是一路滚到底。
 *
 * 三条贯穿全页的规则：
 *
 * 1. **受保护内容不回显、也不假装知道**：`reference_answer`、`cases[]`、适配器细节与夹具清单
 *    都不在公开 DTO 里。本页只预填能读回的字段；留空 = 不声明（`CHG-0017`），编辑时既有用例集合
 *    由一次显式确认负责整体替换。
 * 2. **发布是一道真门禁**：入口在 `ReferenceVerificationPanel`——教师请求平台验证，只有"全通过 ∧
 *    存证已记录 ∧ 摘要未变"才出现发布动作。AI 候选同样要经过它。
 * 3. **未保存的编辑不会自己保住**：AI 候选与手工编辑都只存在当前页面。离开或刷新前若表单已改动，
 *    本页会明确提示"这些编辑不会保留"，而不是让教师以为刷新后还能看到自己改过的版本。
 */
export function AuthoringPage({
  questionId,
  generationId = null,
  onGenerationId = () => {},
}: {
  questionId: string | null;
  /** AI 候选 id（来自 URL）。刷新后用它取回**服务端原始候选**。 */
  generationId?: string | null;
  onGenerationId?: (next: string | null) => void;
}) {
  const courses = useCourses();
  const courseOptions = loadedCourses(courses.data?.pages);
  const question = useQuestion(questionId ?? "");
  // 最新版本决定两件事：就地改还是新建版本，以及适配器/上限的可读回值。
  const newestVersionId = question.data?.versions?.[0]?.question_version_id ?? null;
  const newestVersion = useQuestionVersion(newestVersionId);
  const createQuestion = useCreateQuestion();
  const updateQuestion = useUpdateQuestionDraft();
  const navigate = useNavigate();

  // 课程刻意**不预选**：新建题目会写进某个课程作用域，而下拉默认值会让"点错一下"
  // 变成"在另一门课里建了一道题"。编辑路径用题目自己的课程（服务端会再次校验）。
  const [courseId, setCourseId] = useState("");
  const [form, setForm] = useState<DraftForm>(() => emptyDraftForm());
  const [baseline, setBaseline] = useState<DraftForm>(() => emptyDraftForm());
  const [issues, setIssues] = useState<DraftIssue[]>([]);
  const [savedVersionId, setSavedVersionId] = useState<string | null>(null);
  const [leaveConfirm, setLeaveConfirm] = useState(false);

  // 预填只发生一次：数据到齐后填入公开 DTO 里**真的存在**的字段，之后不再覆盖用户的编辑。
  const [seeded, setSeeded] = useState(false);
  useEffect(() => {
    if (seeded || questionId === null || !question.data || !newestVersion.data) return;
    const seededForm = draftFormFrom(question.data, newestVersion.data);
    setForm(seededForm);
    setBaseline(seededForm);
    setCourseId(question.data.course_id);
    setSeeded(true);
  }, [newestVersion.data, question.data, questionId, seeded]);

  // 保存/创建后用来渲染发布状态与信号面板的版本（由**服务端返回的 id** 取回，不拼对象）。
  const createdVersionId = createQuestion.data?.versions?.[0]?.question_version_id ?? null;
  const createdVersion = useQuestionVersion(createdVersionId);
  const savedVersion = useQuestionVersion(savedVersionId);
  const targetQuestionId = questionId ?? createQuestion.data?.question_id ?? null;

  const asNewVersion = question.data?.versions?.[0]?.status === "published";
  const mutation = questionId === null ? createQuestion : updateQuestion;

  const parsed = parseTags(form.tags);
  const publishVersion = savedVersion.data ?? createdVersion.data ?? newestVersion.data ?? null;
  const dirty = useMemo(() => isDirty(form, baseline), [form, baseline]);

  // 浏览器级离开提示：只对"表单已改动"的页面生效，且只在真正可能丢失内容时出现。
  useEffect(() => {
    if (!dirty) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      // 现代浏览器忽略自定义文案，但设置 returnValue 是启用提示的条件。
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  // 应用候选：先算出结果，再分别 set 两个 state。
  //
  // 刻意**不**在 `setForm` 的 updater 里调用 `setBaseline`：updater 必须是纯函数，
  // React 在 StrictMode 下会把它执行两次，而在 updater 里 setState 属于渲染期副作用。
  // `form` 来自本次渲染的闭包，所以拿它算一次就够（候选应用是用户显式动作，不存在
  // 同一帧内连续应用两次的情况）。
  const applyProposalToForm = useCallback(
    (proposal: QuestionDraftProposal) => {
      const next = applyProposal(form, proposal);
      setForm(next);
      // 候选是服务端给出的内容，不是教师的未保存编辑：应用后它就是新的基线。
      setBaseline(next);
    },
    [form],
  );

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
      if (courseId === "") {
        setIssues([{ key: "authoring.courseRequired" }]);
        return;
      }
      createQuestion.mutate(
        { courseId, input: build.input, generationId },
        {
          onSuccess: () => {
            setBaseline(form);
          },
        },
      );
      return;
    }
    updateQuestion.mutate(
      { questionId, input: build.input, asNewVersion },
      {
        // PATCH 返回 `QuestionPublic`（CHG-0016）：新版本标识取自 `versions[0]`，再用它把
        // 版本 DTO 取回来渲染发布门禁（摘要只在版本 DTO 上）。
        onSuccess: (result) => {
          setSavedVersionId(result.versions?.[0]?.question_version_id ?? null);
          setBaseline(form);
        },
      },
    );
  };

  const requestLeave = () => {
    if (dirty) {
      setLeaveConfirm(true);
      return;
    }
    void navigate({ to: "/questions" });
  };

  // 新建路径保存成功后必须停手：这一页仍是 P08（questionId 为 null），再点一次「保存草稿」
  // 会**创建第二道题**。要继续编辑（尤其是上传夹具后补 manifest）就走「继续编辑」进 P09，
  // 那里是 PATCH，不会再产生新题目。
  const alreadyCreated = questionId === null && createQuestion.data !== undefined;
  const saveDisabled =
    mutation.isPending || (questionId === null && courseId === "") || alreadyCreated;

  if (questionId !== null && question.isPending) return <Spinner />;
  if (questionId !== null && question.isError) return <ErrorState error={question.error} />;

  return (
    <div className="stack">
      <PageTitle>
        {questionId === null ? t("authoring.titleNew") : t("authoring.titleEdit")}
      </PageTitle>

      {/* 顶部操作栏：标题、取消、保存。窄屏时按钮仍在正文之前，不需要滚到底才能保存。 */}
      <Panel
        title={t("authoring.actions")}
        actions={
          <div className="inline">
            <button
              type="button"
              className="button button--ghost"
              data-testid="authoring-cancel"
              onClick={requestLeave}
            >
              {t("common.cancel")}
            </button>
            <button
              type="button"
              className="button"
              data-testid="authoring-save"
              disabled={saveDisabled}
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
          </div>
        }
      >
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
        {questionId === null && courseId === "" ? (
          <Banner tone="warn" testId="authoring-course-required">
            {t("authoring.courseRequired")}
          </Banner>
        ) : null}
        {dirty ? (
          <p className="muted" data-testid="authoring-dirty">
            {t("authoring.unsaved")}
          </p>
        ) : null}
        {mutation.isError ? <ErrorState error={mutation.error} /> : null}
        {savedVersion.data ? (
          <Banner tone="success" testId="authoring-saved">
            {t("authoring.updated", {
              version: savedVersion.data.version,
              status: t(`questions.status.${savedVersion.data.status}`),
            })}
            {" · "}
            <Link
              className="button button--ghost"
              to="/questions/$questionId"
              params={{ questionId: targetQuestionId ?? "" }}
              search={{ version: savedVersion.data.question_version_id }}
            >
              {t("authoring.openDetail")}
            </Link>
            <Link
              className="button button--ghost"
              to="/questions/$questionId/edit"
              params={{ questionId: targetQuestionId ?? "" }}
            >
              {t("authoring.keepEditing")}
            </Link>
          </Banner>
        ) : null}
        {createQuestion.data ? (
          <Banner tone="success" testId="authoring-created">
            <p>{t("authoring.created")}</p>
            <p className="muted">{t("authoring.createdDraftHint")}</p>
            <p className="muted" data-testid="authoring-created-next">
              {t("authoring.createdNext")}
            </p>
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

      {/* 1. 基本信息 */}
      <Panel title={t("authoring.basic")}>
        <div className="form-grid">
          {questionId === null ? (
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
          ) : (
            <Field label={t("authoring.course")} htmlFor="authoring-course-readonly">
              <input
                id="authoring-course-readonly"
                value={question.data?.course_id ?? ""}
                readOnly
                className="mono-id"
              />
            </Field>
          )}
          <Field label={t("authoring.title")} htmlFor="authoring-title">
            <input
              id="authoring-title"
              value={form.title}
              maxLength={TITLE_MAX_LENGTH}
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
        {questionId === null ? (
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
        ) : null}
        {courses.isError ? <ErrorState error={courses.error} /> : null}
        {newestVersion.isError ? <ErrorState error={newestVersion.error} /> : null}
        {questionId !== null ? (
          <Banner tone="info" testId="authoring-version-mode">
            {asNewVersion ? t("authoring.newVersionNotice") : t("authoring.draftVersionNotice")}
          </Banner>
        ) : null}
      </Panel>

      {/* 2. 题干：编辑与安全预览并排（窄屏由 .grid-2 自动堆叠）。 */}
      <Panel title={t("authoring.stem")}>
        <div className="grid-2">
          <div>
            <Field label={t("authoring.stem")} htmlFor="authoring-stem">
              <textarea
                id="authoring-stem"
                rows={14}
                value={form.stemMarkdown}
                onChange={(event) => setForm({ ...form, stemMarkdown: event.target.value })}
              />
            </Field>
            <p className="muted">{t("authoring.stemPreviewHint")}</p>
          </div>
          <div data-testid="authoring-stem-preview">
            <h3>{t("authoring.stemPreview")}</h3>
            {form.stemMarkdown.trim() === "" ? (
              <p className="muted">{t("authoring.stemEmpty")}</p>
            ) : (
              <SafeMarkdown source={form.stemMarkdown} testId="authoring-preview-stem" />
            )}
          </div>
        </div>

        <h3>{t("authoring.image.title")}</h3>
        <ImageUploader
          questionId={targetQuestionId}
          // 新建并已保存之后不再允许上传：本页的「插入题干」不会写库，而保存按钮已停用，
          // 插入的地址会随着离开而丢失。要插图请进 P09（那里是 PATCH，插入能保存）。
          disabled={targetQuestionId === null || asNewVersion || alreadyCreated}
          disabledReason={
            targetQuestionId === null
              ? t("authoring.image.needDraft")
              : asNewVersion
                ? t("authoring.image.publishedFrozen")
                : t("authoring.image.createdHint")
          }
          onInsert={(snippet) => setForm({ ...form, stemMarkdown: insertIntoStem(form.stemMarkdown, snippet) })}
        />
      </Panel>

      {/* 3. 参考答案（受保护，学生看不到） */}
      <Panel title={t("authoring.reference")} testId="authoring-protected">
        <Banner tone="warn" testId="authoring-reference-notice">
          {t("authoring.referenceNotice")}
        </Banner>
        <Field label={t("authoring.referenceAnswer")} htmlFor="authoring-reference">
          <textarea
            id="authoring-reference"
            rows={8}
            value={form.referenceAnswer}
            onChange={(event) => setForm({ ...form, referenceAnswer: event.target.value })}
          />
        </Field>
        <p className="muted" data-testid="authoring-omission-rule">
          {t("authoring.protectedRule")}
        </p>
        <p className="muted" data-testid="authoring-protected-unknown">
          {t("authoring.protectedUnknown")}
        </p>
      </Panel>

      {/* 4. 测试用例 */}
      <Panel title={t("authoring.casesSection")}>
        <p className="muted" data-testid="authoring-cases-hint">
          {t("authoring.casesHint")}
        </p>
        <CaseEditor
          cases={form.cases}
          onChange={(cases) => {
            setForm({ ...form, cases });
          }}
        />
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

      {/* 5. 比较规则 */}
      <Panel title={t("authoring.comparison")}>
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

      {/* 6. 适配器与夹具：字段随 adapter 展开，不同时显示所有高级项。 */}
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

        {form.adapterKind === "stdin" ? (
          <div className="form-grid">
            <Field label={t("authoring.adapterArgs")} htmlFor="authoring-adapter-args">
              <input
                id="authoring-adapter-args"
                value={form.adapterArgs}
                onChange={(event) => setForm({ ...form, adapterArgs: event.target.value })}
              />
            </Field>
            <Field label={t("authoring.adapterExpectExitCode")} htmlFor="authoring-adapter-exit">
              <input
                id="authoring-adapter-exit"
                inputMode="numeric"
                value={form.adapterExpectExitCode}
                onChange={(event) =>
                  setForm({ ...form, adapterExpectExitCode: event.target.value })
                }
              />
            </Field>
            <Field label={t("authoring.adapterStdin")} htmlFor="authoring-adapter-stdin">
              <textarea
                id="authoring-adapter-stdin"
                rows={3}
                value={form.adapterStdin}
                onChange={(event) => setForm({ ...form, adapterStdin: event.target.value })}
              />
            </Field>
          </div>
        ) : null}

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

        {form.adapterKind === "file" ? (
          <>
            <h3>{t("authoring.fixture.title")}</h3>
            <p className="muted" data-testid="fixture-explanation">
              {targetQuestionId === null
                ? t("authoring.fixture.saveFirst")
                : t("authoring.fixture.explanation")}
            </p>
            <FixtureEditor
              versionId={newestVersionId ?? savedVersionId ?? createdVersionId}
              questionId={targetQuestionId}
              fixtures={form.fixtures}
              onChange={(fixtures) => setForm({ ...form, fixtures })}
              // 已保存过的新建页同样停用：夹具声明是 manifest 的一部分，而本页保存已停用，
              // 上传后无法写进题目——要保住这次声明请进 P09 上传并保存。
              disabled={
                (newestVersionId ?? savedVersionId ?? createdVersionId) === null ||
                asNewVersion ||
                alreadyCreated
              }
              disabledReason={
                asNewVersion
                  ? t("authoring.fixture.publishedFrozen")
                  : alreadyCreated
                    ? t("authoring.fixture.createdHint")
                    : t("authoring.fixture.needVersion")
              }
              onlyInputMounts
            />
          </>
        ) : null}

        <p className="muted" data-testid="adapter-not-readable">
          {t("authoring.adapterNotReadable")}
        </p>
      </Panel>

      {/* 7. 版本说明：只在"已发布版本另存新版本"时出现且必填。 */}
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
          {form.revisionNote.trim() === "" ? (
            <Banner tone="warn" testId="authoring-revision-note-required">
              {t("authoring.revisionNoteRequired")}
            </Banner>
          ) : null}
        </Panel>
      ) : null}

      {/* 8. AI 草稿：只在新建题目时提供"按教学目标生成题目"。 */}
      {questionId === null ? (
        <GenerationPanel
          courseId={courseId === "" ? null : courseId}
          form={form}
          generationId={generationId}
          onGenerationId={onGenerationId}
          onApply={applyProposalToForm}
        />
      ) : null}

      {/* 9. 信号：确定性诊断、模型建议（P09 才有对象可检查）。 */}
      {targetQuestionId ? (
        <>
          <ComparisonDiagnosticsPanel questionId={targetQuestionId} />
          {questionId !== null ? <AuthoringAssistancePanel questionId={targetQuestionId} /> : null}
        </>
      ) : null}

      {/* 10. 验证并发布：只有已保存的草稿才谈得上验证。 */}
      {publishVersion ? (
        <ReferenceVerificationPanel version={publishVersion} questionId={targetQuestionId} />
      ) : null}

      {leaveConfirm ? (
        <Modal title={t("authoring.leaveTitle")} onClose={() => setLeaveConfirm(false)}>
          <p>{t("authoring.leaveConfirm")}</p>
          <div className="inline">
            <button
              type="button"
              className="button button--danger"
              data-testid="authoring-leave-confirm"
              onClick={() => {
                setLeaveConfirm(false);
                void navigate({ to: "/questions" });
              }}
            >
              {t("authoring.leaveAction")}
            </button>
            <button
              type="button"
              className="button button--ghost"
              onClick={() => setLeaveConfirm(false)}
            >
              {t("authoring.leaveStay")}
            </button>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
