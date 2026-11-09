import { useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { t } from "../../i18n";
import type { CreateAssignmentRequest, QuestionVersionSummary } from "../../api/types";
import { isApiError } from "../../api/problem";
import {
  courseLabel,
  loadedCourses,
  useCourses,
  useRosterSummary,
} from "../../api/directory";
import { useQuestionList } from "../questions/useQuestions";
import { Banner, ErrorState, Field, InlineLoader, Modal } from "../../ui/components";
import { shortenId } from "../../ui/format";
import { Select } from "../../ui/Select";
import { useCreateAssignment } from "./useAssignments";
import "./composerLayout.css";

/**
 * 新建作业（v0.32.0：课程级作业）。
 *
 * 作业属于**一门课程**，一经发布自动对该课程**全部课序与全部班级**的学生生效：
 * 契约里不再有 `section_ids` / `student_ids`，因此这里没有课序勾选，也没有指定学生。
 * 预览里的人数来自服务端 `roster-summary`（SQL 聚合），不是客户端数已加载的名单页——
 * 否则"这门课多少人"会随分页静默变化。
 *
 * 课程按 `course_id` 独立列出：同名课程用课程编号或短 id 区分，绝不按名字合并（合并会把
 * 另一门课的题目混进来，最终由服务端拒绝）。
 */

type PublishedVersion = QuestionVersionSummary & { questionTitle: string };

function localInputToIso(value: string): string | undefined {
  if (!value) return undefined;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return undefined;
  // `datetime-local` 是本地时间；契约要 UTC RFC 3339，因此先转 UTC 再序列化。
  return parsed.toISOString();
}

export function AssignmentComposer({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (assignmentId: string) => void;
}) {
  const navigate = useNavigate();
  const courses = useCourses();
  const courseOptions = useMemo(() => loadedCourses(courses.data?.pages), [courses.data]);
  const [courseId, setCourseId] = useState("");
  const [title, setTitle] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const selectedCourseId = courseId || (courseOptions[0]?.course_id ?? "");
  const selectedCourse = courseOptions.find(
    (course) => course.course_id === selectedCourseId,
  );
  // 课程按 `course_id` 独立列出；同名、同编号课程用短 id 区分，绝不合并成一项。
  const duplicateLabels = useMemo(() => {
    const seen = new Map<string, number>();
    for (const course of courseOptions) {
      const label = courseLabel(course);
      seen.set(label, (seen.get(label) ?? 0) + 1);
    }
    return new Set(
      [...seen.entries()].filter(([, count]) => count > 1).map(([label]) => label),
    );
  }, [courseOptions]);
  const courseOptionLabel = (course: {
    course_id: string;
    course_code?: string | null;
    title: string;
  }) => {
    const label = courseLabel(course);
    return duplicateLabels.has(label) ? `${label} · ${shortenId(course.course_id)}` : label;
  };

  // 受众规模来自服务端聚合；它只是预览信息，不参与发布校验（受众永远由服务端按课程名单判定）。
  const summary = useRosterSummary(selectedCourseId || null);

  const questions = useQuestionList(
    selectedCourseId || null,
    { publishedOnly: true, tags: [] },
    { enabled: Boolean(selectedCourseId) },
  );

  const publishedVersions = useMemo<PublishedVersion[]>(() => {
    const rows: PublishedVersion[] = [];
    for (const page of questions.data?.pages ?? []) {
      for (const question of page.items) {
        for (const version of question.versions ?? []) {
          if (version.status === "published") {
            rows.push({ ...version, questionTitle: question.title });
          }
        }
      }
    }
    return rows;
  }, [questions.data]);

  const create = useCreateAssignment();

  const submit = () => {
    setError(null);
    if (title.trim() === "") {
      setError(t("assignments.create.errorTitle"));
      return;
    }
    if (selected.length === 0) {
      setError(t("assignments.create.errorVersions"));
      return;
    }
    setPreviewing(true);
  };

  const publish = () => {
    const body: CreateAssignmentRequest = {
      course_id: selectedCourseId,
      title: title.trim(),
      question_version_ids: selected,
    };
    const isoDue = localInputToIso(dueAt);
    if (isoDue) body.due_at = isoDue;
    create.mutate(body, {
      onSuccess: (created) => {
        setPreviewing(false);
        onCreated(created.assignment_id);
        onClose();
        // 详情页是新建结果的唯一可信展示（服务端已存储），因此直接进入它。
        navigate({
          to: "/assignments/$assignmentId",
          params: { assignmentId: created.assignment_id },
          search: {},
        });
      },
      onError: (failure) => {
        setPreviewing(false);
        if (isApiError(failure) && failure.status === 409) {
          setError(t("assignments.create.conflict"));
          return;
        }
        if (isApiError(failure) && failure.status === 422) {
          setError(t("assignments.create.invalidRequest"));
          return;
        }
        if (isApiError(failure) && failure.status === 403) {
          setError(t("assignments.create.forbidden"));
          return;
        }
        if (isApiError(failure) && failure.status === 503) {
          setError(t("assignments.create.unavailable"));
          return;
        }
        setError(isApiError(failure) ? failure.message : t("common.requestFailed"));
      },
    });
  };

  const audienceValue = (value: number | undefined) =>
    value === undefined ? t("common.none") : String(value);

  const audienceSummary = summary.isPending ? (
    <p className="muted" data-testid="compose-audience-loading">
      <InlineLoader />
    </p>
  ) : summary.isError ? (
    <Banner tone="warn" testId="compose-audience-error">
      {t("assignments.create.summaryUnavailable")}
    </Banner>
  ) : summary.data ? (
    <dl className="stack" data-testid="compose-audience-summary">
      <div className="inline">
        <dt className="muted">{t("assignments.create.previewSections")}</dt>
        <dd data-testid="compose-preview-section-count">
          {audienceValue(summary.data.section_count)}
        </dd>
        <dt className="muted">{t("assignments.create.previewClasses")}</dt>
        <dd data-testid="compose-preview-class-count">
          {audienceValue(summary.data.class_count)}
        </dd>
        <dt className="muted">{t("assignments.create.previewStudents")}</dt>
        <dd data-testid="compose-preview-student-total">
          {audienceValue(summary.data.student_total)}
        </dd>
        <dt className="muted">{t("assignments.create.previewBound")}</dt>
        <dd data-testid="compose-preview-bound">{audienceValue(summary.data.bound_count)}</dd>
        <dt className="muted">{t("assignments.create.previewUnbound")}</dt>
        <dd data-testid="compose-preview-unbound">
          {audienceValue(summary.data.unbound_count)}
        </dd>
      </div>
    </dl>
  ) : null;

  return (
    <>
      <Modal
        title={t("assignments.create.title")}
        onClose={onClose}
        testId="assignment-composer"
        className="modal--tall assignment-composer"
      >
        <div className="composer">
          <div className="composer__grid">
            <div className="composer__row--wide">
              <Field label={t("grades.course")} htmlFor="compose-course">
                <Select
                  id="compose-course"
                  value={selectedCourseId}
                  onChange={(event) => {
                    setCourseId(event.target.value);
                    // 换课程等于换题目来源与受众：已选版本不能沿用。
                    setSelected([]);
                  }}
                  disabled={create.isPending}
                >
                  {courseOptions.length === 0 ? (
                    <option value="">{t("common.selectPrompt")}</option>
                  ) : null}
                  {courseOptions.map((course) => (
                    <option key={course.course_id} value={course.course_id}>
                      {courseOptionLabel(course)}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>

            <div className="composer__row--wide">
              <Field label={t("assignments.create.name")} htmlFor="compose-title">
                <input
                  id="compose-title"
                  value={title}
                  maxLength={200}
                  placeholder={t("assignments.create.namePlaceholder")}
                  onChange={(event) => setTitle(event.target.value)}
                  disabled={create.isPending}
                />
              </Field>
            </div>

            <Field
              label={t("assignments.create.due")}
              htmlFor="compose-due"
              hint={t("assignments.create.dueHint")}
            >
              <input
                id="compose-due"
                type="datetime-local"
                value={dueAt}
                onChange={(event) => setDueAt(event.target.value)}
                disabled={create.isPending}
              />
            </Field>

            {/* 打分标准（PDF 要求）：百分制是固定口径，不新增契约字段，这里只做说明。 */}
            <div className="field">
              <span>{t("assignments.create.scoreScale")}</span>
              <p className="muted composer__scale" data-testid="compose-score-scale">
                {t("assignments.create.scoreScaleValue")}
              </p>
            </div>
          </div>

          {courses.isError ? <ErrorState error={courses.error} testId="compose-courses-error" /> : null}

          <Banner tone="info" testId="compose-course-wide-notice">
            {t("assignments.create.courseWideNotice")}
          </Banner>

          <fieldset className="composer__versions">
            <legend>
              {t("assignments.create.versions")}
              <span className="muted"> · {t("assignments.create.versionsHint")}</span>
            </legend>

            <div className="composer__versions-bar">
              <span className="muted" data-testid="compose-version-count">
                {t("assignments.create.chosen", { count: selected.length })}
              </span>
              {publishedVersions.length > 0 ? (
                <div className="inline">
                  <button
                    type="button"
                    className="button button--ghost"
                    data-testid="compose-version-all"
                    disabled={create.isPending}
                    onClick={() =>
                      setSelected(publishedVersions.map((version) => version.question_version_id))
                    }
                  >
                    {t("assignments.create.selectAll")}
                  </button>
                  <button
                    type="button"
                    className="button button--ghost"
                    data-testid="compose-version-none"
                    disabled={create.isPending || selected.length === 0}
                    onClick={() => setSelected([])}
                  >
                    {t("assignments.create.clearAll")}
                  </button>
                </div>
              ) : null}
            </div>

            {questions.isPending ? (
              <p className="muted">
                <InlineLoader />
              </p>
            ) : publishedVersions.length === 0 ? (
              <p className="muted">{t("assignments.create.noVersions")}</p>
            ) : (
              <ul className="composer__version-list" data-testid="compose-version-list">
                {publishedVersions.map((version) => {
                  const checked = selected.includes(version.question_version_id);
                  return (
                    <li key={version.question_version_id}>
                      <label>
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={create.isPending}
                          onChange={() =>
                            setSelected((previous) =>
                              checked
                                ? previous.filter((id) => id !== version.question_version_id)
                                : [...previous, version.question_version_id],
                            )
                          }
                        />
                        <span>
                          {version.questionTitle} · v{version.version}
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </fieldset>

          {error ? (
            <Banner tone="error" testId="assignment-compose-error">
              {error}
            </Banner>
          ) : null}

          <div className="composer__foot">
            <button type="button" className="button button--ghost" onClick={onClose}>
              {t("common.cancel")}
            </button>
            <button
              type="button"
              className="button"
              data-testid="assignment-compose-preview"
              disabled={create.isPending}
              onClick={submit}
            >
              {t("assignments.create.preview")}
            </button>
          </div>
        </div>
      </Modal>

      {previewing ? (
        <Modal title={t("assignments.create.confirmTitle")} onClose={() => setPreviewing(false)}>
          <div className="stack" data-testid="assignment-compose-confirm">
            <p>{t("assignments.create.confirmBody")}</p>
            <p className="muted" data-testid="compose-preview-audience-notice">
              {t("assignments.create.courseWideNotice")}
            </p>
            <dl className="stack">
              <div>
                <dt className="muted">{t("grades.course")}</dt>
                <dd data-testid="compose-preview-course">
                  {selectedCourse ? courseOptionLabel(selectedCourse) : selectedCourseId}
                </dd>
              </div>
              <div>
                <dt className="muted">{t("assignments.audience")}</dt>
                <dd>{audienceSummary}</dd>
              </div>
              <div>
                <dt className="muted">{t("assignments.create.name")}</dt>
                <dd>{title.trim()}</dd>
              </div>
              <div>
                <dt className="muted">{t("assignments.create.due")}</dt>
                <dd>{dueAt ? new Date(dueAt).toLocaleString() : t("assignments.noDue")}</dd>
              </div>
              <div>
                <dt className="muted">{t("assignments.create.versions")}</dt>
                <dd data-testid="compose-preview-questions">
                  {t("assignments.create.chosen", { count: selected.length })}
                </dd>
              </div>
            </dl>
            <div className="inline">
              <button
                type="button"
                className="button button--ghost"
                onClick={() => setPreviewing(false)}
                disabled={create.isPending}
              >
                {t("common.cancel")}
              </button>
              <button
                type="button"
                className="button"
                data-testid="assignment-compose-confirm-button"
                onClick={publish}
                disabled={create.isPending}
              >
                {create.isPending ? t("assignments.create.saving") : t("assignments.create.confirm")}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </>
  );
}
