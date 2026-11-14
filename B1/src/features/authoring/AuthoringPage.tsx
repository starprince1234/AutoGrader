import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { t } from "../../i18n";
import { courseLabel as formatCourseLabel, loadedCourses, useCourses } from "../../api/directory";
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
import { Toast } from "../../ui/Toast";
import { SafeMarkdown } from "../questions/SafeMarkdown";
import { useQuestion, useQuestionVersion } from "../questions/useQuestions";
import { AuthoringAssistancePanel, ComparisonDiagnosticsPanel } from "./AuthoringSignals";
import { AiAuthoringView } from "./AiAuthoringView";
import { ImageUploader } from "./ImageUploader";
import { StepNav } from "./StepNav";
import { STEPS, isStepComplete, type StepId } from "./steps";
import {
  LANGUAGE_OPTIONS,
  TITLE_MAX_LENGTH,
  applyProposal,
  applyProtectedContent,
  buildDraftInput,
  buildDraftSkeleton,
  deriveTags,
  draftFormFrom,
  emptyDraftForm,
  emptyReferenceTier,
  insertIntoStem,
  isDirty,
  parseTags,
  syncPrimaryReference,
  type DraftForm,
  type DraftIssue,
} from "./draft";
import {
  useCreateQuestion,
  useDraftContent,
  usePublishQuestionVersion,
  useUpdateQuestionDraft,
} from "./useAuthoring";
import { Select } from "../../ui/Select";

/**
 * 出题页（P08 新建 / P09 编辑，共用一个编辑器与同一条序列化路径）。
 *
 * 版面为**三步向导**：基本信息 → 题干 → 参考答案与 AI 评分细则。
 * 左侧是步骤导航，右侧一次只渲染一步；最后一步支持直接保存草稿或保存并直接发布，
 * 不再要求先编写测试用例和运行沙箱核验。
 */
/**
 * 把校验清单拼成**一条**提示：每条自带的句号去掉，用「；」连起来，最后补一个「。」
 * （否则会出现"请选择输出比较方式。；第 1 个用例的分值要大于 0。"这种两个标点连在一起的样子）。
 */
function issueMessage(issues: DraftIssue[]): string {
  const parts = issues.map((issue) => t(issue.key, issue.params).replace(/[。；;\s]+$/, ""));
  return `${parts.join("；")}。`;
}

