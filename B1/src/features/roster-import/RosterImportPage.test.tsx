import { afterEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../testing/renderWithProviders";
import {
  installFetchStub,
  jsonResponse,
  problemResponse,
  type FetchStub,
} from "../../testing/httpBoundary";
import { courseSummary, rosterImportPreview } from "../../testing/contractFixtures";
import { RosterImportPage } from "./RosterImportPage";

const COURSE_ID = courseSummary().course_id;
const SECTION_ID = rosterImportPreview().section_id;
const IMPORT_ID = rosterImportPreview().roster_import_id;

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
});

function baseRoutes(
  initial: ReturnType<typeof rosterImportPreview>,
  confirm?: () => Response,
) {
  // 解析任务的状态由服务端推进：上传后是初态，确认后变成 committed。
  let status = initial.status;
  return [
    {
      path: "/api/v1/courses",
      respond: () => jsonResponse({ items: [courseSummary()], next_cursor: null }),
    },
    {
      path: `/api/v1/courses/${COURSE_ID}/sections`,
      respond: () =>
        jsonResponse({ items: [{ section_id: SECTION_ID, section_code: "01" }] }),
    },
    {
      method: "POST",
      path: "/api/v1/roster-imports",
      respond: () => jsonResponse(initial, 202),
    },
    {
      method: "PUT",
      path: `/api/v1/roster-imports/${IMPORT_ID}/mapping`,
      respond: () => {
        status = "previewed";
        return jsonResponse({}, 200);
      },
    },
    {
      method: "POST",
      path: `/api/v1/roster-imports/${IMPORT_ID}/confirm`,
      respond:
        confirm ??
        (() => {
          status = "committed";
          return new Response(null, { status: 202 });
        }),
    },
    {
      path: `/api/v1/roster-imports/${IMPORT_ID}`,
      respond: () => jsonResponse({ ...initial, status }),
    },
  ];
}

async function selectCourseAndSection(user: ReturnType<typeof userEvent.setup>) {
  const courseSelect = await screen.findByLabelText("课程");
  await waitFor(() => expect(within(courseSelect).getAllByRole("option")).toHaveLength(2));
  await user.selectOptions(courseSelect, COURSE_ID);

  const sectionSelect = screen.getByLabelText("课序");
  await waitFor(() => expect(within(sectionSelect).getAllByRole("option")).toHaveLength(2));
  await user.selectOptions(sectionSelect, SECTION_ID);
}

