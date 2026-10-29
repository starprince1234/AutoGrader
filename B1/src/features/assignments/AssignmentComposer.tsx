import { useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { t } from "../../i18n";
import type { CreateAssignmentRequest, QuestionVersionSummary } from "../../api/types";
import { isApiError } from "../../api/problem";
import { loadedCourses, useSections } from "../../api/directory";
import { useCourses } from "../../api/directory";
import { useQuestionList } from "../questions/useQuestions";
import { Banner, Drawer, Field, Modal } from "../../ui/components";
import { useCreateAssignment } from "./useAssignments";
import { courseLabel as formatCourseLabel } from "../../api/directory";

/**
 * 新建作业（P04 的主操作）。
 *
 * 只有**已发布**版本可以选题：`POST /assignments` 对草稿引用返回 `409`，因此选择器里根本
 * 不出现草稿——让用户挑一个必然被拒绝的选项是设计缺陷，不是"权限提示"。
 *
 * 发布分两步：先看预览（课程/课序/名称/截止时间/题目数），再二次确认才发 `POST`。
 * 这样"点错一下就把作业发给全班"不可能发生。
 */

type DraftVersion = QuestionVersionSummary & { questionTitle: string };

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
  const [sectionId, setSectionId] = useState("");
  const [title, setTitle] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const selectedCourseId = courseId || (courseOptions[0]?.course_id ?? "");
  const sections = useSections(selectedCourseId || null);
  const questions = useQuestionList(
    selectedCourseId || null,
    { publishedOnly: true, tags: [] },
    { enabled: Boolean(selectedCourseId) },
  );

  const publishedVersions = useMemo<DraftVersion[]>(() => {
    const rows: DraftVersion[] = [];
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
  const sectionLabelOf = (id: string) =>
    sections.data?.find((option) => option.sectionId === id)?.label ?? id;

  const submit = () => {
    setError(null);
    if (title.trim() === "") {
      setError(t("assignments.create.errorTitle"));
      return;
    }
    if (sectionId === "") {
      setError(t("assignments.create.errorSection"));
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
      section_id: sectionId,
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
        });
      },
      onError: (failure) => {
        setPreviewing(false);
        if (isApiError(failure) && failure.status === 409) {
          setError(t("assignments.create.conflict"));
          return;
        }
        setError(isApiError(failure) ? failure.message : t("common.requestFailed"));
      },
    });
  };

  return (
    <>
      <Drawer title={t("assignments.create.title")} onClose={onClose} testId="assignment-composer">
        <div className="stack">
          <Field label={t("grades.course")} htmlFor="compose-course">
            <select
              id="compose-course"
              value={selectedCourseId}
              onChange={(event) => {
                setCourseId(event.target.value);
                setSectionId("");
                setSelected([]);
              }}
              disabled={create.isPending}
            >
              {courseOptions.map((course) => (
                <option key={course.course_id} value={course.course_id}>
                  {formatCourseLabel(course)}
                </option>
              ))}
            </select>
          </Field>

          <Field
            label={t("grades.section")}
            htmlFor="compose-section"
            error={sectionId === "" ? undefined : undefined}
          >
            <select
              id="compose-section"
              value={sectionId}
              onChange={(event) => {
                setSectionId(event.target.value);
                setSelected([]);
              }}
              disabled={create.isPending || selectedCourseId === ""}
            >
              <option value="">{t("common.select")}</option>
              {(sections.data ?? []).map((option) => (
                <option key={option.sectionId} value={option.sectionId}>
                  {option.label}
                </option>
              ))}
            </select>
          </Field>

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

          <fieldset className="stack">
            <legend>
              {t("assignments.create.versions")}
              <span className="muted"> · {t("assignments.create.versionsHint")}</span>
            </legend>
            {questions.isPending ? (
              <p className="muted">{t("common.loading")}</p>
            ) : publishedVersions.length === 0 ? (
              <p className="muted">{t("assignments.create.noVersions")}</p>
            ) : (
              <ul className="stack" data-testid="compose-version-list">
                {publishedVersions.map((version) => {
                  const checked = selected.includes(version.question_version_id);
                  return (
                    <li key={version.question_version_id}>
                      <label className="inline">
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

          <div className="inline">
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
      </Drawer>

      {previewing ? (
        <Modal title={t("assignments.create.confirmTitle")} onClose={() => setPreviewing(false)}>
          <div className="stack" data-testid="assignment-compose-confirm">
            <p>{t("assignments.create.confirmBody")}</p>
            <dl className="stack">
              <div>
                <dt className="muted">{t("grades.course")}</dt>
                <dd>
                  {courseOptions.find((course) => course.course_id === selectedCourseId)
                    ?.title ?? selectedCourseId}
                </dd>
              </div>
              <div>
                <dt className="muted">{t("grades.section")}</dt>
                <dd>{sectionLabelOf(sectionId)}</dd>
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
                <dd>{t("assignments.create.chosen", { count: selected.length })}</dd>
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
