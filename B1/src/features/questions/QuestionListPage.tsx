import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { t } from "../../i18n";
import { courseLabel as formatCourseLabel, loadedCourses, useCourses } from "../../api/directory";
import { isApiError } from "../../api/problem";
import { useSession } from "../../auth/sessionContext";
import { canAuthorQuestions } from "../../auth/visibility";
import {
  Banner,
  EmptyState,
  ErrorState,
  Field,
  LoadMoreActions,
  PageTitle,
  Panel,
  Spinner,
  StatusPill,
  Table,
} from "../../ui/components";
import { Select } from "../../ui/Select";
import { Toast } from "../../ui/Toast";
import type { QuestionPublic } from "../../api/types";
import { usePublishQuestionVersion, useVerifyReferenceAnswer } from "../authoring/useAuthoring";
import { QuestionCoveragePanel } from "./QuestionCoveragePanel";
import { useQuestionList } from "./useQuestions";

/**
 * 题库列表（P06，`listQuestions` + `getQuestionTagCoverage`）。
 *
 * 版面顺序是产品定义的一部分：筛选 → **题目列表** → 知识点覆盖。教师进这一页的第一件事是
 * 看"题库里有哪些题"，所以列表必须在分析面板之前。
 *
 * 与角色相关的规则，都由服务端再次判定、这里只决定"要不要显示和请求"：
 *
 * 1. **学生不浏览全量题库**：这一页只对教师/助教/管理员开放；学生从作业详情进入 P07 看被布置的
 *    那一个已发布版本。对非出题角色，本页不请求课程列表、不请求题目列表、也不请求覆盖度。
 * 2. **标签覆盖度是教师分析**：面板与请求都只对出题角色存在。
 * 3. **发布状态是筛选项**（2.0）：契约 `listQuestions` 只有 `published_only` 这个布尔量——`true`
 *    表示"只要已发布"，`false` 表示"连草稿一起"。因此「草稿」这一档是 `published_only=false`
 *    + **在已加载的题目里筛 `status === "draft"`**：契约没有"只要草稿"的参数，这里也不假装有。
 * 4. **标签改成下拉**（2.0）：选项来自已加载题目里出现过的标签（在应用标签过滤**之前**取并集，
 *    否则选中一个标签后其他标签会一起消失）。契约 `tags` 是 all-of，单选一档天然等价。
 *
 * 题干搜索在**已加载的题目**里做：契约 `listQuestions` 没有文本参数，所以这里不声称"搜遍全库"；
 * 继续加载更多页后搜索范围随之扩大。
 */

type QuestionGroup = { key: string; label: string; rows: QuestionPublic[] };

/**
 * 按**课程名称**分组（不是按 code）：老师认的是课程名，code 是给系统看的标识。
 *
 * 组内保持原有顺序，组间按名称排序——折叠状态下老师按字母序找课程，展开后看到的是原顺序，
 * 两种顺序各自服务于不同的使用方式。
 */
function groupByCourse(
  rows: QuestionPublic[],
  labelOf: (courseId: string) => string,
): QuestionGroup[] {
  const groups = new Map<string, QuestionGroup>();
  for (const row of rows) {
    const label = labelOf(row.course_id);
    const existing = groups.get(label);
    if (existing) {
      existing.rows.push(row);
    } else {
      groups.set(label, { key: label, label, rows: [row] });
    }
  }
  return [...groups.values()].sort((left, right) => left.label.localeCompare(right.label, "zh"));
}

function statusTone(status: QuestionPublic["status"]): "ok" | "warn" | "idle" {
  if (status === "published") return "ok";
  if (status === "draft") return "warn";
  return "idle";
}

/** 列表里「发布」失败时，把服务端的结论翻译成老师能读的一句话。 */
function publishFailureMessage(error: unknown, forVerification: boolean): string {
  if (isApiError(error)) {
    if (error.status === 503) return t("authoring.verify.environment");
    if (error.isForbidden) return t("authoring.verify.forbidden");
    if (error.isNotFound) return t("authoring.verify.notFound");
    if (error.status === 422) {
      return forVerification
        ? t("authoring.verify.digestUnknown")
        : t("authoring.verify.publishFailed");
    }
  }
  return t("questions.publishFailed");
}