describe("名单导入向导（RQ-B-1-10）", () => {
  it("上传 → 预览 → 确认：预览逐行渲染、敏感列脱敏、被拒行不丢弃，且确认前零写入", async () => {
    const user = userEvent.setup();
    stub = installFetchStub(baseRoutes(rosterImportPreview()));

    renderWithProviders(<RosterImportPage />);
    await selectCourseAndSection(user);

    const file = new File(["roster"], "roster.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    await user.upload(screen.getByLabelText("名单文件（PDF / XLSX）"), file);
    await user.click(screen.getByTestId("upload-submit"));

    // 预览：通过行与拒绝行都在
    const accepted = await screen.findByTestId("accepted-rows");
    expect(accepted).toHaveTextContent("20260001");
    const rejected = screen.getByTestId("rejected-rows");
    expect(rejected).toHaveTextContent("重复学号");
    expect(rejected).toHaveTextContent("缺少必填字段：学号");

    // 邮箱等敏感列被脱敏，学号保持明文
    expect(screen.getByTestId("accepted-rows")).toHaveTextContent("已脱敏");
    expect(screen.getByTestId("accepted-rows")).not.toHaveTextContent("zhangsan@example.edu");

    // 上传请求携带 course/section 与文件（multipart 边界）
    const uploadRequest = stub.requests.find(
      (request) => request.method === "POST" && request.url.pathname === "/api/v1/roster-imports",
    );
    expect(uploadRequest?.formData?.get("course_id")).toBe(COURSE_ID);
    expect(uploadRequest?.formData?.get("section_id")).toBe(SECTION_ID);
    expect(uploadRequest?.formData?.get("file")).toBeInstanceOf(File);

    // 确认前没有任何写入动作
    expect(
      stub.requests.some((request) => request.url.pathname.endsWith("/confirm")),
    ).toBe(false);

    await user.click(screen.getByTestId("confirm-import"));

    await waitFor(() => expect(screen.getByTestId("import-committed")).toBeInTheDocument());
    const confirmRequest = stub.requests.find((request) => request.url.pathname.endsWith("/confirm"));
    expect(confirmRequest?.method).toBe("POST");
    expect(confirmRequest?.headers.get("idempotency-key")).toBeTruthy();
    // 结果报告里仍然保留被拒绝的行
    expect(screen.getByTestId("rejected-rows")).toHaveTextContent("重复学号");
  });

  it("确认失败后重试复用**同一个**幂等键（可重放的前提，键不能每次点击都重生成）", async () => {
    const user = userEvent.setup();
    let attempts = 0;
    stub = installFetchStub(
      baseRoutes(rosterImportPreview(), () => {
        attempts += 1;
        // 第一次确认失败（网络/服务端错误），用户再点一次。
        return attempts === 1
          ? problemResponse({ status: 503, title: "Unavailable", detail: "try again" })
          : new Response(null, { status: 202 });
      }),
    );

    renderWithProviders(<RosterImportPage />);
    await selectCourseAndSection(user);
    await user.upload(
      screen.getByLabelText("名单文件（PDF / XLSX）"),
      new File(["roster"], "roster.xlsx", {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      }),
    );
    await user.click(screen.getByTestId("upload-submit"));
    await screen.findByTestId("confirm-import");

    await user.click(screen.getByTestId("confirm-import"));
    // 失败必须如实展示，且确认按钮仍在（否则用户没有重试入口）
    expect(await screen.findByTestId("error-state")).toBeInTheDocument();
    await user.click(screen.getByTestId("confirm-import"));

    await waitFor(() => expect(attempts).toBe(2));
    const keys = stub.requests
      .filter((request) => request.url.pathname.endsWith("/confirm"))
      .map((request) => request.headers.get("idempotency-key"));
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBeTruthy();
    // 关键断言：同一次导入的重试带同一个键。每次点击重新生成会让这条失败。
    expect(keys[1]).toBe(keys[0]);
  });

  it("列名不匹配 → 进入列映射，未指派时明确报错且不发请求", async () => {
    const user = userEvent.setup();
    stub = installFetchStub(
      baseRoutes(rosterImportPreview({ status: "mapping_required", proposed_mapping: {} })),
    );

    renderWithProviders(<RosterImportPage />);
    await selectCourseAndSection(user);
    await user.upload(
      screen.getByLabelText("名单文件（PDF / XLSX）"),
      new File(["x"], "roster.pdf", { type: "application/pdf" }),
    );
    await user.click(screen.getByTestId("upload-submit"));

    expect(await screen.findByTestId("mapping-form")).toBeInTheDocument();
    // 不自动猜测：所有目标字段初始为"未指派"
    expect(screen.getByLabelText("目标字段：姓名")).toHaveValue("");
    expect(screen.getByLabelText("目标字段：学号")).toHaveValue("");

    await user.click(screen.getByRole("button", { name: "提交列映射" }));
    expect(await screen.findByTestId("mapping-error")).toHaveTextContent("必填字段未指派");
    expect(stub.requests.some((request) => request.method === "PUT")).toBe(false);

    // 人工指派后才提交映射
    await user.selectOptions(screen.getByLabelText("目标字段：姓名"), "姓名");
    await user.selectOptions(screen.getByLabelText("目标字段：学号"), "学号");
    await user.selectOptions(screen.getByLabelText("目标字段：课程编号"), "课程编号");
    await user.selectOptions(screen.getByLabelText("目标字段：课序号"), "课序号");
    await user.click(screen.getByRole("button", { name: "提交列映射" }));

    await waitFor(() => {
      const mapping = stub?.requests.find((request) => request.method === "PUT");
      expect(mapping?.body).toEqual({
        columns: {
          display_name: "姓名",
          student_number: "学号",
          course_code: "课程编号",
          section_code: "课序号",
        },
      });
    });
  });

  it("解析失败时明确提示，不伪造成功状态", async () => {
    const user = userEvent.setup();
    stub = installFetchStub(
      baseRoutes(rosterImportPreview({ status: "failed_parse", accepted_rows: [], rejected_rows: [] })),
    );

    renderWithProviders(<RosterImportPage />);
    await selectCourseAndSection(user);
    await user.upload(
      screen.getByLabelText("名单文件（PDF / XLSX）"),
      new File(["x"], "broken.pdf", { type: "application/pdf" }),
    );
    await user.click(screen.getByTestId("upload-submit"));

    // 状态标签与错误提示都写明解析失败（页面不会显示可确认的假成功状态）
    expect(screen.getAllByText(/解析失败/).length).toBeGreaterThan(0);
    expect(screen.queryByTestId("confirm-import")).toBeNull();
  });
});
