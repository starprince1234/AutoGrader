import { useRef, useState, type ChangeEvent } from "react";
import { t } from "../../i18n";
import { describeApiError } from "../../api/problem";
import { formatDateTime } from "../../ui/format";
import { Banner, EmptyState, ErrorState, Modal, Panel, Spinner } from "../../ui/components";
import {
  type AllowlistBatch,
  type AllowlistEntry,
  useAddAllowlistBatch,
  useAllowlistBatches,
  useAllowlistEntries,
  useDeleteAllowlistBatch,
  useDeleteAllowlistEntry,
} from "./useAllowlist";

/**
 * 注册名单：一班一行，点开看学生，行内可删。
 *
 * 三个刻意的选择：
 * 1. **文件名就是班级名**：老师按班级命名文件，所以不再要求第二次输入班级；
 * 2. **添加名单直接开系统文件选择器**（隐藏的 file input），没有中转预览步骤——
 *    这个操作等价于"把这个班的名单放进来"，预览只会在中间多一次点击；
 * 3. **删学生/删班级都立即生效**，但删班级会连带删掉该班所有名单行——界面上明确写出这一点，
 *    避免"只是删了个表格"的误解。
 */
export function RegistrationAllowlistPanel() {
  const batches = useAllowlistBatches();
  const addBatch = useAddAllowlistBatch();
  const deleteBatch = useDeleteAllowlistBatch();

  const [expanded, setExpanded] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [pendingBatch, setPendingBatch] = useState<AllowlistBatch | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const items = batches.data?.items ?? [];

  const pickFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) {
      return;
    }
    setNotice(null);
    setError(null);
    addBatch.mutate(file, {
      onSuccess: (result) => {
        // "更新了某班"与"新增了某班"必须说清楚：两者对名单的影响完全不同。
        const key = result.replaced
          ? result.skipped_row_count > 0
            ? "allowlist.updatedWithSkipped"
            : "allowlist.updated"
          : result.skipped_row_count > 0
            ? "allowlist.addedWithSkipped"
            : "allowlist.added";
        setNotice(
          t(key, {
            name: result.class_name,
            count: result.row_count,
            skipped: result.skipped_row_count,
          }),
        );
      },
      onError: (failure) => setError(failure),
    });
  };

  return (
    <div className="stack" data-testid="allowlist-panel">
      <Panel
        title={t("allowlist.registered")}
        actions={
          <>
            {/* 真正的按钮：键盘用户能 Tab 到它并用 Enter/Space 打开文件选择器；
                直接给隐藏的 file input 套 label 时，label 不可聚焦，键盘用户没有入口。 */}
            <button
              type="button"
              className="button button--primary"
              data-testid="allowlist-add"
              disabled={addBatch.isPending}
              onClick={() => fileInputRef.current?.click()}
            >
              {t("allowlist.add")}
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,.xlsx,.xls,.pdf,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel,application/pdf"
              data-testid="allowlist-add-input"
              hidden
              onChange={pickFile}
            />
          </>
        }
      >
        {notice ? (
          <Banner tone="success" testId="allowlist-notice">
            {notice}
          </Banner>
        ) : null}
        {error ? (
          <Banner tone="error" testId="allowlist-error">
            {describeApiError(error)}
          </Banner>
        ) : null}
        {batches.isPending ? (
          <Spinner />
        ) : batches.isError ? (
          <ErrorState error={batches.error} />
        ) : items.length === 0 ? (
          <div data-testid="allowlist-empty">
            <EmptyState>{t("allowlist.empty")}</EmptyState>
          </div>
        ) : (
          <ul className="allowlist-batches" data-testid="allowlist-batches">
            {items.map((batch) => (
              <li key={batch.batch_id} data-testid={`allowlist-batch-${batch.batch_id}`}>
                <div className="allowlist-batch__head">
                  {/* 整行（除删除按钮）都是展开开关：可见区域与可点击区域一致。 */}
                  <button
                    type="button"
                    className="allowlist-batch__toggle"
                    aria-expanded={expanded === batch.batch_id}
                    data-testid={`allowlist-expand-${batch.batch_id}`}
                    onClick={() =>
                      setExpanded(expanded === batch.batch_id ? null : batch.batch_id)
                    }
                  >
                    <span className="allowlist-batch__name">
                      <span aria-hidden="true">{expanded === batch.batch_id ? "▾" : "▸"}</span>{" "}
                      {batch.class_name}
                    </span>
                    <span className="muted" data-testid={`allowlist-count-${batch.batch_id}`}>
                      {t("allowlist.students", { count: batch.row_count })}
                    </span>
                    <span className="muted">
                      {formatDateTime(batch.uploaded_at, t("common.none"))}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="button button--ghost allowlist-batch__delete"
                    data-testid={`allowlist-delete-${batch.batch_id}`}
                    disabled={deleteBatch.isPending}
                    onClick={() => {
                      setError(null);
                      deleteBatch.reset();
                      setPendingBatch(batch);
                    }}
                  >
                    {t("allowlist.deleteClass")}
                  </button>
                </div>
                {expanded === batch.batch_id ? <BatchEntries batchId={batch.batch_id} /> : null}
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {pendingBatch ? (
        <Modal
          title={t("allowlist.deleteClassTitle")}
          testId="allowlist-delete-class-modal"
          onClose={() => {
            if (!deleteBatch.isPending) setPendingBatch(null);
          }}
        >
          <div className="stack">
            <p>
              {t("allowlist.deleteClassBody", {
                name: pendingBatch.class_name,
                count: pendingBatch.row_count,
              })}
            </p>
            {deleteBatch.isError ? (
              <Banner tone="error" testId="allowlist-delete-class-error">
                {describeApiError(deleteBatch.error)}
              </Banner>
            ) : null}
            <div className="inline">
              <button
                type="button"
                className="button button--danger"
                data-testid="allowlist-delete-class-confirm"
                disabled={deleteBatch.isPending}
                onClick={() => {
                  deleteBatch.mutate(pendingBatch.batch_id, {
                    onSuccess: () => {
                      if (expanded === pendingBatch.batch_id) {
                        setExpanded(null);
                      }
                      setNotice(t("allowlist.deleted", { name: pendingBatch.class_name }));
                      setPendingBatch(null);
                    },
                  });
                }}
              >
                {deleteBatch.isPending
                  ? t("allowlist.deleting")
                  : t("allowlist.deleteClassConfirm")}
              </button>
              <button
                type="button"
                className="button button--ghost"
                data-testid="allowlist-delete-class-cancel"
                disabled={deleteBatch.isPending}
                onClick={() => setPendingBatch(null)}
              >
                {t("common.cancel")}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}

function BatchEntries({ batchId }: { batchId: string }) {
  const entries = useAllowlistEntries(batchId);
  const removeEntry = useDeleteAllowlistEntry(batchId);
  const [pendingEntry, setPendingEntry] = useState<AllowlistEntry | null>(null);

  if (entries.isPending) {
    return <Spinner />;
  }
  if (entries.isError) {
    return <ErrorState error={entries.error} />;
  }
  const rows = entries.data?.items ?? [];
  if (rows.length === 0) {
    return <p className="muted">{t("allowlist.noStudents")}</p>;
  }
  return (
    <>
      <ul className="allowlist-entries" data-testid={`allowlist-entries-${batchId}`}>
        {rows.map((entry) => (
          <li
            key={entry.entry_id}
            className="allowlist-entry"
            data-testid={`allowlist-entry-${entry.entry_id}`}
          >
            <span className="allowlist-entry__name">{entry.display_name}</span>
            <span className="mono-id allowlist-entry__number">{entry.student_number}</span>
            <button
              type="button"
              className="button button--ghost allowlist-entry__delete"
              aria-label={t("allowlist.deleteStudent", { name: entry.display_name })}
              title={t("allowlist.deleteStudent", { name: entry.display_name })}
              data-testid={`allowlist-delete-entry-${entry.entry_id}`}
              disabled={removeEntry.isPending}
              onClick={() => {
                removeEntry.reset();
                setPendingEntry(entry);
              }}
            >
              <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">
                <path
                  d="M9 3h6l1 2h4v2H4V5h4l1-2zm-3 6h12l-1 12H7L6 9zm3 2v8h2v-8H9zm4 0v8h2v-8h-2z"
                  fill="currentColor"
                />
              </svg>
            </button>
          </li>
        ))}
      </ul>

      {pendingEntry ? (
        <Modal
          title={t("allowlist.deleteStudentTitle")}
          testId="allowlist-delete-entry-modal"
          onClose={() => {
            if (!removeEntry.isPending) setPendingEntry(null);
          }}
        >
          <div className="stack">
            <p>
              {t("allowlist.deleteStudentBody", {
                name: pendingEntry.display_name,
                number: pendingEntry.student_number,
              })}
            </p>
            {removeEntry.isError ? (
              <Banner tone="error" testId="allowlist-delete-entry-error">
                {describeApiError(removeEntry.error)}
              </Banner>
            ) : null}
            <div className="inline">
              <button
                type="button"
                className="button button--danger"
                data-testid="allowlist-delete-entry-confirm"
                disabled={removeEntry.isPending}
                onClick={() => {
                  removeEntry.mutate(pendingEntry.entry_id, {
                    onSuccess: () => setPendingEntry(null),
                  });
                }}
              >
                {removeEntry.isPending
                  ? t("allowlist.deleting")
                  : t("allowlist.deleteStudentConfirm")}
              </button>
              <button
                type="button"
                className="button button--ghost"
                data-testid="allowlist-delete-entry-cancel"
                disabled={removeEntry.isPending}
                onClick={() => setPendingEntry(null)}
              >
                {t("common.cancel")}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </>
  );
}
