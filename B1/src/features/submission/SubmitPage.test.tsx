import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, fireEvent } from "@testing-library/react";
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

/** 编辑器加载器桩：让页面在 jsdom 下走"降级为纯文本输入框"的真实分支。 */
const failingLoader = async (): Promise<never> => {
  throw new Error("monaco unavailable in jsdom");
};

/**
 * 真实形状的访问令牌：`sub` 是 UUID。用不透明字符串（如 `opaque-token-without-claims`）
 * 会让"从令牌的 sub 取身份"这条路径在测试里永远走不到——而它在真实使用中是主路径
 * （从未提交过的学生没有成绩行可回退）。
 */
function jwtFor(subject: string): string {
  const encode = (value: object) =>
    btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${encode({ alg: "EdDSA" })}.${encode({ sub: subject })}.signature`;
}

function studentRoutes(events: StubbedRoute["respond"]): StubbedRoute[] {
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
      respond: () =>
        jsonResponse(questionVersionPublic({ max_files: 2, max_total_bytes: 4096 })),
    },
    {
      method: "POST",
      path: "/api/v1/submissions",
      respond: () => jsonResponse(submissionAccepted(), 202),
    },
    {
      path: `/api/v1/evaluation-runs/${UUID_RUN}/events`,
      respond: events,
    },
  ];
}

async function renderSubmitPage(user: ReturnType<typeof userEvent.setup>) {
  const view = renderWithProviders(
    <SubmitPage questionVersionId={VERSION_ID} editorLoader={failingLoader} />,
  );
  const editor = await screen.findByTestId("code-editor-fallback");
  await waitFor(() => expect(screen.getByTestId("active-file-path")).toHaveValue("solution.py"));
  await user.type(editor, "print(1)");
  return view;
}

let originalFetch: typeof globalThis.fetch;

beforeEach(() => {
  originalFetch = globalThis.fetch;
  localStorage.clear();
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("作答与提交（RQ-B-1-50）", () => {
  it("提交前校验拦截非法路径，且不发任何提交请求", async () => {
    const stub = installFetchStub(studentRoutes(() => sseResponse([])));
    const user = userEvent.setup();
    await signIn("student1", "pw");
    await renderSubmitPage(user);

    const pathInput = screen.getByTestId("active-file-path");
    fireEvent.change(pathInput, { target: { value: "../escape.py" } });
    expect(pathInput).toHaveValue("../escape.py");
    await user.click(screen.getByTestId("submit-answer"));

    expect(await screen.findByTestId("validation-issues")).toHaveTextContent("路径不合法");
    expect(stub.requests.some((request) => request.url.pathname === "/api/v1/submissions")).toBe(
      false,
    );
    stub.restore();
  });

  it("提交成功：信封含摘要与幂等键，随后按 SSE 展示终态，并清除本地草稿", async () => {
    const stub = installFetchStub(
      studentRoutes(() =>
        sseResponse([
          sseFrame(1, streamFrame({ sequence: 1, cases_total: 2, cases_done: 1 })),
          // 重复帧：同一事件 id 与同一 sequence，必须被丢弃
          sseFrame(1, streamFrame({ sequence: 1, cases_total: 2, cases_done: 1 })),
          sseFrame(
            2,
            streamFrame({
              sequence: 2,
              event: "completed",
              state: "completed",
              stage: "terminal",
              cases_done: 2,
              score: 86,
              is_pass: true,
            }),
          ),
        ]),
      ),
    );
    const user = userEvent.setup();
    await signIn("student1", "pw");
    await renderSubmitPage(user);

    // 让草稿先落盘，随后验证提交成功会清掉它
    await new Promise((resolve) => setTimeout(resolve, 900));
    expect(localStorage.getItem(draftKey(STUDENT_ID, VERSION_ID))).not.toBeNull();

    await user.click(screen.getByTestId("submit-answer"));

    expect(await screen.findByTestId("submit-accepted")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("terminal-completed")).toBeInTheDocument());
    expect(screen.getByTestId("terminal-completed")).toHaveTextContent("86");

    const submitted = stub.requests.find(
      (request) => request.method === "POST" && request.url.pathname === "/api/v1/submissions",
    );
    const body = submitted?.body as {
      files: { path: string; content_sha256: string; content_base64: string }[];
      idempotency_key: string;
      question_version_id: string;
      student_id: string;
      course_id: string;
      section_id: string;
    };
    expect(body.question_version_id).toBe(VERSION_ID);
    expect(body.student_id).toBe(STUDENT_ID);
    expect(body.course_id).toBe(UUID_A);
    expect(body.section_id).toBe(UUID_B);
    expect(body.idempotency_key).toBeTruthy();
    expect(submitted?.headers.get("idempotency-key")).toBe(body.idempotency_key);
    expect(body.files[0].path).toBe("solution.py");
    expect(body.files[0].content_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(atob(body.files[0].content_base64)).toContain("print(1)");

    await waitFor(() => expect(localStorage.getItem(draftKey(STUDENT_ID, VERSION_ID))).toBeNull());
    stub.restore();
  });

  it("断线重连：重放的历史帧不会重复展示，进度停在最高 sequence", async () => {
    let connections = 0;
    const stub = installFetchStub(
      studentRoutes(() => {
        connections += 1;
        if (connections === 1) {
          return sseResponse([sseFrame(1, streamFrame({ sequence: 1, cases_done: 1 }))], {
            closeAfter: 1,
          });
        }
        return sseResponse([
          // 重连后服务端从头重放
          sseFrame(1, streamFrame({ sequence: 1, cases_done: 1 })),
          sseFrame(3, streamFrame({ sequence: 3, cases_done: 2 })),
          sseFrame(
            4,
            streamFrame({
              sequence: 4,
              event: "completed",
              state: "completed",
              stage: "terminal",
              score: 70,
              is_pass: true,
            }),
          ),
        ]);
      }),
    );
    const user = userEvent.setup();
    await signIn("student1", "pw");
    await renderSubmitPage(user);
    await user.click(screen.getByTestId("submit-answer"));

    await waitFor(() => expect(screen.getByTestId("terminal-completed")).toBeInTheDocument(), {
      timeout: 5000,
    });
    expect(connections).toBeGreaterThanOrEqual(2);
    // 最终展示的是最高 sequence（重放的历史帧被丢弃）
    expect(screen.getByTestId("progress-panel")).toHaveTextContent("sequence 4");
    stub.restore();
  });

  it("重新打开页面可恢复本地未提交草稿，且草稿不等于已提交", async () => {
    const stub = installFetchStub(studentRoutes(() => sseResponse([])));
    await signIn("student1", "pw");

    localStorage.setItem(
      draftKey(STUDENT_ID, VERSION_ID),
      JSON.stringify({
        ownerId: STUDENT_ID,
        questionVersionId: VERSION_ID,
        language: "python",
        files: [{ path: "draft.py", content: "# 未提交草稿" }],
        savedAt: "2026-09-15T08:00:00.000Z",
      }),
    );

    renderWithProviders(
      <SubmitPage questionVersionId={VERSION_ID} editorLoader={failingLoader} />,
    );

    expect(await screen.findByTestId("draft-restored")).toHaveTextContent("草稿不是已提交");
    expect(screen.getByTestId("active-file-path")).toHaveValue("draft.py");
    // 恢复草稿不会自动提交
    expect(stub.requests.some((request) => request.url.pathname === "/api/v1/submissions")).toBe(
      false,
    );
    stub.restore();
  });

  it("共用机器：不属于当前用户的草稿不会被恢复", async () => {
    const stub = installFetchStub(studentRoutes(() => sseResponse([])));
    await signIn("student1", "pw");

    const otherStudent = "00000000-0000-4000-8000-0000000000d9";
    localStorage.setItem(
      draftKey(otherStudent, VERSION_ID),
      JSON.stringify({
        ownerId: otherStudent,
        questionVersionId: VERSION_ID,
        language: "python",
        files: [{ path: "someone-else.py", content: "# 上一个人的代码" }],
        savedAt: "2026-09-15T08:00:00.000Z",
      }),
    );

    renderWithProviders(
      <SubmitPage questionVersionId={VERSION_ID} editorLoader={failingLoader} />,
    );

    await screen.findByTestId("code-editor-fallback");
    expect(screen.queryByTestId("draft-restored")).toBeNull();
    expect(screen.getByTestId("active-file-path")).toHaveValue("solution.py");
    stub.restore();
  });
});
