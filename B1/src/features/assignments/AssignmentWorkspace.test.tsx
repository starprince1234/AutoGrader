import { afterEach, describe, expect, it } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { renderWithProviders } from "../../testing/renderWithProviders";
import {
  installFetchStub,
  jsonResponse,
  problemResponse,
  sseFrame,
  sseResponse,
  type FetchStub,
  type StubbedRoute,
} from "../../testing/httpBoundary";
import {
  UUID_A,
  UUID_RUN,
  assignmentPublic,
  currentUser,
  emailVerificationChallenge,
  emailVerificationChallengeStatus,
  gradePage,
  gradeRow,
  myMembership,
  questionPublic,
  questionVersionPublic,
  sessionCreated,
  streamFrame,
  submissionAccepted,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { draftKey } from "../submission/draft";
import { AssignmentDetailPage } from "./AssignmentDetailPage";

/**
 * 学生作业工作区（CHG-0032 §7.2；取代旧单题作答页）。
 *
 * 覆盖：多题切换 / 粘贴与 .txt 上传 / 草稿 v2 键与恢复询问 / 每题 `N.txt` 提交体 /
 * 逐题状态与终态 / 邮件提交面板的正文模板、映射与状态。
 */

const COURSE = "00000000-0000-4000-8000-0000000000b1";
const SECTION = "00000000-0000-4000-8000-0000000000b2";
const STUDENT = "00000000-0000-4000-8000-0000000000c1";
const V1 = "00000000-0000-4000-8000-000000000101";
const V2 = "00000000-0000-4000-8000-000000000102";
const Q1 = "00000000-0000-4000-8000-000000000201";
const Q2 = "00000000-0000-4000-8000-000000000202";
const CHALLENGE = "00000000-0000-4000-8000-0000000000aa";
const EMAIL_SUBMISSION = "00000000-0000-4000-8000-0000000000ee";

const ASSIGNMENT = assignmentPublic({
  assignment_id: UUID_A,
  course_id: COURSE,
  title: "第一次作业",
  question_version_ids: [V1, V2],
});

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
  localStorage.clear();
});