export function QuestionListPage() {
  const session = useSession();
  const author = canAuthorQuestions(session);
  const courses = useCourses();
  const courseOptions = loadedCourses(courses.data?.pages);
  const [courseId, setCourseId] = useState("");
  // 折叠状态按课程名称保存：切换筛选后同一门课保持老师上次的展开/收起选择。
  const [collapsedCourses, setCollapsedCourses] = useState<ReadonlySet<string>>(new Set());
  const [tagValue, setTagValue] = useState("");
  const [stemQuery, setStemQuery] = useState("");
  // 2.0：发布状态筛选。默认「已发布」——题库首先是已发布题目的地方，草稿切换一档就能看到。
  const [publishedState, setPublishedState] = useState<"published" | "draft">("published");

  const verify = useVerifyReferenceAnswer();
  const publish = usePublishQuestionVersion();
  const [publishingId, setPublishingId] = useState<string | null>(null);
  const [toast, setToast] = useState<{ key: number; message: string } | null>(null);
  const notify = (message: string) => setToast({ key: Date.now(), message });

  const questions = useQuestionList(
    author ? (courseId === "" ? null : courseId) : null,
    {
      publishedOnly: publishedState === "published",
      tags: tagValue === "" ? [] : [tagValue],
    },
    // 非出题角色连请求都不发（不是"发了再藏起来"）。
    { enabled: author },
  );
  // 分页结果摊平成一张表：游标只用于"还有没有下一页"，页面本身不暴露游标。
  const loadedRows = useMemo(
    () => (questions.data?.pages ?? []).flatMap((page) => page.items),
    [questions.data],
  );

  // 标签下拉的选项：来自已加载题目（**不受当前标签筛选影响**），再加上当前选中的那一档。
  const tagOptions = useMemo(() => {
    const seen = new Set<string>();
    for (const row of loadedRows) for (const tag of row.tags ?? []) seen.add(tag);
    if (tagValue !== "") seen.add(tagValue);
    return [...seen].sort((left, right) => left.localeCompare(right, "zh"));
  }, [loadedRows, tagValue]);

  const statusRows = useMemo(
    () =>
      publishedState === "draft"
        ? loadedRows.filter((row) => row.status === "draft")
        : loadedRows,
    [loadedRows, publishedState],
  );

  const query = stemQuery.trim().toLowerCase();
  const rows = useMemo(
    () =>
      query === ""
        ? statusRows
        : statusRows.filter((row) => row.stem_markdown.toLowerCase().includes(query)),
    [statusRows, query],
  );

  // 课程显示"代码 · 名称"；查不到就留白，绝不把内部编号贴到界面上。
  const courseLabel = (id: string): string => {
    const match = courseOptions.find((course) => course.course_id === id);
    return match ? `${match.course_code} · ${match.title}` : "—";
  };

  /**
   * 列表里直接发布（2.0 第 10 条）。
   *
   * 发布门禁在服务端：必须有与该版本 `content_digest` 绑定的参考解存证。列表页拿不到"当前是否
   * 已存证"，所以点击先**请求一次平台检验**（真的跑一遍参考解），三项满足再发布；任何一步失败都
   * 只说真实原因，不把"跑不起来"伪装成"题目不合格"。
   */
  const publishRow = async (row: QuestionPublic) => {
    const draftVersionId =
      row.versions?.find((version) => version.status === "draft")?.question_version_id ?? null;
    if (draftVersionId === null) {
      notify(t("questions.publishFailed"));
      return;
    }
    setPublishingId(row.question_id);
    try {
      let verification;
      try {
        verification = await verify.mutateAsync({ question_version_id: draftVersionId });
      } catch (error) {
        notify(publishFailureMessage(error, true));
        return;
      }
      if (
        verification.cases_passed !== verification.cases_total ||
        verification.attestation_id === null
      ) {
        notify(t("questions.publishBlockedReason"));
        return;
      }
      try {
        await publish.mutateAsync({
          questionId: row.question_id,
          questionVersionId: draftVersionId,
        });
        notify(t("questions.published"));
      } catch (error) {
        notify(publishFailureMessage(error, false));
      }
    } finally {
      setPublishingId(null);
    }
  };

  if (!author) {
    return (
      <div className="stack">
        <PageTitle>{t("nav.questions")}</PageTitle>
        <Panel title={t("questions.staffOnly")} testId="question-list-staff-only">
          <p className="muted">{t("questions.staffOnlyHint")}</p>
        </Panel>
      </div>
    );
  }

  return (
    <div className="stack">
      <div className="inline">
        <PageTitle>{t("nav.questions")}</PageTitle>
        <Link className="button" to="/questions/new" data-testid="question-new">
          {t("questions.newQuestion")}
        </Link>
      </div>

      <Toast
        key={toast?.key ?? 0}
        message={toast?.message ?? null}
        onClose={() => setToast(null)}
      />

      <Panel title={t("questions.filter")}>
        <div className="form-grid">
          <Field label={t("import.course")} htmlFor="question-course">
            <Select
              id="question-course"
              value={courseId}
              onChange={(event) => setCourseId(event.target.value)}
            >
              <option value="">{t("questions.selectCourse")}</option>
              {courseOptions.map((course) => (
                <option key={course.course_id} value={course.course_id}>
                  {formatCourseLabel(course)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t("questions.publishedState")} htmlFor="question-published-state">
            <Select
              id="question-published-state"
              value={publishedState}
              onChange={(event) =>
                setPublishedState(event.target.value === "draft" ? "draft" : "published")
              }
            >
              <option value="published">{t("questions.statePublished")}</option>
              <option value="draft">{t("questions.stateDraft")}</option>
            </Select>
          </Field>
          <Field label={t("questions.tagFilter")} htmlFor="question-tag-filter">
            <Select
              id="question-tag-filter"
              value={tagValue}
              onChange={(event) => setTagValue(event.target.value)}
            >
              <option value="">{t("questions.tagAll")}</option>
              {tagOptions.map((tag) => (
                <option key={tag} value={tag}>
                  {tag}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t("questions.searchStem")} htmlFor="question-stem-search">
            <input
              id="question-stem-search"
              value={stemQuery}
              placeholder={t("questions.searchStemPlaceholder")}
              onChange={(event) => setStemQuery(event.target.value)}
            />
          </Field>
        </div>
        <LoadMoreActions
          loadedCount={courseOptions.length}
          hasNextPage={courses.hasNextPage}
          isFetchingNextPage={courses.isFetchingNextPage}
          onLoadMore={() => {
            void courses.fetchNextPage();
          }}
          testId="courses"
        />
        {courses.isError ? <ErrorState error={courses.error} /> : null}
      </Panel>

      <Panel
        title={t("questions.countLoaded", { count: rows.length })}
        actions={
          <LoadMoreActions
            loadedCount={loadedRows.length}
            hasNextPage={questions.hasNextPage}
            isFetchingNextPage={questions.isFetchingNextPage}
            onLoadMore={() => {
              void questions.fetchNextPage();
            }}
            testId="questions"
          />
        }
      >
        {questions.isPending ? (
          <Spinner />
        ) : questions.isError ? (
          <Banner tone="error" testId="question-list-error">
            {t("questions.listError")}
          </Banner>
        ) : rows.length === 0 ? (
          <EmptyState>
            {query !== ""
              ? t("questions.noStemMatch")
              : tagValue !== "" || publishedState === "draft"
                ? t("questions.noMatches")
                : t("questions.emptyBank")}
          </EmptyState>
        ) : (
          <div data-testid="question-table">
            {groupByCourse(rows, courseLabel).map((group, index) => {
              const collapsed = collapsedCourses.has(group.key);
              return (
                <div key={group.key} className="question-group">
                  <button
                    type="button"
                    className="question-group__toggle"
                    data-testid={`question-group-toggle-${index}`}
                    aria-expanded={!collapsed}
                    onClick={() =>
                      setCollapsedCourses((previous) => {
                        const next = new Set(previous);
                        if (next.has(group.key)) {
                          next.delete(group.key);
                        } else {
                          next.add(group.key);
                        }
                        return next;
                      })
                    }
                  >
                    <span aria-hidden="true">{collapsed ? "▸" : "▾"}</span> {group.label}
                    <span className="muted"> （{group.rows.length}）</span>
                  </button>
                  {collapsed ? null : (
          <Table<QuestionPublic>
            testId={`question-group-${index}`}
            label={group.label}
            rows={group.rows}
            getRowKey={(row) => row.question_id}
            columns={[
              {
                key: "title",
                header: t("questions.title"),
                render: (row) => (
                  <Link
                    to="/questions/$questionId"
                    params={{ questionId: row.question_id }}
                    search={{ version: undefined }}
                  >
                    {row.title}
                  </Link>
                ),
              },
              {
                key: "language",
                header: t("questions.language"),
                render: (row) => t(`questions.language.${row.language}`),
              },
              {
                key: "status",
                header: t("grades.state"),
                render: (row) => (
                  <StatusPill tone={statusTone(row.status)}>
                    {t(`questions.status.${row.status}`)}
                  </StatusPill>
                ),
              },
              {
                key: "tags",
                header: t("questions.tags"),
                render: (row) =>
                  (row.tags?.length ?? 0) === 0 ? (
                    <span className="muted">{t("questions.tagsNone")}</span>
                  ) : (
                    <span className="inline" data-testid={`tags-${row.question_id}`}>
                      {row.tags?.map((tag) => (
                        <span className="pill pill--idle" key={tag}>
                          {tag}
                        </span>
                      ))}
                    </span>
                  ),
              },
              {
                key: "assets",
                header: t("questions.assets"),
                align: "right",
                render: (row) =>
                  (row.stem_assets?.length ?? 0) === 0 ? (
                    <span className="muted">{t("common.none")}</span>
                  ) : (
                    <span data-testid={`assets-${row.question_id}`}>
                      {row.stem_assets?.length}
                    </span>
                  ),
              },
              {
                key: "actions",
                header: t("questions.actions"),
                render: (row) => {
                  const draftVersionId =
                    row.versions?.find((version) => version.status === "draft")
                      ?.question_version_id ?? null;
                  const publishing = publishingId === row.question_id;
                  const canPublish = row.status === "draft" && draftVersionId !== null;
                  return (
                    <span className="inline">
                      <Link
                        className="button button--ghost"
                        to="/questions/$questionId/edit"
                        params={{ questionId: row.question_id }}
                        data-testid={`edit-${row.question_id}`}
                      >
                        {row.status === "draft"
                          ? t("questions.continueEditing")
                          : t("questions.newVersion")}
                      </Link>
                      {/*
                        草稿可以直接在列表发布（2.0 第 10 条）；已发布题目的这个按钮置灰。
                        点击会先请求一次平台检验——发布门禁要求"检验通过 + 存证已记录"。
                      */}
                      <button
                        type="button"
                        className="button button--ghost"
                        data-testid={`publish-${row.question_id}`}
                        disabled={!canPublish || publishing}
                        // 已发布的题目说明"已发布"；草稿但找不到草稿版本时才说真实原因。
                        title={
                          canPublish || publishing
                            ? undefined
                            : row.status === "draft"
                              ? t("questions.publishFailed")
                              : t("questions.published")
                        }
                        onClick={() => {
                          void publishRow(row);
                        }}
                      >
                        {publishing ? t("questions.publishing") : t("questions.publish")}
                      </button>
                    </span>
                  );
                },
              },
            ]}
          />
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Panel>

      {/*
        标签覆盖度（CHG-0020）与列表**共用**同一个课程作用域与同一个发布范围：若让它各自
        选一次课程，两个并排的数字就可能来自不同作用域，而那种矛盾看起来像"数据不对"。
        只在出题角色下渲染——组件内部才发请求，因此非出题角色连这次请求都不会发生。
        点一个标签即填进上面的标签筛选（复用既有实现，不另造筛选机制）。
      */}
      <QuestionCoveragePanel
        courseId={courseId === "" ? null : courseId}
        publishedOnly={publishedState === "published"}
        onSelectTag={(tag) => setTagValue(tag)}
      />
    </div>
  );
}