export function AuthoringPage({
  questionId,
  generationId = null,
  onGenerationId = () => {},
  aiView = false,
  onAiViewChange = () => {},
  backTo = "questions",
}: {
  questionId: string | null;
  /** AI 候选 id（来自 URL）。刷新后用它取回**服务端原始候选**。 */
  generationId?: string | null;
  onGenerationId?: (next: string | null) => void;
  /** 「AI 出题」视图（来自 URL）。新建题目之外不提供该视图。 */
  aiView?: boolean;
  onAiViewChange?: (next: boolean) => void;
  /**
   * 进入本页的来源：`detail` = 从题目详情页点「编辑草稿 / 另存为新版本」进来的，
   * 右上角按钮显示「返回题目」并回到那道题；`questions` = 从题库列表进来的。
   */
  backTo?: "questions" | "detail";
}) {
  const courses = useCourses();
  const courseOptions = loadedCourses(courses.data?.pages);
  const question = useQuestion(questionId ?? "");
  // 最新版本决定两件事：就地改还是新建版本，以及适配器/上限的可读回值。
  const newestVersionId = question.data?.versions?.[0]?.question_version_id ?? null;
  const newestVersion = useQuestionVersion(newestVersionId);
  const createQuestion = useCreateQuestion();
  const updateQuestion = useUpdateQuestionDraft();
  const publishQuestionVersion = usePublishQuestionVersion();
  const navigate = useNavigate();

  // 课程刻意**不预选**：新建题目会写进某个课程，而下拉默认值会让"点错一下"
  // 变成"在另一门课里建了一道题"。编辑路径用题目自己的课程（服务端会再次校验）。
  const [courseId, setCourseId] = useState("");
  const [form, setForm] = useState<DraftForm>(() => emptyDraftForm());
  const [baseline, setBaseline] = useState<DraftForm>(() => emptyDraftForm());
  const [savedVersionId, setSavedVersionId] = useState<string | null>(null);
  const [leaveConfirm, setLeaveConfirm] = useState(false);
  const [step, setStep] = useState<StepId>("basic");
  // 一条红色提示（2.0：原本常驻在顶部白框里的提示改成屏幕中间一闪而过）。`key` 让同一句话
  // 再次触发时重新计时，而不是"看起来没反应"。
  const [toast, setToast] = useState<{ key: number; message: string } | null>(null);
  const showToast = useCallback((message: string) => {
    setToast({ key: Date.now(), message });
  }, []);

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

  /**
   * 编辑页把**草稿自己的**参考答案与用例读回来（教师、仅草稿）。
   *
   * 没有它，编辑页只能"重新声明"用例：验证没通过时老师想改原来那条也改不了，只能再加一条。
   * 服务端只对本课程可写的人、且版本仍是草稿时放行。
   */
  const newestVersionStatus = question.data?.versions?.[0]?.status ?? null;
  /**
   * 已发布版本的内容也要读：点「另存为新版本」进来的，编辑页必须在**原题完整内容**上开始
   * （参考代码 + 全部用例，含样例/隐藏、分值、输入输出与可见性）。只读草稿时，已发布题目
   * 打开后参考代码和用例都是空的，保存下来就是一个"空的新版本"——这正是本 bug。
   * 服务端 `draft-content` 现在对已发布版本同样只读放行（写路径仍然是新建版本，不动旧版本）。
   */
  const contentReadable =
    newestVersionStatus === "draft" || newestVersionStatus === "published";
  const draftContent = useDraftContent(
    questionId !== null && contentReadable ? newestVersionId : null,
  );
  const [protectedSeeded, setProtectedSeeded] = useState(false);

  useEffect(() => {
    if (protectedSeeded || !draftContent.data) return;
    const content = draftContent.data;
    setForm((previous) => {
      // 参考答案、用例与执行声明都来自作者通道；评分方式的完整配置也在其中。
      const next = applyProtectedContent(previous, content);
      setBaseline(next);
      return next;
    });
    setProtectedSeeded(true);
  }, [draftContent.data, protectedSeeded]);

  // 保存/创建后用来跟踪最新版本与编辑模式。
  const savedVersion = useQuestionVersion(savedVersionId);
  const targetQuestionId = questionId ?? createQuestion.data?.question_id ?? null;

  const asNewVersion = question.data?.versions?.[0]?.status === "published";
  const mutation = questionId === null ? createQuestion : updateQuestion;

  const parsed = parseTags(form.tags);
  const dirty = useMemo(() => isDirty(form, baseline), [form, baseline]);
  // 教师读"哪门课"，不是内部编号：能查到就显示代码与名称，查不到留白。
  const currentCourse = courseOptions.find((course) => course.course_id === courseId);
  const courseLabel = currentCourse ? `${currentCourse.course_code} · ${currentCourse.title}` : "—";

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

  // 「请先选择课程」：新建且还没选课进入页面时提示一次（原来常驻在顶部白框里）。
  useEffect(() => {
    if (questionId === null && courseId === "" && !aiView) {
      showToast(t("authoring.courseRequired"));
    }
    // 只在进入页面那一刻提示一次：之后由老师自己切换课程。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 「有还没保存的修改」：由"干净"变为"脏"时提示一次，不做常驻横幅。
  const wasDirty = useRef(false);
  useEffect(() => {
    if (dirty && !wasDirty.current) showToast(t("authoring.unsaved"));
    wasDirty.current = dirty;
  }, [dirty, showToast]);

  // 应用候选：先算出结果，再分别 set 两个 state。
  //
  // 刻意**不**在 `setForm` 的 updater 里调用 `setBaseline`：updater 必须是纯函数，
  // React 在 StrictMode 下会把它执行两次，而在 updater 里 setState 属于渲染期副作用。
  // `form` 来自本次渲染的闭包，所以拿它算一次就够（候选应用是用户显式动作，不存在
  // 同一帧内连续应用两次的情况）。
  const applyProposalToForm = useCallback(
    (proposal: QuestionDraftProposal) => {
      const next: DraftForm = {
        ...applyProposal(form, proposal),
        language: proposal.language ?? form.language,
      };
      setForm(next);
      // 候选是服务端给出的内容，不是教师的未保存编辑：应用后它就是新的基线。
      setBaseline(next);
    },
    [form],
  );

  const submit = async (alsoPublish = false) => {
    const derived = form.tags.trim() === "" ? deriveTags(form.language, form.adapterKind) : [];
    const effectiveForm =
      derived.length === 0 ? form : { ...form, tags: derived.join(", ") };
    if (effectiveForm !== form) setForm(effectiveForm);
    const build = buildDraftInput(effectiveForm, {
      newVersion: questionId !== null && asNewVersion,
      editing: questionId !== null,
    });
    if (!build.ok) {
      showToast(issueMessage(build.issues));
      return;
    }
    try {
      if (targetQuestionId === null) {
        if (courseId === "") {
          showToast(t("authoring.courseRequired"));
          return;
        }
        const created = await createQuestion.mutateAsync({
          courseId,
          input: build.input,
          generationId,
        });
        const createdVersionId = created.versions?.[0]?.question_version_id ?? null;
        if (alsoPublish && createdVersionId) {
          await publishQuestionVersion.mutateAsync({
            questionId: created.question_id,
            questionVersionId: createdVersionId,
          });
        }
        setBaseline(effectiveForm);
        void navigate({
          to: "/questions/$questionId",
          params: { questionId: created.question_id },
          search: { version: createdVersionId ?? undefined },
        });
        return;
      }
      const result = await updateQuestion.mutateAsync({
        questionId: targetQuestionId,
        input: build.input,
        asNewVersion,
      });
      const versionId = result.versions?.[0]?.question_version_id ?? null;
      if (alsoPublish && versionId) {
        await publishQuestionVersion.mutateAsync({
          questionId: result.question_id,
          questionVersionId: versionId,
        });
      }
      setSavedVersionId(versionId);
      setBaseline(effectiveForm);
      void navigate({
        to: "/questions/$questionId",
        params: { questionId: result.question_id },
        search: { version: versionId ?? undefined },
      });
    } catch {
      // 错误横幅由 mutation.error / publishQuestionVersion.error 渲染。
    }
  };

  /**
   * 插图时向本页要一个题目 id：还没有题目就先在后台存一份"够上传"的最小草稿。
   *
   * 教师不需要先手动保存（2.0：插图不再要求先存草稿）。这里刻意**不跑完整保存校验**：
   * 图床挂在题目上，只要有 id 就能上传；而"标准输出比较""用例分值"是最后一步的东西，
   * 拿它们拦住插图只会让老师看到一串与插图无关的报错。真正必需的是课程、标题、题干。
   */
  const ensureQuestionIdForUpload = async (): Promise<string | null> => {
    if (targetQuestionId !== null) return targetQuestionId;
    if (courseId === "") {
      showToast(t("authoring.courseRequired"));
      return null;
    }
    const build = buildDraftSkeleton(form);
    if (!build.ok) {
      showToast(issueMessage(build.issues));
      return null;
    }
    try {
      const created = await createQuestion.mutateAsync({
        courseId,
        input: build.input,
        generationId,
      });
      setBaseline(form);
      return created.question_id;
    } catch {
      // 失败的细节由下方 `mutation.isError` 的横幅呈现，这里只需要"没拿到 id"。
      return null;
    }
  };

  /** 右上角按钮指向哪里：从详情页进来的回那道题，其余回题库列表（3.0 第 1 条）。 */
  const leaveTo = () => {
    if (backTo === "detail" && targetQuestionId !== null) {
      void navigate({
        to: "/questions/$questionId",
        params: { questionId: targetQuestionId },
        search: {
          version: savedVersion.data?.question_version_id ?? newestVersionId ?? undefined,
        },
      });
      return;
    }
    void navigate({ to: "/questions" });
  };

  const requestLeave = () => {
    if (dirty) {
      setLeaveConfirm(true);
      return;
    }
    leaveTo();
  };

  /**
   * 保存草稿是否可点。契约 `QuestionDraftInput` 要求 `title` 与 `stem_markdown` 非空，
   * 所以缺这两项时按钮灰着；原因由红色提示在点选图/校验失败时说明，不再占一行常驻文案。
   */
  const saveDisabled =
    mutation.isPending ||
    publishQuestionVersion.isPending ||
    courseId === "" ||
    form.title.trim() === "" ||
    form.stemMarkdown.trim() === "";

  const completed = useMemo(
    () =>
      Object.fromEntries(
        STEPS.map((definition) => [
          definition.id,
          isStepComplete(definition.id, form, courseId, { editing: questionId !== null }),
        ]),
      ) as Record<StepId, boolean>,
    [form, courseId, questionId],
  );

  const publishDisabled = saveDisabled || !completed.reference;

  if (questionId !== null && question.isPending) return <Spinner />;
  if (questionId !== null && question.isError) return <ErrorState error={question.error} />;

  if (questionId === null && aiView) {
    return (
      <AiAuthoringView
        courseId={courseId === "" ? null : courseId}
        courses={courseOptions}
        onCourseChange={setCourseId}
        form={form}
        generationId={generationId}
        onGenerationId={onGenerationId}
        onApply={applyProposalToForm}
        onApplied={() => {
          // 教师点了「填进编辑器」：回到题干那一步，看到的是"内容已经填进去"，而不是空白页。
          setStep("stem");
          onAiViewChange(false);
        }}
        onBack={() => onAiViewChange(false)}
      />
    );
  }

  const saveButton = (
    <button
      type="button"
      className="button button--ghost"
      data-testid="authoring-save"
      disabled={saveDisabled}
      onClick={() => void submit(false)}
    >
      {mutation.isPending && !publishQuestionVersion.isPending
        ? t("authoring.saving")
        : targetQuestionId === null
          ? t("authoring.save.create")
          : asNewVersion
            ? t("authoring.save.newVersion")
            : t("authoring.save.update")}
    </button>
  );

  const saveAndPublishButton = (
    <button
      type="button"
      className="button"
      data-testid="authoring-save-publish"
      disabled={publishDisabled}
      onClick={() => void submit(true)}
    >
      {publishQuestionVersion.isPending ? t("questions.publishing") : t("authoring.saveAndPublish")}
    </button>
  );

  const saveRow = (
    <div className="inline wizard__save" data-testid="step-save">
      {saveButton}
      {saveAndPublishButton}
    </div>
  );

  const stepDefinition = STEPS.find((definition) => definition.id === step) ?? STEPS[0];
  // 前四步只有「下一步」：保存动作统一放在最后一步（与检验并发布同屏）。
  const nextIndex = STEPS.findIndex((definition) => definition.id === step) + 1;
  const nextStep = STEPS[nextIndex];
  const nextRow = nextStep ? (
    <div className="inline wizard__save" data-testid="step-next-row">
      <button
        type="button"
        className="button"
        data-testid="step-next"
        onClick={() => setStep(nextStep.id)}
      >
        {t("authoring.next")}
      </button>
    </div>
  ) : null;

  return (
    <div className="stack">
      <div className="inline page-head">
        <PageTitle>
          {questionId === null ? t("authoring.titleNew") : t("authoring.titleEdit")}
        </PageTitle>
        {/* AI 出题就在标题旁边：一进页面就能选，不必先走到某一步。 */}
        {questionId === null ? (
          <button
            type="button"
            className="button"
            data-testid="authoring-ai"
            onClick={() => onAiViewChange(true)}
          >
            {t("authoring.ai.open")}
          </button>
        ) : null}
        <button
          type="button"
          className="button button--ghost page-head__end"
          data-testid="authoring-back"
          onClick={requestLeave}
        >
          {backTo === "detail" ? t("authoring.backToDetail") : t("authoring.backToList")}
        </button>
      </div>

      {/* 提示与校验原因：屏幕中间一闪而过，不再占据顶部版面。 */}
      <Toast
        key={toast?.key ?? 0}
        message={toast?.message ?? null}
        onClose={() => setToast(null)}
      />

      {mutation.isError || publishQuestionVersion.isError ? (
        <ErrorState error={(mutation.error ?? publishQuestionVersion.error)!} />
      ) : null}

      <div className="wizard">
        <StepNav current={step} completed={completed} onSelect={setStep} />

        <div className="wizard__body">
          {step === "basic" ? (
            <Panel title={stepDefinition.label()} testId="step-basic">
              <div className="form-grid">
                {questionId === null ? (
                  <Field label={t("authoring.course")} htmlFor="authoring-course">
                    <Select
                      id="authoring-course"
                      value={courseId}
                      onChange={(event) => setCourseId(event.target.value)}
                    >
                      <option value="">{t("common.selectPrompt")}</option>
                      {courseOptions.map((course) => (
                        <option key={course.course_id} value={course.course_id}>
                          {formatCourseLabel(course)}
                        </option>
                      ))}
                    </Select>
                  </Field>
                ) : (
                  <Field label={t("authoring.course")} htmlFor="authoring-course-readonly">
                    <input id="authoring-course-readonly" value={courseLabel} readOnly />
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
                  <Select
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
                  </Select>
                </Field>
                <Field label={t("authoring.tags")} htmlFor="authoring-tags">
                  <input
                    id="authoring-tags"
                    value={form.tags}
                    onChange={(event) => setForm({ ...form, tags: event.target.value })}
                  />
                </Field>
              </div>
              <div className="inline" data-testid="authoring-tags-preview">
                {parsed.tags.length === 0 ? null : (
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
              {questionId === null && (courses.hasNextPage || courses.isFetchingNextPage) ? (
                // 只在还有下一页时给入口；到末页什么都不显示（不再出现「已到末页」那行字）。
                <LoadMoreActions
                  loadedCount={courseOptions.length}
                  hasNextPage={courses.hasNextPage}
                  isFetchingNextPage={courses.isFetchingNextPage}
                  onLoadMore={() => {
                    void courses.fetchNextPage();
                  }}
                  testId="authoring-courses"
                />
              ) : null}
              {courses.isError ? <ErrorState error={courses.error} /> : null}
              {newestVersion.isError ? <ErrorState error={newestVersion.error} /> : null}
              {questionId !== null ? (
                <Banner tone="info" testId="authoring-version-mode">
                  {asNewVersion
                    ? t("authoring.newVersionNotice")
                    : t("authoring.draftVersionNotice")}
                </Banner>
              ) : null}
              {nextRow}
            </Panel>
          ) : null}

          {step === "stem" ? (
            <Panel title={stepDefinition.label()} testId="step-stem">
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

              <div className="inline">
                <h3>{t("authoring.image.title")}</h3>
              </div>
              <ImageUploader
                questionId={targetQuestionId}
                // 已发布版本的内容冻结（服务端 409）；其余情况只要草稿存在就能上传。
                frozen={asNewVersion && targetQuestionId !== null}
                ensureQuestionId={ensureQuestionIdForUpload}
                onInsert={(snippet) =>
                  setForm({ ...form, stemMarkdown: insertIntoStem(form.stemMarkdown, snippet) })
                }
              />

              {/* 「下一步」放在这一屏的最下面：老师填完题干与图片再继续。 */}
              {nextRow}
            </Panel>
          ) : null}

          {step === "reference" ? (
            <Panel title={stepDefinition.label()} testId="step-reference">
              <div className="stack" data-testid="reference-tiers">
                <h3>{t("authoring.referenceTiersTitle")}</h3>
                {form.referenceAnswers.map((tier, index) => (
                  <div className="case-card stack" key={index} data-testid={`reference-tier-${index}`}>
                    <Field
                      label={`${t("authoring.referenceTierLabel")} (${index + 1})`}
                      htmlFor={`authoring-reference-label-${index}`}
                    >
                      <input
                        id={`authoring-reference-label-${index}`}
                        data-testid={`reference-tier-label-${index}`}
                        placeholder={t("authoring.referenceTierLabelPlaceholder")}
                        value={tier.label}
                        onChange={(event) => {
                          const nextTiers = form.referenceAnswers.map((item, itemIdx) =>
                            itemIdx === index ? { ...item, label: event.target.value } : item,
                          );
                          setForm({ ...form, referenceAnswers: nextTiers });
                        }}
                      />
                    </Field>
                    {index === 0 ? (
                      <Field label={t("authoring.referenceAnswer")} htmlFor="authoring-reference">
                        <textarea
                          id="authoring-reference"
                          rows={10}
                          value={tier.body}
                          onChange={(event) =>
                            setForm(syncPrimaryReference(form, event.target.value))
                          }
                        />
                      </Field>
                    ) : (
                      <Field
                        label={`${t("authoring.referenceTierBody")} (${index + 1})`}
                        htmlFor={`authoring-reference-${index}`}
                      >
                        <textarea
                          id={`authoring-reference-${index}`}
                          data-testid={`reference-tier-body-${index}`}
                          rows={8}
                          value={tier.body}
                          onChange={(event) => {
                            const nextTiers = form.referenceAnswers.map((item, itemIdx) =>
                              itemIdx === index ? { ...item, body: event.target.value } : item,
                            );
                            setForm({ ...form, referenceAnswers: nextTiers });
                          }}
                        />
                      </Field>
                    )}
                    {form.referenceAnswers.length > 1 ? (
                      <div className="inline">
                        <button
                          type="button"
                          className="button button--ghost"
                          data-testid={`reference-tier-remove-${index}`}
                          onClick={() => {
                            const remaining = form.referenceAnswers.filter(
                              (_item, itemIdx) => itemIdx !== index,
                            );
                            setForm({
                              ...form,
                              referenceAnswers: remaining,
                              referenceAnswer: remaining[0]?.body ?? "",
                            });
                          }}
                        >
                          {t("authoring.referenceTierRemove")}
                        </button>
                      </div>
                    ) : null}
                  </div>
                ))}
                <div className="inline">
                  <button
                    type="button"
                    className="button button--ghost"
                    data-testid="reference-tier-add"
                    onClick={() =>
                      setForm({
                        ...form,
                        referenceAnswers: [
                          ...form.referenceAnswers,
                          emptyReferenceTier(
                            `参考解法 ${form.referenceAnswers.length + 1}`,
                          ),
                        ],
                      })
                    }
                  >
                    {t("authoring.referenceTierAdd")}
                  </button>
                </div>
              </div>

              <Field
                label={t("authoring.gradingPrompt")}
                htmlFor="authoring-grading-prompt"
                hint={t("authoring.gradingPromptHint")}
              >
                <textarea
                  id="authoring-grading-prompt"
                  data-testid="authoring-grading-prompt"
                  rows={5}
                  placeholder={t("authoring.gradingPromptPlaceholder")}
                  value={form.gradingPrompt}
                  onChange={(event) => setForm({ ...form, gradingPrompt: event.target.value })}
                />
              </Field>

              <details className="panel" data-testid="runtime-config">
                <summary className="panel__title">{t("authoring.advancedOptions")}</summary>
                <div className="form-grid">
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
              </details>

              {asNewVersion ? (
                <div className="stack" data-testid="authoring-revision-note">
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
                </div>
              ) : null}

              {saveRow}
            </Panel>
          ) : null}


        </div>
      </div>

      {/* 信号：题目自查、AI 建议（P09 才有对象可检查）。 */}
      {targetQuestionId ? (
        <>
          <ComparisonDiagnosticsPanel questionId={targetQuestionId} />
          {questionId !== null ? <AuthoringAssistancePanel questionId={targetQuestionId} /> : null}
        </>
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
                leaveTo();
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