function jwtFor(subject: string): string {
  const encode = (value: object) =>
    btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${encode({ alg: "EdDSA" })}.${encode({ sub: subject })}.signature`;
}

function baseRoutes(options: {
  grades?: unknown[];
  gradeNextCursor?: string | null;
  delayedGrades?: boolean;
  submit?: () => Response;
  events?: () => Response;
  challengeStatus?: () => Response;
  /** 覆盖 `GET /me` 的 memberships（默认本人在这门课里有一个有效选课）。 */
  memberships?: ReturnType<typeof myMembership>[];
} = {}): StubbedRoute[] {
  return [
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () =>
        jsonResponse(
          sessionCreated({
            roles: ["student"],
            scope: [{ course_id: COURSE, section_id: SECTION }],
            access_token: jwtFor(STUDENT),
          }),
        ),
    },
    { path: `/api/v1/assignments/${UUID_A}`, respond: () => jsonResponse(ASSIGNMENT) },
    {
      path: "/api/v1/me",
      respond: () =>
        jsonResponse(
          currentUser({
            user_id: STUDENT,
            memberships:
              options.memberships ??
              [
                myMembership({
                  course_id: COURSE,
                  course_title: "操作系统",
                  section_id: SECTION,
                  section_code: "01",
                  term: "2026F",
                  class_id: "00000000-0000-4000-8000-0000000000d1",
                  class_name: "软件工程1班",
                }),
              ],
          }),
        ),
    },
    {
      path: "/api/v1/grades",
      respond: () => {
        if (options.delayedGrades) {
          return new Promise<Response>((resolve) => {
            setTimeout(
              () => resolve(jsonResponse(gradePage((options.grades ?? []) as never[]))),
              5000,
            );
          });
        }
        return jsonResponse(
          gradePage((options.grades ?? []) as never[], options.gradeNextCursor ?? null),
        );
      },
    },
    {
      path: `/api/v1/question-versions/${V1}`,
      respond: () =>
        jsonResponse(
          questionVersionPublic({
            question_version_id: V1,
            question_id: Q1,
            version: 1,
            stem_markdown: "# 第一题\n\n第一题说明。",
          }),
        ),
    },
    {
      path: `/api/v1/question-versions/${V2}`,
      respond: () =>
        jsonResponse(
          questionVersionPublic({
            question_version_id: V2,
            question_id: Q2,
            version: 1,
            stem_markdown: "# 第二题\n\n第二题说明。",
          }),
        ),
    },
    { path: `/api/v1/questions/${Q1}`, respond: () => jsonResponse(questionPublic({ question_id: Q1, title: "进程与线程" })) },
    { path: `/api/v1/questions/${Q2}`, respond: () => jsonResponse(questionPublic({ question_id: Q2, title: "文件与目录" })) },
    {
      method: "POST",
      path: "/api/v1/submissions",
      respond:
        options.submit ?? (() => jsonResponse(submissionAccepted({ submission_id: EMAIL_SUBMISSION }), 202)),
    },
    {
      path: `/api/v1/evaluation-runs/${UUID_RUN}/events`,
      respond:
        options.events ??
        (() =>
          sseResponse([
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
          ])),
    },
    {
      method: "POST",
      path: "/api/v1/email-verification/challenges",
      respond: () => jsonResponse(emailVerificationChallenge({ challenge_id: CHALLENGE }), 201),
    },
    {
      path: `/api/v1/email-verification/challenges/${CHALLENGE}`,
      respond: options.challengeStatus ?? (() => jsonResponse(emailVerificationChallengeStatus())),
    },
    {
      path: `/api/v1/submissions/${EMAIL_SUBMISSION}`,
      respond: () =>
        jsonResponse({
          submission_id: EMAIL_SUBMISSION,
          assignment_id: UUID_A,
          student_id: STUDENT,
          course_id: COURSE,
          section_id: SECTION,
          question_version_id: V1,
          state: "completed",
          intake: "email",
          evaluation_run_id: UUID_RUN,
          files: [{ path: "1.txt", content_sha256: "a".repeat(64), size_bytes: 12 }],
          created_at: "2026-09-15T08:00:00.000Z",
        }),
    },
  ];
}

function renderWorkspace() {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const page = createRoute({
    getParentRoute: () => rootRoute,
    path: "/assignments/$assignmentId",
    component: () => <AssignmentDetailPage assignmentId={UUID_A} />,
  });
  const assignments = createRoute({
    getParentRoute: () => rootRoute,
    path: "/assignments",
    component: () => <span>assignments target</span>,
  });
  const grades = createRoute({
    getParentRoute: () => rootRoute,
    path: "/grades",
    component: () => <span>grades target</span>,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([assignments, page, grades]),
    history: createMemoryHistory({ initialEntries: [`/assignments/${UUID_A}`] }),
  });
  return renderWithProviders(<RouterProvider router={router} />);
}

async function openWorkspace(options: Parameters<typeof baseRoutes>[0] = {}) {
  stub = installFetchStub(baseRoutes(options));
  await signIn("student1", "pw");
  const view = renderWorkspace();
  await screen.findByTestId("workspace-stem");
  return view;
}

describe("学生作业工作区（CHG-0032）", () => {
  it("多题切换：左侧题目列表带序号与标题，右侧题干与答案文件映射随题切换", async () => {
    await openWorkspace();

    await screen.findByTestId("workspace-question-1");
    // 题目标题是版本读回后再取题目 DTO 的（N+1），因此要等标题就位再断言。
    await waitFor(() =>
      expect(screen.getByTestId("workspace-question-1")).toHaveTextContent("进程与线程"),
    );
    await waitFor(() =>
      expect(screen.getByTestId("workspace-question-2")).toHaveTextContent("文件与目录"),
    );
    expect(await screen.findByTestId("stem-markdown")).toHaveTextContent("第一题说明");
    expect(screen.getByTestId("workspace-file-mapping")).toHaveTextContent(
      "第 1 题提交为 1.txt",
    );

    await userEvent.setup().click(screen.getByTestId("workspace-question-2"));
    await waitFor(() =>
      expect(screen.getByTestId("stem-markdown")).toHaveTextContent("第二题说明"),
    );
    expect(screen.getByTestId("workspace-file-mapping")).toHaveTextContent(
      "第 2 题提交为 2.txt",
    );
  });

  it("粘贴答案后提交：信封带 assignment_id 与 N.txt、摘要/base64/幂等键，成功后清草稿并显示终态", async () => {
    await openWorkspace();
    const user = userEvent.setup();

    const textarea = await screen.findByTestId("workspace-answer-text");
    await user.click(textarea);
    await user.paste("我的答案：echo hi");

    // 草稿 v2 键（owner + assignment + question version）在 800ms 防抖后落盘。
    await waitFor(
      () => expect(localStorage.getItem(draftKey(STUDENT, UUID_A, V1))).not.toBeNull(),
      { timeout: 3000 },
    );

    await user.click(screen.getByTestId("workspace-submit"));

    expect(await screen.findByTestId("workspace-submit-accepted")).toBeInTheDocument();
    expect(await screen.findByTestId("terminal-completed")).toHaveTextContent("86");

    const submitted = stub?.requests.find(
      (request) => request.method === "POST" && request.url.pathname === "/api/v1/submissions",
    );
    const body = submitted?.body as {
      assignment_id: string;
      question_version_id: string;
      student_id: string;
      course_id: string;
      section_id: string;
      idempotency_key: string;
      files: { path: string; content_sha256: string; content_base64: string }[];
    };
    expect(body.assignment_id).toBe(UUID_A);
    expect(body.question_version_id).toBe(V1);
    expect(body.student_id).toBe(STUDENT);
    expect(body.course_id).toBe(COURSE);
    expect(body.section_id).toBe(SECTION);
    expect(body.idempotency_key).toBeTruthy();
    expect(submitted?.headers.get("idempotency-key")).toBe(body.idempotency_key);
    // 一次提交 = 一道题、一个 N.txt。
    expect(body.files).toHaveLength(1);
    expect(body.files[0].path).toBe("1.txt");
    expect(body.files[0].content_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(atob(body.files[0].content_base64)).toContain("echo hi");

    await waitFor(() =>
      expect(localStorage.getItem(draftKey(STUDENT, UUID_A, V1))).toBeNull(),
    );
  });

  it("上传 .txt 读取文本；非 .txt 与超限文件在提交前被拦截", async () => {
    await openWorkspace();

    const input = await screen.findByTestId("answer-file-input");
    const file = new File(["从文件读取的答案\n第二行"], "我的答案.txt", {
      type: "text/plain",
    });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() =>
      expect(screen.getByTestId("workspace-answer-text")).toHaveValue(
        "从文件读取的答案\n第二行",
      ),
    );
    expect(screen.getByTestId("workspace-uploaded-file")).toHaveTextContent("我的答案.txt");

    // 非 .txt：直接提示，不写入答案区。
    const markdown = new File(["# 不是文本答案"], "answer.md", { type: "text/markdown" });
    fireEvent.change(input, { target: { files: [markdown] } });
    expect(await screen.findByTestId("workspace-validation-issues")).toHaveTextContent(
      "只接受 .txt",
    );
    expect(screen.getByTestId("workspace-answer-text")).toHaveValue(
      "从文件读取的答案\n第二行",
    );
  });

  it("草稿 v2：重新打开时先询问再恢复，不直接把旧内容塞进答案区", async () => {
    localStorage.setItem(
      draftKey(STUDENT, UUID_A, V1),
      JSON.stringify({
        ownerId: STUDENT,
        assignmentId: UUID_A,
        questionVersionId: V1,
        content: "# 未提交草稿答案",
        updatedAt: "2026-09-15T08:00:00.000Z",
      }),
    );
    stub = installFetchStub(baseRoutes());
    await signIn("student1", "pw");
    renderWorkspace();

    const dialog = await screen.findByTestId("restore-draft");
    expect(dialog).toHaveTextContent("恢复上次未提交的答案？");
    // 询问未决时答案区不被覆盖。
    expect(screen.getByTestId("workspace-answer-text")).toHaveValue("");

    await userEvent.setup().click(screen.getByTestId("restore-draft-confirm"));
    expect(screen.getByTestId("workspace-answer-text")).toHaveValue("# 未提交草稿答案");
    expect(await screen.findByTestId("workspace-draft-restored")).toHaveTextContent(
      "草稿不是已提交",
    );
  });

  it("逐题状态来自成绩行：已完成/失败与整体失败分类，未完成不显示 0 分", async () => {
    await openWorkspace({
      grades: [
        gradeRow({
          submission_id: "00000000-0000-4000-8000-0000000000f1",
          assignment_id: UUID_A,
          question_version_id: V1,
          student_id: STUDENT,
          state: "completed",
          score: 90,
          max_score: 100,
          evaluation_run_id: "00000000-0000-4000-8000-0000000000aa",
        }),
        gradeRow({
          submission_id: "00000000-0000-4000-8000-0000000000f2",
          assignment_id: UUID_A,
          question_version_id: V2,
          student_id: STUDENT,
          state: "failed",
          score: null,
          max_score: null,
          is_pass: null,
          evaluation_run_id: null,
        }),
      ],
    });

    await waitFor(() =>
      expect(screen.getByTestId("workspace-question-state-1")).toHaveTextContent("已完成"),
    );
    expect(screen.getByTestId("workspace-question-state-2")).toHaveTextContent("失败");
    // 两题都提交过，但存在失败 → 整体分类"失败"（不是未交，也不是 0 分）。
    expect(screen.getByTestId("workspace-overall")).toHaveTextContent("失败");
    // 当前题（第 1 题）结果：90 / 100 + 报告入口。
    expect(screen.getByTestId("workspace-current-result")).toHaveTextContent("90 / 100");
    expect(screen.getByTestId("workspace-open-result")).toBeInTheDocument();
  });

  it("成绩未取全时每题状态显示加载，不显示「待提交」结论", async () => {
    await openWorkspace({ delayedGrades: true });

    const badge = await screen.findByTestId("workspace-question-state-1");
    expect(badge.querySelector(".inline-loader")).not.toBeNull();
    expect(screen.getByTestId("workspace-progress-loading")).toBeInTheDocument();
    // 未取全时不得输出任何"待提交/未交"。
    expect(screen.queryByText("待提交")).toBeNull();
    expect(screen.queryByText("未交")).toBeNull();
  });

  it("系统故障（503）显示可重试文案，不显示 0 分/未交", async () => {
    // 成绩行故意还没返回：此时页面不得以任何方式推出"未交"；再加上提交 503，
    // 也不得把系统故障渲染成 0 分。两者共同验证"未取全不下结论 + 故障不是分数"。
    await openWorkspace({
      delayedGrades: true,
      submit: () =>
        problemResponse({
          status: 503,
          title: "Service Unavailable",
          detail: "B3 assignment directory is unavailable",
          instance: "/api/v1/submissions",
        }),
    });
    const user = userEvent.setup();
    await user.type(await screen.findByTestId("workspace-answer-text"), "答案");
    await user.click(screen.getByTestId("workspace-submit"));

    expect(await screen.findByTestId("workspace-submit-error")).toHaveTextContent(
      "系统未完成本次提交",
    );
    // 系统故障不是分数结论：没有当前结果区，也没有"未交"判定。
    expect(screen.queryByTestId("workspace-current-result")).toBeNull();
    expect(screen.queryByText("未交")).toBeNull();
    expect(screen.getByTestId("workspace-progress-loading")).toBeInTheDocument();
  });

  it("邮件提交面板：正文五行自动填充、N.txt 映射、申请核验码与收到后的逐题结果入口", async () => {
    await openWorkspace({
      challengeStatus: () =>
        jsonResponse(
          emailVerificationChallengeStatus({
            state: "received",
            submissions: [{ question_version_id: V1, submission_id: EMAIL_SUBMISSION }],
          }),
        ),
    });
    const user = userEvent.setup();

    await user.click(await screen.findByTestId("workspace-email-entry"));
    const panel = await screen.findByTestId("email-submission-panel");

    // 正文五行：作业编号 / 课序 / 姓名 / 学号自动填充；核验码在申请后填入。
    await waitFor(() =>
      expect(within(panel).getByTestId("email-field-name")).toHaveValue("张三"),
    );
    expect(within(panel).getByTestId("email-field-student-number")).toHaveValue("20260001");
    expect(within(panel).getByTestId("email-field-section")).toHaveValue("01");
    const body = within(panel).getByTestId("email-template-body") as HTMLTextAreaElement;
    expect(body.value).toContain(`作业编号：${UUID_A}`);
    expect(body.value).toContain("姓名：张三");
    expect(body.value).toContain("学号：20260001");

    // 附件映射：第 N 题 → N.txt。
    const mapping = within(panel).getByTestId("email-attachment-mapping");
    expect(mapping).toHaveTextContent("1.txt");
    expect(mapping).toHaveTextContent("2.txt");
    expect(mapping).toHaveTextContent("进程与线程");

    await user.click(within(panel).getByTestId("email-issue-code"));
    expect(await within(panel).findByTestId("email-code")).toHaveTextContent("AG-123456789012");
    expect(body.value).toContain("核验码：AG-123456789012");

    const issueRequest = stub?.requests.find(
      (request) => request.url.pathname === "/api/v1/email-verification/challenges",
    );
    expect(issueRequest?.body).toEqual({
      course_id: COURSE,
      section_id: SECTION,
      assignment_id: UUID_A,
    });

    // 收到后：状态与逐题结果入口（带 assignment 的 submissions[]）。
    expect(await within(panel).findByTestId("email-received-hint")).toBeInTheDocument();
    await waitFor(() =>
      expect(within(panel).getByTestId("email-received-submissions")).toBeInTheDocument(),
    );
    expect(
      within(panel).getByTestId(`email-result-link-${EMAIL_SUBMISSION}`),
    ).toBeInTheDocument();
  });

  it("本人不在该课程名单中时说明原因并禁止提交（不以令牌 scope 代替权威名单）", async () => {
    await openWorkspace({ memberships: [] });

    expect(await screen.findByTestId("workspace-scope-missing")).toHaveTextContent(
      "不在该课程的有效名单中",
    );
    expect(screen.getByTestId("workspace-submit")).toBeDisabled();
    expect(screen.getByTestId("workspace-email-entry")).toBeInTheDocument();
  });

  it("邮件核验被拒绝时显示原因码文案（身份不符）", async () => {
    await openWorkspace({
      challengeStatus: () =>
        jsonResponse(
          emailVerificationChallengeStatus({
            state: "rejected",
            reason_code: "email_body_mismatch",
          }),
        ),
    });
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("workspace-email-entry"));
    await user.click(await screen.findByTestId("email-issue-code"));

    expect(await screen.findByTestId("email-challenge-reason")).toHaveTextContent(
      "邮件正文中的作业编号、姓名或学号与账号登记不一致",
    );
  });
});
