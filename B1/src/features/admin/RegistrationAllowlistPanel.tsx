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
                  <button
                    type="button"
                    className="button button--ghost"
                    aria-expanded={expanded === batch.batch_id}
                    data-testid={`allowlist-expand-${batch.batch_id}`}
                    onClick={() =>
                      setExpanded(expanded === batch.batch_id ? null : batch.batch_id)
                    }
                  >
                    <span aria-hidden="true">{expanded === batch.batch_id ? "▾" : "▸"}</span>{" "}
                    {batch.class_name}
                  </button>
                  <span className="muted" data-testid={`allowlist-count-${batch.batch_id}`}>
                    {t("allowlist.students", { count: batch.row_count })}
                  </span>
                  <span className="muted">{batch.uploaded_at.slice(0, 19).replace("T", " ")}</span>
                  <button
                    type="button"
                    className="button button--ghost"
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
    <table className="allowlist-entries" data-testid={`allowlist-entries-${batchId}`}>
      <tbody>
        {rows.map((entry) => (
          <tr key={entry.entry_id} data-testid={`allowlist-entry-${entry.entry_id}`}>
            <td>{entry.display_name}</td>
            <td className="mono-id">{entry.student_number}</td>
            <td className="allowlist-entries__action">
              <button
                type="button"
                className="button button--ghost"
                aria-label={t("allowlist.deleteStudent", { name: entry.display_name })}
                data-testid={`allowlist-delete-entry-${entry.entry_id}`}
                onClick={() => removeEntry.mutate(entry.entry_id)}
              >
                ✕
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
