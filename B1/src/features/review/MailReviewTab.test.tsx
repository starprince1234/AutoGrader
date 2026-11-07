import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../testing/renderWithProviders";
import {
  installFetchStub,
  jsonResponse,
  problemResponse,
  type FetchStub,
  type StubbedRoute,
} from "../../testing/httpBoundary";
import {
  UUID_A,
  UUID_B,
  UUID_C,
  UUID_D,
  coursePage,
  courseSummary,
  assignmentPage,
  assignmentPublic,
  enrollmentPage,
  enrollmentPublic,
  intakeQuarantinePage,
  intakeQuarantineView,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { resetLoggerForTests } from "../../logging/logger";
import { MailReviewTab } from "./MailReviewTab";
import type { ReviewSearch } from "./search";

/**
 * P13-A 邮件提交标签。
 *
 * 断言的重点是**授权与数据边界**而不是排版：
 * - 列表请求显式带 `resolved`（未处理 = false），并带上 URL 的课程/课序筛选；
 * - 403 显示"没有权限"而不是空列表；未关联行只在管理员视图出现；
 * - 只有未处理且可归属的原因码有归属入口；确认前不发 POST、POST 体只有 `student_id/note`；
 * - 候选学生来自 B4 名单，空名单时禁用确认。
 */

let stub: FetchStub | null = null;

beforeEach(() => {
  resetLoggerForTests();
});

afterEach(() => {
  stub?.restore();
  stub = null;
  vi.restoreAllMocks();
});

const SESSION = "/api/v1/auth/session";
const COURSES = "/api/v1/courses";
const SECTIONS = `/api/v1/courses/${UUID_A}/sections`;
const QUARANTINES = "/api/v1/intake/quarantines";
const ASSIGNMENTS = "/api/v1/assignments";
const ENROLLMENTS = `/api/v1/sections/${UUID_B}/enrollments`;

function routesFor(options: {
  rows?: ReturnType<typeof intakeQuarantineView>[];
  roles?: ("teacher" | "admin")[];
  scope?: { course_id: string; section_id: string }[];
  quarantineRespond?: () => Response;
  enrollments?: ReturnType<typeof enrollmentPublic>[];
  resolveRespond?: () => Response;
  dismissRespond?: () => Response;
  courses?: ReturnType<typeof courseSummary>[];
}): StubbedRoute[] {
  const rows = options.rows ?? [];
  return [
    {
      method: "POST",
      path: SESSION,
      respond: () =>
        jsonResponse(
          sessionCreated({
            roles: options.roles ?? ["teacher"],
            scope: options.scope ?? [{ course_id: UUID_A, section_id: UUID_B }],
          }),
        ),
    },
    {
      method: "GET",
      path: COURSES,
      respond: () => jsonResponse(coursePage(options.courses ?? [courseSummary()])),
    },
    {
      method: "GET",
      path: SECTIONS,
      respond: () =>
        jsonResponse({
          items: [{ section_id: UUID_B, section_code: "1班" }],
          next_cursor: null,
        }),
    },
    {
      method: "GET",
      path: QUARANTINES,
      respond: () =>
        options.quarantineRespond?.() ?? jsonResponse(intakeQuarantinePage(rows)),
    },
    {
      method: "GET",
      path: ENROLLMENTS,
      respond: () => jsonResponse(enrollmentPage(options.enrollments ?? [])),
    },
    {
      method: "GET",
      path: ASSIGNMENTS,
      respond: () =>
        jsonResponse(
          assignmentPage([
            assignmentPublic({
              course_id: UUID_A,
              section_id: UUID_B,
              question_version_ids: [UUID_C],
            }),
          ]),
        ),
    },
    {
      method: "POST",
      path: `/api/v1/intake/quarantines/${UUID_C}/complete`,
      respond: () =>
        options.resolveRespond?.() ??
        jsonResponse(
          intakeQuarantineView({
            intake_quarantine_id: UUID_C,
            resolved: true,
            resolution: "completed",
            resolved_student_id: UUID_D,
            resolved_submission_id: UUID_A,
            resolved_at: "2026-09-15T09:00:00.000Z",
            resolved_by: UUID_D,
          }),
        ),
    },
    {
      method: "POST",
      path: `/api/v1/intake/quarantines/${UUID_C}/hold`,
      respond: () =>
        jsonResponse(
          intakeQuarantineView({
            intake_quarantine_id: UUID_C,
            held: false,
            held_at: null,
            held_by: null,
          }),
        ),
    },
    {
      method: "POST",
      path: `/api/v1/intake/quarantines/${UUID_C}/dismiss`,
      respond: () =>
        options.dismissRespond?.() ??
        jsonResponse(
          intakeQuarantineView({
            intake_quarantine_id: UUID_C,
            resolved: true,
            resolution: "dismissed",
            resolved_at: "2026-09-15T09:00:00.000Z",
            resolved_by: UUID_D,
          }),
        ),
    },
  ];
}

function Harness({ initial }: { initial: ReviewSearch }) {
  const [search, setSearch] = useState(initial);
  return <MailReviewTab search={search} onChange={setSearch} />;
}

const MAIL_PATH: ReviewSearch = { tab: "mail", course_id: UUID_A, section_id: UUID_B };

/** 等待候选名单真的加载出该学生（否则 selectOptions 会拿到空 select）。 */
async function waitForCandidate(studentNumber: string): Promise<void> {
  await waitFor(() => {
    const select = screen.getByTestId("review-resolve-student");
    const options = Array.from(select.querySelectorAll("option"));
    expect(options.some((option) => option.textContent?.includes(studentNumber))).toBe(true);
  });
}

/** 匹配用户必须先选作业；选中后版本自动取第一个，名单按作业课序加载。 */
async function chooseAssignment(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  const select = await screen.findByTestId("review-resolve-assignment");
  await waitFor(() => {
    expect(select.querySelectorAll("option").length).toBeGreaterThan(1);
  });
  await user.selectOptions(select, UUID_A);
}

describe("P13-A 邮件提交标签", () => {
  it("列表请求显式发送 resolved=false 与课程/课序筛选，并渲染真实记录", async () => {
    const row = intakeQuarantineView({ intake_quarantine_id: UUID_C });
    stub = installFetchStub(routesFor({ rows: [row] }));
    await signIn("teacher1", "pw");
    renderWithProviders(<Harness initial={MAIL_PATH} />);

    expect(await screen.findByTestId("review-mail-table")).toBeInTheDocument();
    const request = stub!.requests.find(
      (item) => item.method === "GET" && item.url.pathname === QUARANTINES,
    );
    expect(request).toBeDefined();
    expect(request?.url.searchParams.get("resolved")).toBe("false");
    expect(request?.url.searchParams.get("course_id")).toBe(UUID_A);
    expect(request?.url.searchParams.get("section_id")).toBe(UUID_B);
    expect(request?.url.searchParams.get("limit")).toBe("50");
    // 页面只渲染契约字段：scope 来自记录本身，而不是 URL 推断。
    expect(within(screen.getByTestId("review-mail-table")).getByText(/未唯一匹配/)).toBeInTheDocument();
  });

  it("403 显示权限说明而不是空列表", async () => {
    stub = installFetchStub(
      routesFor({
        quarantineRespond: () =>
          problemResponse({
            status: 403,
            title: "Forbidden",
            detail: "course scope does not cover this quarantined record",
          }),
      }),
    );
    await signIn("teacher1", "pw");
    renderWithProviders(<Harness initial={MAIL_PATH} />);

    expect(await screen.findByTestId("review-mail-forbidden")).toHaveTextContent(
      "你没有权限查看这个课程的邮件记录。",
    );
    expect(screen.queryByTestId("review-mail-empty")).toBeNull();
  });

  it("未处理为空时给出明确的空状态（不是错误）", async () => {
    stub = installFetchStub(routesFor({ rows: [] }));
    await signIn("teacher1", "pw");
    renderWithProviders(<Harness initial={MAIL_PATH} />);

    expect(await screen.findByTestId("review-mail-empty")).toHaveTextContent(
      "没有待处理记录。",
    );
    expect(screen.getByText(/无法确认课程的邮件只有管理员能看到/)).toBeInTheDocument();
  });

  it("详情只显示老师需要的信息，不堆 Message-ID/UID 等接口字段", async () => {
    const user = userEvent.setup();
    const row = intakeQuarantineView({ intake_quarantine_id: UUID_C });
    stub = installFetchStub(routesFor({ rows: [row] }));
    await signIn("teacher1", "pw");
    renderWithProviders(<Harness initial={MAIL_PATH} />);

    await user.click(await screen.findByTestId(`review-mail-detail-${UUID_C}`));
    const drawer = await screen.findByTestId("review-mail-detail-drawer");
    expect(within(drawer).getByText(/submissions@example\.edu/)).toBeInTheDocument();
    expect(within(drawer).getByText(/发件邮箱对应不止一个学生/)).toBeInTheDocument();
    expect(within(drawer).queryByText(/Message-ID/)).toBeNull();
    expect(within(drawer).queryByText(/UID/)).toBeNull();
    const before = stub!.requests.length;
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(stub!.requests.length).toBe(before);
  });

  it("非身份类原因码只提供暂存/作废，不提供匹配用户", async () => {
    const row = intakeQuarantineView({
      intake_quarantine_id: UUID_C,
      reason_code: "archive_bomb",
    });
    stub = installFetchStub(routesFor({ rows: [row] }));
    await signIn("teacher1", "pw");
    renderWithProviders(<Harness initial={MAIL_PATH} />);

    await screen.findByTestId("review-mail-table");
    // 压缩炸弹在服务端会被同一条附件闸门再次拒绝；给按钮只会让老师点出一个 422。
    expect(screen.queryByTestId(`review-mail-resolve-${UUID_C}`)).toBeNull();
    expect(screen.getByTestId(`review-mail-dismiss-${UUID_C}`)).toBeInTheDocument();
    expect(screen.getByTestId(`review-mail-hold-${UUID_C}`)).toBeInTheDocument();
    expect(screen.getByText("压缩包解压后体积异常，安全策略拒绝。")).toBeInTheDocument();
  });

  it("匹配用户：确认前不 POST，确认后提交课程/作业/版本/学生", async () => {
    const user = userEvent.setup();
    const candidate = enrollmentPublic({ student_id: UUID_D });
    stub = installFetchStub(
      routesFor({ rows: [intakeQuarantineView({ intake_quarantine_id: UUID_C })], enrollments: [candidate] }),
    );
    await signIn("teacher1", "pw");
    renderWithProviders(<Harness initial={MAIL_PATH} />);

    await user.click(await screen.findByTestId(`review-mail-resolve-${UUID_C}`));
    const drawer = await screen.findByTestId("review-mail-resolve-drawer");
    await chooseAssignment(user);
    await waitForCandidate(candidate.student_number);
    await user.selectOptions(
      within(drawer).getByTestId("review-resolve-student"),
      UUID_D,
    );
    await user.click(screen.getByTestId("review-resolve-continue"));

    const confirm = await screen.findByTestId("review-mail-confirm");
    expect(confirm).toHaveTextContent(candidate.student_number);
    expect(confirm).toHaveTextContent(candidate.display_name);
    // 确认框出现时还没有 POST。
    expect(
      stub!.requests.filter((item) => item.method === "POST" && item.url.pathname.includes("complete")),
    ).toHaveLength(0);

    await user.click(screen.getByTestId("review-mail-confirm-submit"));
    await waitFor(() => {
      const posts = stub!.requests.filter(
        (item) => item.method === "POST" && item.url.pathname.endsWith("/complete"),
      );
      expect(posts).toHaveLength(1);
      expect(posts[0].body).toEqual({
        student_id: UUID_D,
        course_id: UUID_A,
        section_id: UUID_B,
        question_version_id: UUID_C,
        note: null,
      });
    });
    expect(await screen.findByTestId("review-mail-resolved-ok")).toHaveTextContent(
      "已匹配用户并生成提交",
    );
  });

  it("409 冲突：保留抽屉并提示刷新，不显示成功", async () => {
    const user = userEvent.setup();
    stub = installFetchStub(
      routesFor({
        rows: [intakeQuarantineView({ intake_quarantine_id: UUID_C })],
        enrollments: [enrollmentPublic({ student_id: UUID_D })],
        resolveRespond: () =>
          problemResponse({
            status: 409,
            title: "Conflict",
            detail: "The quarantined intake record has already been resolved.",
          }),
      }),
    );
    await signIn("teacher1", "pw");
    renderWithProviders(<Harness initial={MAIL_PATH} />);

    await user.click(await screen.findByTestId(`review-mail-resolve-${UUID_C}`));
    await chooseAssignment(user);
    await waitForCandidate(enrollmentPublic().student_number);
    await user.selectOptions(screen.getByTestId("review-resolve-student"), UUID_D);
    await user.click(screen.getByTestId("review-resolve-continue"));
    await user.click(await screen.findByTestId("review-mail-confirm-submit"));

    expect(await screen.findByTestId("review-resolve-conflict")).toHaveTextContent(
      "这条记录已处理，请刷新列表。",
    );
    expect(screen.queryByTestId("review-mail-resolved-ok")).toBeNull();
  });

  it("候选名单为空时禁用确认并说明原因", async () => {
    const user = userEvent.setup();
    stub = installFetchStub(
      routesFor({ rows: [intakeQuarantineView({ intake_quarantine_id: UUID_C })], enrollments: [] }),
    );
    await signIn("teacher1", "pw");
    renderWithProviders(<Harness initial={MAIL_PATH} />);

    await user.click(await screen.findByTestId(`review-mail-resolve-${UUID_C}`));
    await chooseAssignment(user);
    expect(await screen.findByTestId("review-resolve-no-candidates")).toHaveTextContent(
      "当前课序没有可选择的学生。",
    );
    expect(screen.getByTestId("review-resolve-continue")).toBeDisabled();
  });

  it("管理员看到未关联行并标为「未关联」", async () => {
    const unscoped = intakeQuarantineView({
      intake_quarantine_id: UUID_C,
      scope_course_id: null,
      scope_section_id: null,
    });
    stub = installFetchStub(
      routesFor({ rows: [unscoped], roles: ["admin"], scope: [] }),
    );
    await signIn("admin1", "pw");
    renderWithProviders(
      <Harness initial={{ tab: "mail", course_id: undefined, section_id: undefined }} />,
    );

    expect(await screen.findByTestId("review-mail-table")).toHaveTextContent("未关联");
    const request = stub!.requests.find(
      (item) => item.method === "GET" && item.url.pathname === QUARANTINES,
    );
    // 管理员默认"全部课程"：不带课程筛选，未关联行才会出现。
    expect(request?.url.searchParams.get("course_id")).toBeNull();
    expect(request?.url.searchParams.get("resolved")).toBe("false");
  });

  it("未带课程上下文时也直接显示待处理列表", async () => {
    stub = installFetchStub(
      routesFor({ rows: [intakeQuarantineView({ intake_quarantine_id: UUID_C })] }),
    );
    await signIn("teacher1", "pw");
    renderWithProviders(<Harness initial={{ tab: "mail" }} />);

    // 老师从任意入口进来都应看到自己的待处理记录，而不是只有一个查询框。
    expect(await screen.findByTestId("review-mail-table")).toBeInTheDocument();
    expect(screen.getByText(/待处理记录/)).toBeInTheDocument();
    expect(screen.getByTestId(`review-mail-dismiss-${UUID_C}`)).toBeInTheDocument();
  });

  it("课程下拉只展示令牌作用域内的课程", async () => {
    const other = courseSummary({ course_id: UUID_D, course_code: "OTHER-1", title: "别的课" });
    stub = installFetchStub(
      routesFor({
        rows: [],
        courses: [courseSummary(), other],
        scope: [{ course_id: UUID_A, section_id: UUID_B }],
      }),
    );
    await signIn("teacher1", "pw");
    renderWithProviders(<Harness initial={{ tab: "mail" }} />);

    const select = await screen.findByTestId("review-mail-course");
    await waitFor(() => {
      const labels = Array.from(select.querySelectorAll("option")).map(
        (option) => option.textContent ?? "",
      );
      expect(labels.some((label) => label.includes("OS-2026"))).toBe(true);
      expect(labels.some((label) => label.includes("OTHER-1"))).toBe(false);
    });
  });

  it("标记作废：确认前不 POST，确认后体只含 note，成功后提示", async () => {
    const user = userEvent.setup();
    stub = installFetchStub(
      routesFor({ rows: [intakeQuarantineView({ intake_quarantine_id: UUID_C })] }),
    );
    await signIn("teacher1", "pw");
    renderWithProviders(<Harness initial={MAIL_PATH} />);

    await user.click(await screen.findByTestId(`review-mail-dismiss-${UUID_C}`));
    await user.type(screen.getByTestId("review-dismiss-note"), "附件是误报");
    await user.click(screen.getByTestId("review-mail-dismiss-submit"));

    expect(
      stub!.requests.filter(
        (item) => item.method === "POST" && item.url.pathname.includes("/dismiss"),
      ),
    ).toHaveLength(0);
    await user.click(screen.getByTestId("review-mail-dismiss-confirm"));

    await waitFor(() => {
      const posted = stub!.requests.find(
        (item) => item.method === "POST" && item.url.pathname.endsWith("/dismiss"),
      );
      expect(posted).toBeDefined();
      expect(posted!.body).toEqual({ note: "附件是误报" });
    });
    expect(await screen.findByTestId("review-mail-resolved-ok")).toHaveTextContent("邮件已作废");
  });

  it("已处理记录显示结果与处理人/时间，且不再提供匹配/暂存/作废", async () => {
    const row = intakeQuarantineView({
      intake_quarantine_id: UUID_C,
      resolved: true,
      resolution: "dismissed",
      resolved_at: "2026-09-15T09:00:00.000Z",
      resolved_by: UUID_D,
    });
    stub = installFetchStub(routesFor({ rows: [row] }));
    await signIn("teacher1", "pw");
    renderWithProviders(<Harness initial={MAIL_PATH} />);

    const table = await screen.findByTestId("review-mail-table");
    expect(within(table).getByText("已作废")).toBeInTheDocument();
    expect(within(table).getByText(/处理人：/)).toBeInTheDocument();
    expect(within(table).getByText("作业一提交")).toBeInTheDocument();
    expect(within(table).queryByTestId(`review-mail-resolve-${UUID_C}`)).toBeNull();
    expect(within(table).queryByTestId(`review-mail-hold-${UUID_C}`)).toBeNull();
    expect(within(table).queryByTestId(`review-mail-dismiss-${UUID_C}`)).toBeNull();
  });

  it("暂存行显示「暂存」且可恢复，仍保留匹配/作废入口", async () => {
    const user = userEvent.setup();
    const row = intakeQuarantineView({
      intake_quarantine_id: UUID_C,
      held: true,
      held_at: "2026-09-15T09:00:00.000Z",
      held_by: UUID_D,
    });
    stub = installFetchStub(routesFor({ rows: [row] }));
    await signIn("teacher1", "pw");
    renderWithProviders(<Harness initial={{ tab: "mail" }} />);

    const table = await screen.findByTestId("review-mail-table");
    expect(within(table).getByText("暂存")).toBeInTheDocument();
    expect(within(table).getByTestId(`review-mail-hold-${UUID_C}`)).toHaveTextContent("恢复处理");
    await user.click(within(table).getByTestId(`review-mail-hold-${UUID_C}`));
    await waitFor(() => {
      const posted = stub!.requests.find(
        (item) => item.method === "POST" && item.url.pathname.endsWith("/hold"),
      );
      expect(posted?.body).toEqual({ held: false });
    });
  });
});

/**
 * 示例预览（`?demo=1`）评审的是**同一套界面**：它必须完全不发业务请求，且筛选与处置都在
 * 本地副本上生效——否则"样例"会变成"另一个页面"，评出来的东西对不上真实实现。
 */
describe("P13-A 邮件提交 · 示例预览", () => {
  const DEMO_SEARCH: ReviewSearch = { tab: "mail", demo: "1" };

  /** 只声明登录边界：任何别的请求都会让 `installFetchStub` 直接抛错。 */
  function sessionOnly(): StubbedRoute[] {
    return [
      {
        method: "POST",
        path: SESSION,
        respond: () =>
          jsonResponse(
            sessionCreated({
              roles: ["teacher"],
              scope: [
                {
                  course_id: "00000000-0000-4000-8000-00000000d007",
                  section_id: "00000000-0000-4000-8000-00000000d004",
                },
              ],
            }),
          ),
      },
    ];
  }

  it("不请求课程/名单/隔离区，直接渲染待处理示例；切换状态看得到已处理记录", async () => {
    const user = userEvent.setup();
    stub = installFetchStub(sessionOnly());
    await signIn("teacher1", "pw");
    renderWithProviders(<Harness initial={DEMO_SEARCH} />);

    expect(await screen.findByTestId("review-demo-banner")).toHaveTextContent("示例预览");
    const table = await screen.findByTestId("review-mail-table");
    expect(within(table).getByText("【作业】统计文件行数 - 王一")).toBeInTheDocument();
    expect(within(table).getByText("请问作业提交格式")).toBeInTheDocument();
    expect(within(table).queryByText("作业提交 - 李四")).toBeNull();

    // 示例自己给出课程/课序、原因与计数；不是"加载中"也不是"没有权限"。
    expect(screen.getByTestId("review-mail-course")).toHaveValue(
      "00000000-0000-4000-8000-00000000d007",
    );
    // 默认仍是「全部课序」；示例课序在下拉里可选，而不是从后端目录读来的。
    const sectionSelect = screen.getByTestId("review-mail-section");
    expect(sectionSelect).toHaveValue("");
    expect(
      sectionSelect.querySelector('option[value="00000000-0000-4000-8000-00000000d004"]')
        ?.textContent,
    ).toContain("01 · 2026 秋");

    await user.selectOptions(screen.getByTestId("review-mail-resolved"), "processed");
    expect(await screen.findByText("作业提交 - 李四")).toBeInTheDocument();
    // 已处理视图里既有「已完成」也有「已作废」：两行都带处理人/时间。
    expect(screen.getAllByText(/处理人：/).length).toBeGreaterThan(0);

    const business = stub!.requests.filter(
      (item) => item.method !== "POST" || item.url.pathname !== SESSION,
    );
    expect(business).toHaveLength(0);
  });

  it("匹配用户只改本地副本：成功后该行离开待处理列表，且不发任何写请求", async () => {
    const user = userEvent.setup();
    stub = installFetchStub(sessionOnly());
    await signIn("teacher1", "pw");
    renderWithProviders(<Harness initial={DEMO_SEARCH} />);

    const rowId = "00000000-0000-4000-8000-00000000e001";
    await user.click(await screen.findByTestId(`review-mail-resolve-${rowId}`));
    const assignment = await screen.findByTestId("review-resolve-assignment");
    await waitFor(() => {
      expect(assignment.querySelectorAll("option").length).toBeGreaterThan(1);
    });
    const assignmentId = Array.from(assignment.querySelectorAll("option"))[1].value;
    await user.selectOptions(assignment, assignmentId);
    await user.selectOptions(
      screen.getByTestId("review-resolve-student"),
      "00000000-0000-4000-8000-00000000d002",
    );
    await user.click(screen.getByTestId("review-resolve-continue"));
    await user.click(await screen.findByTestId("review-mail-confirm-submit"));

    expect(await screen.findByTestId("review-mail-resolved-ok")).toHaveTextContent(
      "已匹配用户并生成提交",
    );
    await waitFor(() => {
      expect(
        screen.queryByText("【作业】统计文件行数 - 王一"),
      ).toBeNull();
    });
    expect(
      stub!.requests.filter(
        (item) => item.method !== "POST" || item.url.pathname !== SESSION,
      ),
    ).toHaveLength(0);
  });
});
