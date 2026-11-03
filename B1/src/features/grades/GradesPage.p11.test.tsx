import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../testing/renderWithProviders";
import {
  binaryResponse,
  installFetchStub,
  jsonResponse,
  type StubbedRoute,
} from "../../testing/httpBoundary";
import {
  UUID_A,
  UUID_B,
  UUID_RUN,
  coursePage,
  courseSummary,
  enrollmentPublic,
  evaluationCommentary,
  evaluationReport,
  gradeRow,
  learningHintsResponse,
  questionPublic,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { GradesPage } from "./GradesPage";
import type { GradeRow } from "../../api/types";
import type { EChartsLoader } from "./echartsLoader";

/** 图表加载器桩：jsdom 没有 canvas，真实 ECharts 无法初始化。 */
const stubChartLoader: EChartsLoader = async () => ({
  init: () => ({ setOption: () => {}, resize: () => {}, dispose: () => {} }),
});

const STUDENT_ONE = "00000000-0000-4000-8000-0000000000e2";
const STUDENT_TWO = "00000000-0000-4000-8000-0000000000e4";
const STUDENT_THREE = "00000000-0000-4000-8000-0000000000e6";
const TEACHER_ID = "00000000-0000-4000-8000-0000000000d9";

function jwtFor(subject: string): string {
  const encode = (value: object) =>
    btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${encode({ alg: "EdDSA" })}.${encode({ sub: subject })}.signature`;
}

let originalFetch: typeof globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function rows(): GradeRow[] {
  return [
    gradeRow({
      submission_id: "00000000-0000-4000-8000-0000000000e1",
      student_id: STUDENT_ONE,
      student_number: null,
      display_name: null,
      state: "completed",
      score: 90,
      max_score: 100,
      is_pass: true,
      evaluation_run_id: null,
    }),
    gradeRow({
      submission_id: "00000000-0000-4000-8000-0000000000e3",
      student_id: STUDENT_TWO,
      student_number: null,
      display_name: null,
      state: "running",
      score: null,
      max_score: null,
      is_pass: null,
      evaluation_run_id: UUID_RUN,
    }),
  ];
}

function routes(
  role: "teacher" | "student",
  options: {
    roster?: string[];
    gradeRows?: GradeRow[];
    commentary?: ReturnType<typeof evaluationCommentary>;
  } = {},
): StubbedRoute[] {
  const roster = options.roster ?? [STUDENT_ONE, STUDENT_TWO, STUDENT_THREE];
  return [
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () =>
        jsonResponse(
          sessionCreated({
            roles: [role],
            scope: [{ course_id: UUID_A, section_id: UUID_B }],
            access_token: jwtFor(role === "teacher" ? TEACHER_ID : STUDENT_ONE),
          }),
        ),
    },
    { path: "/api/v1/courses", respond: () => jsonResponse(coursePage([courseSummary()])) },
    {
      path: `/api/v1/courses/${UUID_A}/sections`,
      respond: () => jsonResponse({ items: [{ section_id: UUID_B, section_code: "01" }] }),
    },
    {
      path: "/api/v1/questions",
      respond: () => jsonResponse({ items: [questionPublic()], next_cursor: null }),
    },
    {
      path: "/api/v1/grades",
      respond: () => jsonResponse({ items: options.gradeRows ?? rows(), next_cursor: null }),
    },
    {
      path: `/api/v1/sections/${UUID_B}/enrollments`,
      respond: () =>
        jsonResponse({
          items: roster.map((studentId, index) =>
            enrollmentPublic({
              enrollment_id: `00000000-0000-4000-8000-0000000000f${index + 1}`,
              student_id: studentId,
              student_number: `2026000${index + 1}`,
              display_name: ["张三", "李四", "王五"][index] ?? "学生",
            }),
          ),
          next_cursor: null,
        }),
    },
    {
      path: `/api/v1/evaluation-runs/${UUID_RUN}/report`,
      respond: () => jsonResponse(evaluationReport()),
    },
    {
      path: `/api/v1/evaluation-runs/${UUID_RUN}/commentary`,
      respond: () => jsonResponse(options.commentary ?? evaluationCommentary()),
    },
    {
      path: `/api/v1/evaluation-runs/${UUID_RUN}/learning-hints`,
      respond: () => jsonResponse(learningHintsResponse()),
    },
    {
      path: "/api/v1/grades/export",
      respond: () =>
        binaryResponse({
          body: "student_number,score\n20260001,90\n",
          contentType: "text/csv",
          filename: "grades.csv",
        }),
    },
  ];
}

async function openGrades(
  role: "teacher" | "student",
  options: Parameters<typeof routes>[1] = {},
  pageProps: { initialRunId?: string | null; onRunChange?: (runId: string | null) => void } = {},
) {
  originalFetch = globalThis.fetch;
  const stub = installFetchStub(routes(role, options));
  const user = userEvent.setup();
  await signIn(`${role}1`, "pw");
  renderWithProviders(<GradesPage chartLoader={stubChartLoader} {...pageProps} />);
  if (role === "teacher") {
    await screen.findByTestId("grades-table");
  } else {
    await screen.findByTestId("grades-table");
  }
  return { stub, user };
}

describe("P11 成绩筛选、身份关联、导出与深链", () => {
  it("时间范围按本地输入转 UTC RFC 3339 发送，结束早于开始时不发查询", async () => {
    const { stub, user } = await openGrades("teacher");

    const from = screen.getByLabelText(/起始时间/);
    const to = screen.getByLabelText(/结束时间/);
    await user.clear(from);
    await user.type(from, "2026-09-15T08:00");
    await user.clear(to);
    await user.type(to, "2026-09-15T09:30");

    await waitFor(() => {
      const request = stub.requests
        .filter((item) => item.url.pathname === "/api/v1/grades")
        .at(-1);
      expect(request?.url.searchParams.get("from")).toBe(
        new Date("2026-09-15T08:00").toISOString(),
      );
      expect(request?.url.searchParams.get("to")).toBe(
        new Date("2026-09-15T09:30").toISOString(),
      );
    });

    // 反向区间：显示字段级提示，且不会把反向区间发出去（不静默扩大范围）。
    await user.clear(from);
    await user.type(from, "2026-09-16T10:00");
    expect(await screen.findByTestId("grades-time-problem")).toHaveTextContent(
      "结束时间不能早于起始时间",
    );
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(
      stub.requests.filter(
        (item) =>
          item.url.pathname === "/api/v1/grades" &&
          item.url.searchParams.get("from") === new Date("2026-09-16T10:00").toISOString(),
      ),
    ).toHaveLength(0);
    stub.restore();
  });

  it("身份关联来自 B4 名单：缺行显示“名单中未找到”，不把平台 ID 当学号", async () => {
    const { stub } = await openGrades("teacher", { roster: [STUDENT_ONE, STUDENT_THREE] });
    const table = await screen.findByTestId("grades-table");

    // 李四（STUDENT_TWO）不在名单里：学号列与姓名列都显示“名单中未找到”。
    const markers = within(table).getAllByTestId("identity-missing");
    expect(markers.length).toBeGreaterThanOrEqual(1);
    const missingRow = markers[0].closest("tr") as HTMLElement;
    expect(within(missingRow).getAllByText("名单中未找到").length).toBeGreaterThanOrEqual(1);
    // 平台用户 ID 不作为学号显示。
    expect(table.textContent).not.toContain(STUDENT_TWO);
    // 张三来自名单，显示学号与姓名。
    expect(table).toHaveTextContent("20260001");
    expect(table).toHaveTextContent("张三");
    stub.restore();
  });

  it("导出 Drawer：必需列固定，可选列随 columns 发送，时间范围沿用页面筛选", async () => {
    const objectUrlSpy = vi.fn(() => "blob:test");
    URL.createObjectURL = objectUrlSpy as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = vi.fn() as unknown as typeof URL.revokeObjectURL;
    const { stub, user } = await openGrades("teacher");

    await user.click(screen.getByTestId("open-export"));
    const drawer = await screen.findByTestId("export-drawer");
    expect(within(drawer).getByTestId("export-required-columns").textContent).toContain("学号");
    await user.click(within(drawer).getByTestId("export-column-state"));
    await user.click(within(drawer).getByTestId("export-column-is_pass"));
    await user.click(within(drawer).getByTestId("download-export"));

    await waitFor(() => expect(objectUrlSpy).toHaveBeenCalledTimes(1));
    const exportRequest = stub.requests.find((item) =>
      item.url.pathname.endsWith("/grades/export"),
    );
    expect(exportRequest?.url.searchParams.get("columns")).toBe("state,is_pass");
    stub.restore();
  });

  it("从 P10 深链打开同一次评测的 report；关闭时清掉 URL 状态", async () => {
    const onRunChange = vi.fn();
    const { stub, user } = await openGrades(
      "teacher",
      {},
      { initialRunId: UUID_RUN, onRunChange },
    );

    expect(await screen.findByTestId("drilldown-modal")).toBeInTheDocument();
    expect(await screen.findByTestId("grade-drilldown")).toBeInTheDocument();
    const report = stub.requests.find((item) => item.url.pathname.endsWith("/report"));
    expect(report?.url.searchParams.get("view")).toBe("teacher");

    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(onRunChange).toHaveBeenCalledWith(null);
    stub.restore();
  });

  it("教师能看到模型参考分；学生视图中不出现参考分", async () => {
    const teacher = await openGrades("teacher");
    await teacher.user.click(screen.getByTestId(`drilldown-${UUID_RUN}`));
    const teacherDrilldown = await screen.findByTestId("grade-drilldown");
    expect(within(teacherDrilldown).getByTestId("advisory-score")).toBeInTheDocument();
    teacher.stub.restore();

    const student = await openGrades("student");
    await student.user.click(screen.getByTestId(`drilldown-${UUID_RUN}`));
    const studentDrilldown = await screen.findByTestId("grade-drilldown");
    expect(within(studentDrilldown).queryByTestId("advisory-score")).toBeNull();
    expect(within(studentDrilldown).getByTestId("learning-hints")).toBeInTheDocument();
    student.stub.restore();
  });

  it("题目选择器显示“标题 · vN”，选择后按 question_version_id 查询", async () => {
    const { stub, user } = await openGrades("teacher");
    const select = screen.getByLabelText("题目");
    expect(within(select).getByText("统计文件行数 · v3")).toBeInTheDocument();

    await user.selectOptions(select, UUID_A);
    await waitFor(() => {
      const request = stub.requests
        .filter((item) => item.url.pathname === "/api/v1/grades")
        .at(-1);
      expect(request?.url.searchParams.get("question_version_id")).toBe(UUID_A);
    });
    stub.restore();
  });
});
