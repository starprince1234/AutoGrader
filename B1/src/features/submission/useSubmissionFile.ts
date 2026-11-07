import { useQuery } from "@tanstack/react-query";
import { apiRequestRaw, apiRequestText } from "../../api/client";
import { queryKeys } from "../../app/queryClient";
import { log, newRequestId } from "../../logging/logger";

/**
 * 提交答案文件的受控读取（CHG-0032）。
 *
 * `GET /api/v1/submissions/{id}/files?path=<N.txt>` 需要 `Authorization: Bearer …`，
 * 浏览器不会为裸 `<a href>`/`<img>` 附加请求头（B1 的访问令牌只在内存里），因此：
 * - 展示答案：用本文件的 hook 取文本，交给 React 以文本节点渲染（`<pre>` 内自动转义）；
 * - 下载附件：走带鉴权的 fetch，再用本模块创建的对象 URL 触发下载；
 * - **内容不写日志**，也不缓存到 localStorage。
 */

/** 教师端答案预览的字符上限：超出只显示前 N 字符并如实说明被截断。 */
export const MAX_ANSWER_PREVIEW_CHARS = 200_000;

export function submissionFileQueryKey(submissionId: string, path: string) {
  return queryKeys.submissionFile(submissionId, path);
}

/** 读取一道题的答案文本（`path` 形如 `1.txt`）。 */
export function useSubmissionFileText(
  submissionId: string | null,
  path: string | null,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: queryKeys.submissionFile(submissionId ?? "", path ?? ""),
    enabled: Boolean(submissionId) && Boolean(path) && (options?.enabled ?? true),
    // 403/404 是确定性结论，重试只会把"没有权限"拖成"加载中"。
    retry: false,
    queryFn: async (): Promise<string> =>
      apiRequestText(`/api/v1/submissions/${submissionId as string}/files`, {
        query: { path: path as string },
      }),
  });
}

/**
 * 下载一个提交文件（保持服务端原字节）。
 *
 * 返回实际下载的字节数；文件名优先取 `Content-Disposition`，没有就用契约里的
 * 提交相对路径（`1.txt` 等）。
 */
export async function downloadSubmissionFile(
  submissionId: string,
  path: string,
): Promise<{ filename: string; bytes: number }> {
  const requestId = newRequestId();
  const { response } = await apiRequestRaw(`/api/v1/submissions/${submissionId}/files`, {
    query: { path },
    accept: "application/octet-stream",
    requestId,
  });
  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") ?? "";
  const matched = /filename\*?=(?:UTF-8''|")?([^";]+)/i.exec(disposition);
  const filename = matched ? decodeURIComponent(matched[1].replace(/"/g, "")) : path;

  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(url);
  }

  log({
    event: "submission_file_downloaded",
    request_id: requestId,
    submission_id: submissionId,
    // 只记文件名与大小，不记内容（答案不写日志）。
    file_path: path,
    bytes: blob.size,
  });
  return { filename, bytes: blob.size };
}
