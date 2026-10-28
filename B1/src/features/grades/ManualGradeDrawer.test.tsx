import { describe, expect, it } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../testing/renderWithProviders";
import {
  installFetchStub,
  jsonResponse,
  problemResponse,
  type StubbedRoute,
} from "../../testing/httpBoundary";
import { ManualGradeDrawer } from "./ManualGradeDrawer";

/**
 * 人工评分抽屉（P11 的"待评分 → 老师打分"入口）。
 *
 * 这几条断言是功能本身，不是外观：
 * 1. 老师必须能看到学生交了什么（邮件正文摘录 + 附件）；
 * 2. 网页提交没有来信时要说清楚，而不是显示空白；
 * 3. **改分必须带理由**——服务端会 409，前端不该先给出一个必然失败的按钮；
 * 4. 保存时把 GET 拿到的 `updated_at` 原样回传（否则乐观锁形同虚设）。
 */

const UUID_SUBMISSION = "11111111-1111-4111-8111-111111111111";
const UPDATED_AT = "2026-10-28T08:00:00.000Z";

function routes(options: {
  grade?: { score: number; updated_at: string } | null;
  intake?: "message" | "none";
  onSave?: (body: unknown) => void;
}): StubbedRoute[] {
  return [
    {
      method: "GET",
      path: `/api/v1/submissions/${UUID_SUBMISSION}/manual-grade`,
      respond: () =>
        options.grade
          ? jsonResponse({
              submission_id: UUID_SUBMISSION,
              score: options.grade.score,
              score_source: "manual",
              grade_status: "graded",
              comment: null,
              graded_by: "22222222-2222-4222-8222-222222222222",
              graded_at: options.grade.updated_at,
              updated_at: options.grade.updated_at,
              revision: 1,
            })
          : new Response(null, { status: 204 }),
    },
    {
      method: "GET",
      path: `/api/v1/submissions/${UUID_SUBMISSION}/intake-message`,
      respond: () =>
        options.intake === "none"
          ? problemResponse({ status: 404, title: "Not Found", detail: "no message" })
          : jsonResponse({
              subject: "2024141460192_1",
              sender: "2024141460192@qq.com",
              received_at: "2026-10-28T07:00:00.000Z",
              body_excerpt: "老师好，这是第一次作业的命令行。",
              truncated: false,
              attachment_names: ["2024141460192.txt"],
            }),
    },
    {
      method: "POST",
      path: `/api/v1/submissions/${UUID_SUBMISSION}/manual-grade`,
      respond: ({ request }) => {
        options.onSave?.(request.body);
        return jsonResponse({
          submission_id: UUID_SUBMISSION,
          score: 88,
          score_source: "manual",
          grade_status: "graded",
          comment: null,
          graded_by: "22222222-2222-4222-8222-222222222222",
          graded_at: UPDATED_AT,
          updated_at: UPDATED_AT,
          revision: 2,
        });
      },
    },
  ];
}

describe("人工评分抽屉", () => {
  it("显示来信正文与附件下载，首评不需要理由", async () => {
    const stub = installFetchStub(routes({ grade: null, intake: "message" }));
    try {
      renderWithProviders(<ManualGradeDrawer submissionId={UUID_SUBMISSION} onClose={() => {}} />);
      expect(await screen.findByTestId("intake-message")).toBeTruthy();
      expect(screen.getByTestId("intake-body").textContent).toContain("第一次作业");
      expect(screen.getByTestId("download-2024141460192.txt")).toBeTruthy();
      // 首评：不出现"改分理由"
      expect(screen.queryByTestId("manual-reason")).toBeNull();
    } finally {
      stub.restore();
    }
  });

  it("网页提交没有来信时明确告知，而不是留空", async () => {
    const stub = installFetchStub(routes({ grade: null, intake: "none" }));
    try {
      renderWithProviders(<ManualGradeDrawer submissionId={UUID_SUBMISSION} onClose={() => {}} />);
      expect(await screen.findByTestId("intake-none")).toBeTruthy();
    } finally {
      stub.restore();
    }
  });

  it("改分时理由必填：没有理由不能保存", async () => {
    const stub = installFetchStub(
      routes({ grade: { score: 70, updated_at: UPDATED_AT }, intake: "none" }),
    );
    try {
      renderWithProviders(<ManualGradeDrawer submissionId={UUID_SUBMISSION} onClose={() => {}} />);
      const reason = await screen.findByTestId("manual-reason");
      expect(reason).toBeTruthy();
      await userEvent.clear(screen.getByTestId("manual-score"));
      await userEvent.type(screen.getByTestId("manual-score"), "85");
      expect((screen.getByTestId("manual-save") as HTMLButtonElement).disabled).toBe(true);
      await userEvent.type(reason, "第 5 项命令缺少参数，酌情扣分");
      expect((screen.getByTestId("manual-save") as HTMLButtonElement).disabled).toBe(false);
    } finally {
      stub.restore();
    }
  });

  it("保存时回传 GET 得到的 updated_at 作为乐观锁", async () => {
    const stub = installFetchStub(
      routes({
        grade: { score: 70, updated_at: UPDATED_AT },
        intake: "none",
      }),
    );
    try {
      renderWithProviders(<ManualGradeDrawer submissionId={UUID_SUBMISSION} onClose={() => {}} />);
      await screen.findByTestId("manual-score");
      // 重新查询：数据到达后组件重渲染，之前拿到的节点可能已脱离文档。
      fireEvent.change(screen.getByTestId("manual-score"), { target: { value: "92" } });
      await userEvent.type(screen.getByTestId("manual-reason"), "补交了第 3 项");
      expect((screen.getByTestId("manual-score") as HTMLInputElement).value).toBe("92");
      await waitFor(() =>
        expect((screen.getByTestId("manual-save") as HTMLButtonElement).disabled).toBe(false),
      );
      await userEvent.click(screen.getByTestId("manual-save"));
      await waitFor(() =>
        expect(stub.requests.map((r) => r.method)).toContain("POST"),
      );
      const posted = stub.requests.find((r) => r.method === "POST");
      const body = (posted?.body ?? {}) as {
        expected_updated_at?: string;
        score?: number;
      };
      expect(body.expected_updated_at).toBe(UPDATED_AT);
      expect(body.score).toBe(92);
    } finally {
      stub.restore();
    }
  });
});
