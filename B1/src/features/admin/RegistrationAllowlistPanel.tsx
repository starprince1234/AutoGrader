import { useState } from "react";
import { t } from "../../i18n";
import { describeApiError } from "../../api/problem";
import { Banner, EmptyState, ErrorState, Panel, Spinner } from "../../ui/components";
import {
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

  const items = batches.data?.items ?? [];

  return (
    <div className="stack" data-testid="allowlist-panel">
      <Panel
        title={t("allowlist.registered")}
        actions={
          <label className="button button--primary" data-testid="allowlist-add-label">
            {t("allowlist.add")}
            <input
              type="file"
              accept=".csv,text/csv"
              data-testid="allowlist-add"
              style={{ display: "none" }}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (!file) {
                  return;
                }
                setNotice(null);
                setError(null);
                addBatch.mutate(file, {
                  onSuccess: (result) => {
                    setNotice(
                      result.skipped_row_count > 0
                        ? t("allowlist.addedWithSkipped", {
                            name: result.class_name,
                            count: result.row_count,
                            skipped: result.skipped_row_count,
                          })
                        : t("allowlist.added", {
                            name: result.class_name,
                            count: result.row_count,
                          }),
                    );
                  },
                  onError: (failure) => setError(failure),
                });
              }}
            />
          </label>
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
                      {batch.uploaded_at.slice(0, 19).replace("T", " ")}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="button button--ghost allowlist-batch__delete"
                    data-testid={`allowlist-delete-${batch.batch_id}`}
                    onClick={() => {
                      setError(null);
                      deleteBatch.mutate(batch.batch_id, {
                        onSuccess: () => {
                          if (expanded === batch.batch_id) {
                            setExpanded(null);
                          }
                          setNotice(t("allowlist.deleted", { name: batch.class_name }));
                        },
                        onError: (failure) => setError(failure),
                      });
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
    </div>
  );
}

function BatchEntries({ batchId }: { batchId: string }) {
  const entries = useAllowlistEntries(batchId);
  const removeEntry = useDeleteAllowlistEntry(batchId);

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
    <ul className="allowlist-entries" data-testid={`allowlist-entries-${batchId}`}>
      {rows.map((entry) => (
        <li key={entry.entry_id} className="allowlist-entry" data-testid={`allowlist-entry-${entry.entry_id}`}>
          <span className="allowlist-entry__name">{entry.display_name}</span>
          <span className="mono-id allowlist-entry__number">{entry.student_number}</span>
          <button
            type="button"
            className="button button--ghost allowlist-entry__delete"
            aria-label={t("allowlist.deleteStudent", { name: entry.display_name })}
            title={t("allowlist.deleteStudent", { name: entry.display_name })}
            data-testid={`allowlist-delete-entry-${entry.entry_id}`}
            onClick={() => removeEntry.mutate(entry.entry_id)}
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
  );
}
