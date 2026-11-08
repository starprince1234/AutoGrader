import { useState } from "react";
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
import {
  classPublic,
  courseSummary,
  rosterImportPreview,
} from "../../testing/contractFixtures";
import { RosterImportPanel } from "./RosterImportPanel";

const COURSE_ID = courseSummary().course_id;
const SECTION_ID = rosterImportPreview().section_id;
const CLASS_ID = classPublic().class_id;
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
        jsonResponse({
          items: [
            {
              section_id: SECTION_ID,
              course_id: COURSE_ID,
              section_code: "01",
              class_count: 1,
              enrollment_count: 2,
            },
          ],
        }),
    },
    {
      path: `/api/v1/sections/${SECTION_ID}/classes`,
      respond: () =>
        jsonResponse({
          items: [classPublic({ class_id: CLASS_ID, section_id: SECTION_ID })],
        }),
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

/**
 * 面板的作用域与任务 ID 来自页面（真实页面把它们放在 URL 里）。
 * 这里用一个持有 state 的壳代替 URL：上传成功后 id 回写，面板据此进入预览/映射。
 */
function PanelHarness({
  initialImportId = null,
}: {
  initialImportId?: string | null;
}) {
  const [importId, setImportId] = useState<string | null>(initialImportId);
  return (
    <RosterImportPanel
      importId={importId}
      initialCourseId={COURSE_ID}
      initialSectionId={SECTION_ID}
      onImportId={setImportId}
    />
  );
}

function renderPanel(importId: string | null = null) {
  return renderWithProviders(<PanelHarness initialImportId={importId} />);
}

async function selectClass(user: ReturnType<typeof userEvent.setup>) {
  // 课程/课序/班级都是异步目录：必须等选项真的进入 select 再选（否则 selectOptions 会抛错）。
  const course = await screen.findByTestId("import-course");
  await waitFor(() => expect(within(course).getAllByRole("option").length).toBeGreaterThan(1));
  await user.selectOptions(course, COURSE_ID);

  const section = await screen.findByTestId("import-section");
  await waitFor(() =>
    expect(within(section).getAllByRole("option").length).toBeGreaterThan(1),
  );
  await user.selectOptions(section, SECTION_ID);

  const klass = await screen.findByTestId("import-class");
  await waitFor(() => expect(within(klass).getAllByRole("option").length).toBeGreaterThan(1));
  await user.selectOptions(klass, CLASS_ID);
}

async function uploadFile(user: ReturnType<typeof userEvent.setup>, name = "roster.xlsx") {
  await user.upload(
    screen.getByLabelText("名单文件（CSV / XLSX）"),
    new File(["roster"], name, {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }),
  );
  await user.click(screen.getByTestId("upload-submit"));
}

describe("名单导入向导（RQ-B-1-10 / v0.32.0 班级级）", () => {
  it("上传前必须选清 课程 → 课序 → 班级；关系不清时不发请求", async () => {
    const user = userEvent.setup();
    stub = installFetchStub(baseRoutes(rosterImportPreview()));
    renderPanel();

    // 已带初始课程/课序，但班级未选：仍不可上传，且不发请求。
    expect(await screen.findByTestId("import-scope-incomplete")).toHaveTextContent(
      "选择课程、课序和班级后才能上传名单",
    );
    await user.upload(
      screen.getByLabelText("名单文件（CSV / XLSX）"),
      new File(["roster"], "roster.csv", { type: "text/csv" }),
    );
    expect(screen.getByTestId("upload-submit")).toBeDisabled();
    await user.click(screen.getByTestId("upload-submit"));
    expect(
      stub.requests.some(
        (request) => request.method === "POST" && request.url.pathname === "/api/v1/roster-imports",
      ),
    ).toBe(false);
  });

  it("选清归属后上传：multipart 带 course_id/section_id/class_id，预览逐行渲染且确认前零写入", async () => {
    const user = userEvent.setup();
    stub = installFetchStub(baseRoutes(rosterImportPreview()));
    renderPanel();

    await selectClass(user);
    await uploadFile(user);

    const accepted = await screen.findByTestId("accepted-rows");
    expect(accepted).toHaveTextContent("20260001");
    const rejected = screen.getByTestId("rejected-rows");
    expect(rejected).toHaveTextContent("重复学号");
    expect(rejected).toHaveTextContent("缺少必填字段：学号");

    // 邮箱等敏感列被脱敏，学号保持明文
    expect(screen.getByTestId("accepted-rows")).toHaveTextContent("已脱敏");
    expect(screen.getByTestId("accepted-rows")).not.toHaveTextContent("zhangsan@example.edu");

    const uploadRequest = stub.requests.find(
      (request) => request.method === "POST" && request.url.pathname === "/api/v1/roster-imports",
    );
    expect(uploadRequest?.formData?.get("course_id")).toBe(COURSE_ID);
    expect(uploadRequest?.formData?.get("section_id")).toBe(SECTION_ID);
    expect(uploadRequest?.formData?.get("class_id")).toBe(CLASS_ID);
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

    renderPanel();
    await selectClass(user);
    await uploadFile(user);
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

    renderPanel();
    await selectClass(user);
    await uploadFile(user, "roster.csv");

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
      baseRoutes(
        rosterImportPreview({ status: "failed_parse", accepted_rows: [], rejected_rows: [] }),
      ),
    );

    renderPanel();
    await selectClass(user);
    await uploadFile(user, "broken.csv");

    // 状态标签与错误提示都写明解析失败（页面不会显示可确认的假成功状态）
    expect(screen.getAllByText(/解析失败/).length).toBeGreaterThan(0);
    expect(screen.queryByTestId("confirm-import")).toBeNull();
  });
});

describe("导入任务恢复（P12：任务 ID 住在 URL）", () => {
  it("带 import_id 打开时直接恢复服务端预览，不重新上传", async () => {
    const stub = installFetchStub(baseRoutes(rosterImportPreview()));
    renderPanel(IMPORT_ID);

    // 预览来自 GET /roster-imports/{id}：刷新后仍能看到逐行结果。
    expect(await screen.findByTestId("accepted-rows")).toBeInTheDocument();
    expect(screen.getByTestId("no-write-notice")).toBeInTheDocument();
    const uploads = stub.requests.filter(
      (request) => request.method === "POST" && request.url.pathname === "/api/v1/roster-imports",
    );
    expect(uploads).toHaveLength(0);
    stub.restore();
  });

  it("任务已过期（404）时说明需要重新上传，不把 404 显示成空预览", async () => {
    const stub = installFetchStub([
      ...baseRoutes(rosterImportPreview()).filter(
        (route) => route.path !== `/api/v1/roster-imports/${IMPORT_ID}`,
      ),
      {
        path: `/api/v1/roster-imports/${IMPORT_ID}`,
        respond: () =>
          problemResponse({
            status: 404,
            title: "Not Found",
            detail: "import task not found",
            instance: `/api/v1/roster-imports/${IMPORT_ID}`,
          }),
      },
    ]);
    renderPanel(IMPORT_ID);

    expect(await screen.findByTestId("import-expired")).toHaveTextContent("找不到这次导入");
    expect(screen.queryByTestId("accepted-rows")).toBeNull();
    stub.restore();
  });
});
