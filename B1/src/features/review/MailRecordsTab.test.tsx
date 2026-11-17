import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
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
  type FetchStub,
  type StubbedRoute,
} from "../../testing/httpBoundary";
import {
  UUID_A,
  UUID_B,
  UUID_C,
  UUID_D,
  assignmentPage,
  assignmentPublic,
  coursePage,
  courseSummary,
  mailRecordPage,
  mailRecordView,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { resetLoggerForTests } from "../../logging/logger";
import { MailRecordsTab } from "./MailRecordsTab";
import type { ReviewSearch } from "./search";

/**
 * `/review` 邮件记录标签（只读）。
 *
 * 断言的重点是**分类语义与数据边界**，而不是排版：
 * - 默认请求 `category=pending`，点「已处理 / 其他」改发对应分类；
 * - 已处理行给作业标题与「查看作业」链接（href 带 student 查询参数）；
 * - 待处理/其他行显示原因的中文说明，不透出英文原因码；
 * - 403 显示"没有权限"而不是空列表；分类不同，空状态文案不同；
 * - 不再请求旧隔离接口，也不再出现归属/暂存/作废/打回等处置按钮（本页只读）。
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
const MAIL_RECORDS = "/api/v1/intake/mail-records";
const ASSIGNMENTS = "/api/v1/assignments";

function routesFor(options: {
  rows?: ReturnType<typeof mailRecordView>[];
  roles?: ("teacher" | "admin")[];
  scope?: { course_id: string; section_id?: string }[];
  mailRespond?: () => Response;
  courses?: ReturnType<typeof courseSummary>[];
  assignments?: ReturnType<typeof assignmentPublic>[];
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
      path: MAIL_RECORDS,
      respond: () => options.mailRespond?.() ?? jsonResponse(mailRecordPage(rows)),
    },
    {
      method: "GET",
      path: ASSIGNMENTS,
      respond: () => jsonResponse(assignmentPage(options.assignments ?? [])),
    },
  ];
}

function Harness({ initial }: { initial: ReviewSearch }) {
  const [search, setSearch] = useState(initial);
  return <MailRecordsTab search={search} onChange={setSearch} />;
}

/** 页面里有 `<Link>`，因此需要一条真实的最小路由（只用于满足路由注册表）。 */
function renderTab(initial: ReviewSearch) {
  const rootRoute = createRootRoute({ component: () => <Harness initial={initial} /> });
  const grading = createRoute({
    getParentRoute: () => rootRoute,
    path: "/assignments/$assignmentId/grading",
    component: () => <span>grading target</span>,
  });
  const assignments = createRoute({
    getParentRoute: () => rootRoute,
    path: "/assignments",
    component: () => <span>assignments target</span>,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([grading, assignments]),
    history: createMemoryHistory({ initialEntries: ["/review"] }),
  });
  return renderWithProviders(<RouterProvider router={router} />);
}

function mailRequests(): FetchStub["requests"] {
  return (stub?.requests ?? []).filter(
    (item) => item.method === "GET" && item.url.pathname === MAIL_RECORDS,
  );
}

const MAIL_PATH: ReviewSearch = { course_id: UUID_A, section_id: UUID_B };

describe("/review 邮件记录（只读）", () => {
  it("默认请求 pending 分类并带上课程/课序筛选，渲染真实记录", async () => {
    const row = mailRecordView({ record_id: UUID_C, subject: "作业一提交" });
    stub = installFetchStub(routesFor({ rows: [row] }));
    await signIn("teacher1", "pw");
    renderTab(MAIL_PATH);

    const table = await screen.findByTestId("review-records-table");
    const request = mailRequests()[0];
    expect(request).toBeDefined();
    expect(request.url.searchParams.get("category")).toBe("pending");
    expect(request.url.searchParams.get("course_id")).toBe(UUID_A);
    expect(request.url.searchParams.get("section_id")).toBe(UUID_B);
    expect(request.url.searchParams.get("limit")).toBe("50");
    expect(request.url.searchParams.get("cursor")).toBeNull();
    // 三个分类标签都在，默认选中「待处理」。
    expect(screen.getByRole("tab", { name: "已处理" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "待处理" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "其他" })).toBeInTheDocument();
    expect(within(table).getByText("作业一提交")).toBeInTheDocument();
    expect(within(table).getByText(/OS-2026/)).toBeInTheDocument();
  });

  it("点「已处理 / 其他」会改发对应 category 的请求", async () => {
    const user = userEvent.setup();
    stub = installFetchStub(routesFor({ rows: [mailRecordView({ record_id: UUID_C })] }));
    await signIn("teacher1", "pw");
    renderTab(MAIL_PATH);
    await screen.findByTestId("review-records-table");

    await user.click(screen.getByRole("tab", { name: "已处理" }));
    await waitFor(() => {
      expect(mailRequests().some((item) => item.url.searchParams.get("category") === "processed")).toBe(
        true,
      );
    });

    await user.click(screen.getByRole("tab", { name: "其他" }));
    await waitFor(() => {
      expect(mailRequests().some((item) => item.url.searchParams.get("category") === "other")).toBe(
        true,
      );
    });
  });

  it("已处理行显示作业标题，并给出带 student 查询参数的「查看作业」链接", async () => {
    const row = mailRecordView({
      record_id: UUID_C,
      category: "processed",
      assignment_id: UUID_A,
      student_id: UUID_D,
      reason_code: null,
    });
    stub = installFetchStub(
      routesFor({
        rows: [row],
        assignments: [
          assignmentPublic({ assignment_id: UUID_A, course_id: UUID_A, title: "第一次作业" }),
        ],
      }),
    );
    await signIn("teacher1", "pw");
    renderTab({ course_id: UUID_A, section_id: UUID_B, status: "processed" });

    const table = await screen.findByTestId("review-records-table");
    expect(await within(table).findByText("第一次作业")).toBeInTheDocument();
    const link = within(table).getByTestId(`review-view-assignment-${UUID_C}`);
    expect(link).toHaveTextContent("查看作业");
    expect(link).toHaveAttribute(
      "href",
      `/assignments/${UUID_A}/grading?student=${UUID_D}`,
    );
    const request = mailRequests()[0];
    expect(request.url.searchParams.get("category")).toBe("processed");
  });

  it("已处理行缺少 assignment_id/student_id 时不渲染「查看作业」链接", async () => {
    const row = mailRecordView({
      record_id: UUID_C,
      category: "processed",
      assignment_id: null,
      student_id: null,
      reason_code: null,
    });
    stub = installFetchStub(routesFor({ rows: [row] }));
    await signIn("teacher1", "pw");
    renderTab({ course_id: UUID_A, status: "processed" });

    const table = await screen.findByTestId("review-records-table");
    expect(within(table).getByText("（未关联作业）")).toBeInTheDocument();
    expect(within(table).queryByTestId(`review-view-assignment-${UUID_C}`)).toBeNull();
  });

  it("待处理行显示原因的中文说明，不显示英文原因码或处置按钮", async () => {
    const row = mailRecordView({
      record_id: UUID_C,
      reason_code: "email_verification_expired",
    });
    stub = installFetchStub(routesFor({ rows: [row] }));
    await signIn("teacher1", "pw");
    renderTab(MAIL_PATH);

    const table = await screen.findByTestId("review-records-table");
    expect(within(table).getByTestId(`review-reason-${UUID_C}`)).toHaveTextContent(
      "核验码已过期，提交没有列入作业",
    );
    expect(within(table).queryByText("email_verification_expired")).toBeNull();
    // 本页只读：旧的归属/暂存/作废/打回/详情入口全部不存在。
    expect(screen.queryByTestId(`review-mail-resolve-${UUID_C}`)).toBeNull();
    expect(screen.queryByTestId(`review-mail-hold-${UUID_C}`)).toBeNull();
    expect(screen.queryByTestId(`review-mail-dismiss-${UUID_C}`)).toBeNull();
    expect(screen.queryByTestId(`review-mail-reject-${UUID_C}`)).toBeNull();
    expect(screen.queryByTestId(`review-mail-detail-${UUID_C}`)).toBeNull();
    expect(screen.queryByTestId("review-mail-detail-drawer")).toBeNull();
  });

  it("403 显示权限说明而不是空列表", async () => {
    stub = installFetchStub(
      routesFor({
        mailRespond: () =>
          problemResponse({
            status: 403,
            title: "Forbidden",
            detail: "course scope does not cover this mail record",
          }),
      }),
    );
    await signIn("teacher1", "pw");
    renderTab(MAIL_PATH);

    expect(await screen.findByTestId("review-records-forbidden")).toHaveTextContent(
      "你没有权限查看该课程的邮件记录。",
    );
    expect(screen.queryByTestId("review-records-empty")).toBeNull();
  });

  it("空列表按分类给出不同空状态，且不再附带解释性提示", async () => {
    const user = userEvent.setup();
    stub = installFetchStub(routesFor({ rows: [] }));
    await signIn("teacher1", "pw");
    renderTab(MAIL_PATH);

    expect(await screen.findByTestId("review-records-empty")).toHaveTextContent(
      "没有待处理的邮件记录。",
    );
    // 解释性提示已删除（未关联记录仅管理员可见之类的说明不再出现在页面上）。
    expect(screen.queryByText(/仅管理员可见/)).toBeNull();
    await user.click(screen.getByRole("tab", { name: "已处理" }));
    await waitFor(() => {
      expect(screen.getByTestId("review-records-empty")).toHaveTextContent(
        "还没有已处理的邮件记录。",
      );
    });
    await user.click(screen.getByRole("tab", { name: "其他" }));
    await waitFor(() => {
      expect(screen.getByTestId("review-records-empty")).toHaveTextContent(
        "没有其他邮件记录。",
      );
    });
  });

  it("课程筛选会进入请求参数", async () => {
    const user = userEvent.setup();
    stub = installFetchStub(routesFor({ rows: [] }));
    await signIn("teacher1", "pw");
    renderTab({});

    const select = await screen.findByTestId("review-records-course");
    // 课程目录是异步加载的：等到选项真的出现再选（否则 selectOptions 会拿到空 select）。
    await waitFor(() => {
      expect(within(select).getByRole("option", { name: /OS-2026/ })).toBeInTheDocument();
    });
    await user.selectOptions(select, UUID_A);
    await waitFor(() => {
      expect(
        mailRequests().some((item) => item.url.searchParams.get("course_id") === UUID_A),
      ).toBe(true);
    });
  });

  it("staff 默认看全部有权限课程（不加课程筛选，且下拉含「全部课程」）", async () => {
    stub = installFetchStub(routesFor({ rows: [mailRecordView({ record_id: UUID_C })] }));
    await signIn("teacher1", "pw");
    renderTab({});

    const table = await screen.findByTestId("review-records-table");
    expect(table).toBeInTheDocument();
    const course = screen.getByTestId("review-records-course");
    expect(within(course).getByRole("option", { name: "全部课程" })).toBeInTheDocument();
    const request = mailRequests()[0];
    expect(request.url.searchParams.get("course_id")).toBeNull();
    expect(request.url.searchParams.get("section_id")).toBeNull();
  });

  it("管理员看到未关联行并标为「未关联」", async () => {
    const unscoped = mailRecordView({
      record_id: UUID_C,
      course_id: null,
      section_id: null,
    });
    stub = installFetchStub(routesFor({ rows: [unscoped], roles: ["admin"], scope: [] }));
    await signIn("admin1", "pw");
    renderTab({});

    expect(await screen.findByTestId("review-records-table")).toHaveTextContent("未关联");
    expect(mailRequests()[0].url.searchParams.get("course_id")).toBeNull();
  });

  it("不再请求旧隔离列表接口", async () => {
    stub = installFetchStub(routesFor({ rows: [mailRecordView({ record_id: UUID_C })] }));
    await signIn("teacher1", "pw");
    renderTab(MAIL_PATH);
    await screen.findByTestId("review-records-table");

    expect(
      stub!.requests.filter((item) => item.url.pathname.includes("/intake/quarantines")),
    ).toHaveLength(0);
  });

  it("加载更多追加下一页并带上服务端游标", async () => {
    const user = userEvent.setup();
    const first = mailRecordView({ record_id: UUID_C, subject: "第一封" });
    const second = mailRecordView({ record_id: UUID_D, subject: "第二封" });
    stub = installFetchStub([
      ...routesFor({}).filter((route) => route.path !== MAIL_RECORDS),
      {
        method: "GET",
        path: MAIL_RECORDS,
        respond: ({ url }) =>
          jsonResponse(
            url.searchParams.get("cursor") === null
              ? mailRecordPage([first], "next-page")
              : mailRecordPage([second]),
          ),
      },
    ]);
    await signIn("teacher1", "pw");
    renderTab(MAIL_PATH);

    await user.click(await screen.findByTestId("review-records-load-more"));
    await waitFor(() => {
      const requests = mailRequests();
      expect(requests).toHaveLength(2);
      expect(requests[1].url.searchParams.get("cursor")).toBe("next-page");
    });
    expect(await screen.findByText("第二封")).toBeInTheDocument();
  });
});
