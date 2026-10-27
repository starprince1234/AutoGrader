import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../testing/renderWithProviders";
import {
  installFetchStub,
  jsonResponse,
  sseFrame,
  sseResponse,
  type StubbedRoute,
} from "../../testing/httpBoundary";
import {
  UUID_A,
  UUID_B,
  UUID_C,
  UUID_RUN,
  gradePage,
  gradeRow,
  questionPublic,
  questionVersionPublic,
  sessionCreated,
  streamFrame,
  submissionAccepted,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { draftKey } from "./draft";
import { SubmitPage } from "./SubmitPage";

const VERSION_ID = questionVersionPublic().question_version_id;
const STUDENT_ID = UUID_C;
const failingLoader = async (): Promise<never> => {
  throw new Error("monaco unavailable in jsdom");
};

function jwtFor(subject: string): string {
  const encode = (value: object) =>
    btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${encode({ alg: "EdDSA" })}.${encode({ sub: subject })}.signature`;
}

function routes(options: {
  version?: ReturnType<typeof questionVersionPublic>;
  events?: StubbedRoute["respond"];
  accepted?: ReturnType<typeof submissionAccepted>;
} = {}): StubbedRoute[] {
  return [
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () =>
        jsonResponse(
          sessionCreated({
            roles: ["student"],
            scope: [{ course_id: UUID_A, section_id: UUID_B }],
            access_token: jwtFor(STUDENT_ID),
          }),
        ),
    },
    {
      path: "/api/v1/grades",
      respond: () => jsonResponse(gradePage([gradeRow({ student_id: STUDENT_ID })])),
    },
    {
      path: `/api/v1/question-versions/${VERSION_ID}`,
      respond: () => jsonResponse(options.version ?? questionVersionPublic({ max_files: 3 })),
    },
    {
      path: `/api/v1/questions/${UUID_A}`,
      respond: () => jsonResponse(questionPublic()),
    },
    {
      method: "POST",
      path: "/api/v1/submissions",
      respond: () => jsonResponse(options.accepted ?? submissionAccepted(), 202),
    },
    {
      path: `/api/v1/evaluation-runs/${UUID_RUN}/events`,
      respond: options.events ?? (() => sseResponse([])),
    },
  ];
}

let originalFetch: typeof globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  localStorage.clear();
});

describe("P10 读题 → 写代码 → 提交 → 看结果", () => {
  it("题目要求与编辑器同时可见，版本/语言/上限在顶部说明", async () => {
    const stub = installFetchStub(routes());
    originalFetch = globalThis.fetch;
    await signIn("student1", "pw");
    renderWithProviders(<SubmitPage questionVersionId={VERSION_ID} editorLoader={failingLoader} />);

    expect(await screen.findByTestId("submit-stem")).toHaveTextContent("统计文件行数");
    expect(screen.getByTestId("stem-markdown")).toBeInTheDocument();
    expect(screen.getByTestId("submit-editor")).toBeInTheDocument();
    const meta = screen.getByTestId("submit-meta");
    expect(meta).toHaveTextContent("版本 3");
    expect(meta).toHaveTextContent("Bash");
    // 不再暴露 base64 字符量等实现细节：只说明学生能理解的字节上限。
    expect(meta.textContent).not.toContain("base64");
    stub.restore();
  });

  it("草稿恢复先询问；选择不恢复会清除本机草稿且保持空编辑器", async () => {
    const stub = installFetchStub(routes());
    originalFetch = globalThis.fetch;
    await signIn("student1", "pw");
    localStorage.setItem(
      draftKey(STUDENT_ID, VERSION_ID),
      JSON.stringify({
        ownerId: STUDENT_ID,
        questionVersionId: VERSION_ID,
        language: "bash",
        files: [{ path: "old.py", content: "# 旧草稿" }],
        savedAt: "2026-09-15T08:00:00.000Z",
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<SubmitPage questionVersionId={VERSION_ID} editorLoader={failingLoader} />);

    expect(await screen.findByTestId("restore-draft")).toHaveTextContent("恢复上次未提交代码？");
    await user.click(screen.getByTestId("restore-draft-decline"));

    await waitFor(() =>
      expect(localStorage.getItem(draftKey(STUDENT_ID, VERSION_ID))).toBeNull(),
    );
    expect(screen.getByTestId("active-file-path")).toHaveValue("solution.py");
    expect(screen.queryByTestId("draft-restored")).toBeNull();
    stub.restore();
  });

  it("删除文件需要确认，确认前保留文件与内容", async () => {
    const stub = installFetchStub(routes());
    originalFetch = globalThis.fetch;
    await signIn("student1", "pw");
    const user = userEvent.setup();
    renderWithProviders(<SubmitPage questionVersionId={VERSION_ID} editorLoader={failingLoader} />);
    await screen.findByTestId("code-editor-fallback");

    await user.click(screen.getByTestId("add-file"));
    expect(screen.getAllByRole("tab")).toHaveLength(2);
    await user.click(screen.getByTestId("remove-file"));

    const dialog = await screen.findByTestId("remove-file-confirm");
    expect(dialog).toHaveTextContent("移除");
    expect(within(dialog).getByText(/未提交的内容会一并移除/)).toBeInTheDocument();
    // 取消：文件仍在。
    await user.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(screen.getAllByRole("tab")).toHaveLength(2);

    await user.click(screen.getByTestId("remove-file"));
    await user.click(screen.getByTestId("remove-file-confirm-button"));
    await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(1));
    stub.restore();
  });

  it("completed 终态提供“查看本次结果”，并把真实 evaluation_run_id 交给调用方", async () => {
    const onOpenResult = vi.fn();
    const stub = installFetchStub(
      routes({
        events: () =>
          sseResponse([
            sseFrame(
              1,
              streamFrame({
                sequence: 1,
                event: "completed",
                state: "completed",
                stage: "terminal",
                score: 86,
                is_pass: true,
              }),
            ),
          ]),
      }),
    );
    originalFetch = globalThis.fetch;
    await signIn("student1", "pw");
    const user = userEvent.setup();
    renderWithProviders(
      <SubmitPage
        questionVersionId={VERSION_ID}
        editorLoader={failingLoader}
        onOpenResult={onOpenResult}
      />,
    );
    await screen.findByTestId("code-editor-fallback");
    await user.type(screen.getByTestId("code-editor-fallback"), "echo hi");
    await user.click(screen.getByTestId("submit-answer"));

    await waitFor(() => expect(screen.getByTestId("terminal-completed")).toBeInTheDocument());
    await user.click(await screen.findByTestId("open-result"));
    expect(onOpenResult).toHaveBeenCalledWith(UUID_RUN);
    stub.restore();
  });

  it("draft 版本不能作答：不显示编辑器与提交入口", async () => {
    const stub = installFetchStub(
      routes({ version: questionVersionPublic({ status: "draft" }) }),
    );
    originalFetch = globalThis.fetch;
    await signIn("student1", "pw");
    renderWithProviders(<SubmitPage questionVersionId={VERSION_ID} editorLoader={failingLoader} />);

    expect(await screen.findByTestId("version-not-published")).toHaveTextContent("尚未发布");
    expect(screen.queryByTestId("submit-editor")).toBeNull();
    expect(screen.queryByTestId("submit-answer")).toBeNull();
    stub.restore();
  });
});
