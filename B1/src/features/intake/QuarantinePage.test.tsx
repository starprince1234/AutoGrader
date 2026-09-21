import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../testing/renderWithProviders";
import {
  installFetchStub,
  jsonResponse,
  problemResponse,
  type StubbedRoute,
} from "../../testing/httpBoundary";
import {
  UUID_A,
  UUID_B,
  UUID_C,
  UUID_D,
  coursePage,
  courseSummary,
  enrollmentPublic,
  intakeQuarantinePage,
  intakeQuarantineView,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { QuarantinePage } from "./QuarantinePage";
import type { IntakeQuarantineView } from "../../api/types";

/**
 * 隔离区复核台（CHG-0014；可归属集合与 `resolved` 语义见 CHG-0021）。
 *
 * 这个页面存在的理由决定了断言：邮件接收路径一直在写隔离记录，却长期没有读取路径，
 * 于是从教师角度看未匹配的邮件"无声无息地消失"。因此这里断言的是**读取路径真的通了**
 * （筛选条件确实随请求发出，且 `resolved` **永不省略**）、**归属确实按契约形状提交**
 * （只有 `student_id`/`note`）、**不可归属的原因码不显示归属按钮**，
 * 以及**拿不到候选学生时如实说明**（不伪造候选人）。
 */

const TARGET = UUID_C;
const ALREADY_RESOLVED = "00000000-0000-4000-8000-0000000000c9";
const NOT_ATTRIBUTABLE = "00000000-0000-4000-8000-0000000000ca";

function quarantineRoutes(options: {
  pages?: { items: IntakeQuarantineView[]; next_cursor: string | null }[];
  resolve?: () => Response;
  enrollments?: ReturnType<typeof enrollmentPublic>[];
}): StubbedRoute[] {
  const pages =
    options.pages ??
    [
      intakeQuarantinePage([
        intakeQuarantineView({ intake_quarantine_id: TARGET }),
        intakeQuarantineView({
          intake_quarantine_id: ALREADY_RESOLVED,
          // `ambiguous_match` 是可归属的（CHG-0021 §2.1）：这一行的按钮消失只能是因为
          // **已经归属过**，而不是因为原因码——两者必须能被测试分开。
          reason_code: "ambiguous_match",
          detail: "同一学号在名单中出现两次。",
          resolved: true,
          resolved_student_id: UUID_D,
          resolution_note: "与上一封重复。",
        }),
        // 内容类拒绝：人工补一个学生修不好它（服务端一律 422），因此界面不提供归属入口。
        intakeQuarantineView({
          intake_quarantine_id: NOT_ATTRIBUTABLE,
          reason_code: "archive_bomb",
          detail: "附件解压后体积超过上限，已按策略拒绝。",
        }),
      ]),
    ];

  // 归属成功后，后续读取返回"已处理"的那一行（模拟服务端真实变化）。
  let resolved = false;

  return [
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () => jsonResponse(sessionCreated({ roles: ["teacher"], access_token: "t" })),
    },
    {
      method: "GET",
      path: "/api/v1/intake/quarantines",
      respond: ({ url }) => {
        if (url.searchParams.get("resolved") === "true") {
          return jsonResponse(
            intakeQuarantinePage([
              intakeQuarantineView({
                intake_quarantine_id: TARGET,
                resolved: true,
                resolved_student_id: UUID_D,
                resolution_note: "助教确认是张三的信。",
              }),
            ]),
          );
        }
        if (resolved) {
          return jsonResponse(
            intakeQuarantinePage([
              intakeQuarantineView({
                intake_quarantine_id: TARGET,
                resolved: true,
                resolved_student_id: UUID_D,
                resolution_note: "助教确认是张三的信。",
              }),
            ]),
          );
        }
        const cursor = url.searchParams.get("cursor");
        const index = Math.min(cursor === null ? 0 : Number(cursor), pages.length - 1);
        return jsonResponse({
          items: pages[index].items,
          next_cursor: pages[index].next_cursor,
        });
      },
    },
    {
      method: "POST",
      path: `/api/v1/intake/quarantines/${TARGET}/resolve`,
      respond: () => {
        resolved = true;
        return (
          options.resolve?.() ??
          jsonResponse(
            intakeQuarantineView({
              intake_quarantine_id: TARGET,
              resolved: true,
              resolved_student_id: UUID_D,
              resolution_note: "助教确认是张三的信。",
            }),
          )
        );
      },
    },
    {
      method: "GET",
      path: "/api/v1/courses",
      respond: () => jsonResponse(coursePage([courseSummary()])),
    },
    {
      method: "GET",
      path: `/api/v1/courses/${UUID_A}/sections`,
      respond: () => jsonResponse({ items: [{ section_id: UUID_B, section_code: "01" }] }),
    },
    {
      method: "GET",
      path: `/api/v1/sections/${UUID_B}/enrollments`,
      respond: () =>
        jsonResponse({
          items:
            options.enrollments ??
            [
              enrollmentPublic({ student_id: UUID_D, display_name: "张三", student_number: "20260001" }),
              enrollmentPublic({
                enrollment_id: "00000000-0000-4000-8000-0000000000f5",
                // 名单里尚未绑定平台账号的行：**不是**候选人（猜一个 ID 出来是伪造数据）。
                student_id: null,
                display_name: "李四",
                student_number: "20260002",
              }),
            ],
          next_cursor: null,
        }),
    },
  ];
}

async function open(options: Parameters<typeof quarantineRoutes>[0] = {}) {
  const stub = installFetchStub(quarantineRoutes(options));
  const user = userEvent.setup();
  await signIn("teacher1", "pw");
  renderWithProviders(<QuarantinePage />);
  // 筛选面板总是渲染（列表可能为空，因此不能等表格）；这里等到列表进入"有结果/空态"的稳定状态。
  await screen.findByLabelText(/原因码/);
  await waitFor(() => {
    const settled =
      screen.queryByTestId("quarantine-table") ?? screen.queryByTestId("quarantine-empty");
    expect(settled).not.toBeNull();
  });
  return { stub, user };
}

describe("隔离区列表（CHG-0014；resolved 语义 CHG-0021）", () => {
  it("默认读「未处理」，并把 resolved/limit 显式放进请求（永不省略 resolved）", async () => {
    const { stub } = await open();

    const request = stub.requests.find(
      (item) => item.url.pathname === "/api/v1/intake/quarantines",
    );
    expect(request?.url.searchParams.get("resolved")).toBe("false");
    expect(request?.url.searchParams.get("limit")).toBe("200");
    expect(request?.url.searchParams.has("reason_code")).toBe(false);

    // 反向断言：**每一次**读取都必须带 `resolved`。CHG-0021 §2.5 之后省略它意味着
    // "不过滤（全部）"，于是"筛选器说只看未归属、列表却给了全部"只差一个省略。
    const listRequests = stub.requests.filter(
      (item) => item.url.pathname === "/api/v1/intake/quarantines",
    );
    expect(listRequests.length).toBeGreaterThan(0);
    expect(listRequests.every((item) => item.url.searchParams.has("resolved"))).toBe(true);

    const table = await screen.findByTestId("quarantine-table");
    expect(within(table).getByText("not_uniquely_matched")).toBeInTheDocument();
    // 三行来自同一个邮箱：邮箱是"去哪里找回那封信"的定位信息，不是唯一键。
    expect(within(table).getAllByText("submissions@example.edu")).toHaveLength(3);
    // 未处理两行（可归属的一条 + 不可归属的一条），已归属一行。
    expect(within(table).getAllByText("未处理")).toHaveLength(2);
    expect(within(table).getByText("已归属")).toBeInTheDocument();
    // 隔离记录不含信体：页面必须说明这一点（否则看起来像"内容被藏了"）。
    // 反向断言：界面上**不得**出现关于"对方实现到哪一步"的常驻断言（端点已实现，这类文案会变成假话）。
    expect(screen.queryByTestId("declared-contract-quarantine")).toBeNull();
    expect(screen.getByText(/不含附件内容、学生源码或提交的任何部分/)).toBeInTheDocument();
    stub.restore();
  });

  it("切到「已处理」后是新的一次查询（resolved=true），不是同一份缓存的复用", async () => {
    const { stub, user } = await open();

    await user.selectOptions(screen.getByTestId("quarantine-resolved-filter"), "true");
    await waitFor(() => {
      const last = stub.requests
        .filter((item) => item.url.pathname === "/api/v1/intake/quarantines")
        .at(-1);
      expect(last?.url.searchParams.get("resolved")).toBe("true");
    });
    expect(await screen.findByText("已归属")).toBeInTheDocument();
    stub.restore();
  });

  it("reason_code 筛选会随请求发出（契约声明的参数，不是摆设）", async () => {
    const { stub, user } = await open();

    await user.type(screen.getByLabelText(/原因码/), "not_uniquely_matched");
    await waitFor(() => {
      const last = stub.requests
        .filter((item) => item.url.pathname === "/api/v1/intake/quarantines")
        .at(-1);
      expect(last?.url.searchParams.get("reason_code")).toBe("not_uniquely_matched");
    });
    stub.restore();
  });

  it("超过一页时逐页追加，到末页给出终点（不静默丢记录）", async () => {
    const { stub, user } = await open({
      pages: [
        {
          items: [intakeQuarantineView({ intake_quarantine_id: TARGET })],
          next_cursor: "1",
        },
        {
          items: [
            intakeQuarantineView({
              intake_quarantine_id: ALREADY_RESOLVED,
              reason_code: "malformed_message",
              detail: "信封无法解析。",
            }),
          ],
          next_cursor: null,
        },
      ],
    });

    const table = await screen.findByTestId("quarantine-table");
    expect(within(table).getAllByRole("row")).toHaveLength(2); // 表头 + 1 行
    await user.click(screen.getByTestId("quarantines-load-more"));
    await waitFor(() =>
      expect(within(screen.getByTestId("quarantine-table")).getAllByRole("row")).toHaveLength(3),
    );
    expect(screen.getByTestId("quarantines-end")).toHaveTextContent("已到末页");
    stub.restore();
  });

  it("当前筛选下没有记录时说明「先确认筛选」，而不是让空表自己说话", async () => {
    const { stub } = await open({ pages: [intakeQuarantinePage([])] });

    expect(screen.getByTestId("quarantine-empty")).toHaveTextContent("没有隔离记录");
    expect(screen.getByTestId("quarantine-empty")).toHaveTextContent("先确认上面的处理状态筛选");
    stub.restore();
  });
});

describe("隔离记录归属（CHG-0014）", () => {
  async function openModal(options: Parameters<typeof quarantineRoutes>[0] = {}) {
    const opened = await open(options);
    await opened.user.click(screen.getByTestId(`resolve-${TARGET}`));
    await screen.findByTestId("resolve-modal");
    return opened;
  }

  it("提交体只含契约声明的 student_id/note，成功后该记录离开「未处理」列表", async () => {
    const { stub, user } = await openModal();

    await user.type(screen.getByTestId("resolve-student-id"), UUID_D);
    await user.type(screen.getByTestId("resolve-note"), "助教确认是张三的信。");
    await user.click(screen.getByTestId("submit-resolve"));

    const post = await waitFor(() => {
      const found = stub.requests.find((item) => item.method === "POST" &&
        item.url.pathname === `/api/v1/intake/quarantines/${TARGET}/resolve`);
      if (!found) throw new Error("尚未发出归属请求");
      return found;
    });
    // 契约 `additionalProperties: false`：只发 student_id 与 note，不夹带任何额外字段。
    expect(post.body).toEqual({
      student_id: UUID_D,
      note: "助教确认是张三的信。",
    });

    expect(await screen.findByTestId("quarantine-resolved-ok")).toHaveTextContent(
      `已归属（${UUID_D}）`,
    );
    // 归属后缓存失效并重新读取：该行变成"已归属"，归属按钮消失（不再重复归属）。
    await waitFor(() => {
      expect(screen.queryByTestId(`resolve-${TARGET}`)).toBeNull();
    });
    stub.restore();
  });

  it("note 留空时发送 null（契约的 note 可空），而不是空字符串", async () => {
    const { stub, user } = await openModal();

    await user.type(screen.getByTestId("resolve-student-id"), UUID_D);
    await user.click(screen.getByTestId("submit-resolve"));

    const post = await waitFor(() => {
      const found = stub.requests.find(
        (item) => item.method === "POST" && item.url.pathname.endsWith("/resolve"),
      );
      if (!found) throw new Error("尚未发出归属请求");
      return found;
    });
    expect(post.body).toEqual({ student_id: UUID_D, note: null });
    stub.restore();
  });

  it("学生 ID 为空或格式不是 UUID 时不发请求（避免注定 422 的调用）", async () => {
    const { stub, user } = await openModal();

    await user.click(screen.getByTestId("submit-resolve"));
    expect(await screen.findByText("请填写学生（平台用户 ID）。")).toBeInTheDocument();

    await user.type(screen.getByTestId("resolve-student-id"), "张三");
    await user.click(screen.getByTestId("submit-resolve"));
    expect(await screen.findByText(/必须是 UUID/)).toBeInTheDocument();

    expect(stub.requests.filter((item) => item.method === "POST" && item.url.pathname.endsWith("/resolve"))).toHaveLength(0);
    stub.restore();
  });

  it("服务端拒绝（409）原样展示，不假装归属成功", async () => {
    const { stub, user } = await openModal({
      resolve: () =>
        problemResponse({
          status: 409,
          title: "Conflict",
          detail: "This quarantine record was already resolved.",
        }),
    });

    await user.type(screen.getByTestId("resolve-student-id"), UUID_D);
    await user.click(screen.getByTestId("submit-resolve"));

    expect(await screen.findByTestId("resolve-conflict")).toBeInTheDocument();
    expect(screen.getByTestId("resolve-error")).toBeInTheDocument();
    expect(screen.queryByTestId("quarantine-resolved-ok")).toBeNull();
    stub.restore();
  });

  it("已处理的记录不再提供归属入口（重复归属只会换来 409）", async () => {
    const { stub } = await open();

    expect(screen.queryByTestId(`resolve-${ALREADY_RESOLVED}`)).toBeNull();
    expect(screen.getByTestId(`resolve-${TARGET}`)).toBeInTheDocument();
    stub.restore();
  });

  it("不可归属的原因码不显示归属按钮，并说明为什么（不让教师点了才拿 422）", async () => {
    const { stub } = await open();

    // CHG-0021 §2.1：只有 not_uniquely_matched / ambiguous_match 可归属；`archive_bomb`
    // 是内容类拒绝，人工补身份修不好它。界面不该用一次失败去表达一条已写下来的规则。
    expect(screen.queryByTestId(`resolve-${NOT_ATTRIBUTABLE}`)).toBeNull();
    const note = screen.getByTestId(`not-attributable-${NOT_ATTRIBUTABLE}`);
    expect(note).toHaveTextContent("不可归属");
    expect(note).toHaveTextContent("archive_bomb");
    expect(note).toHaveTextContent("422");

    // 可归属的那一行仍然有按钮：两者必须能被分开。
    expect(screen.getByTestId(`resolve-${TARGET}`)).toBeInTheDocument();
    stub.restore();
  });

  it("反向断言：不可归属的记录既没有按钮，点不到也不会有归属请求", async () => {
    const { stub } = await open();

    expect(screen.queryByTestId(`resolve-${NOT_ATTRIBUTABLE}`)).toBeNull();
    // 归属请求数为 0（没有任何路径能把这条记录送进归属流程）。
    expect(
      stub.requests.filter(
        (item) => item.method === "POST" && item.url.pathname.endsWith("/resolve"),
      ),
    ).toHaveLength(0);
    stub.restore();
  });
});

describe("学生选择器（B1 没有全量用户列表，候选来自名单）", () => {
  async function openPicker(options: Parameters<typeof quarantineRoutes>[0] = {}) {
    const { stub, user } = await open(options);
    await user.click(screen.getByTestId(`resolve-${TARGET}`));
    await screen.findByTestId("resolve-modal");
    await user.click(screen.getByTestId("toggle-student-picker"));
    await screen.findByTestId("student-picker");
    return { stub, user };
  }

  it("展开前不发课程请求；展开后按课程→课序→学生逐级取候选并填入 ID", async () => {
    const { stub, user } = await open({
      enrollments: [enrollmentPublic({ student_id: UUID_D, display_name: "张三" })],
    });

    expect(stub.requests.some((item) => item.url.pathname === "/api/v1/courses")).toBe(false);

    await user.click(screen.getByTestId(`resolve-${TARGET}`));
    await screen.findByTestId("resolve-modal");
    await user.click(screen.getByTestId("toggle-student-picker"));

    await user.selectOptions(await screen.findByLabelText("课程"), UUID_A);
    await user.selectOptions(await screen.findByLabelText("课序"), UUID_B);
    const studentSelect = await screen.findByTestId("picker-student");
    await waitFor(() => expect(within(studentSelect).getAllByRole("option")).toHaveLength(2));
    await user.selectOptions(studentSelect, UUID_D);

    // 选中的是**平台用户 ID**（契约要的键），不是姓名或学号。
    expect(screen.getByTestId("resolve-student-id")).toHaveValue(UUID_D);
    stub.restore();
  });

  it("名单里的行尚未绑定账号时不进候选，并如实说明（不伪造候选人）", async () => {
    const { stub, user } = await openPicker({
      enrollments: [
        enrollmentPublic({
          enrollment_id: "00000000-0000-4000-8000-0000000000f6",
          student_id: null,
          display_name: "李四",
          student_number: "20260002",
        }),
      ],
    });

    await user.selectOptions(await screen.findByLabelText("课程"), UUID_A);
    await user.selectOptions(await screen.findByLabelText("课序"), UUID_B);

    expect(await screen.findByTestId("student-picker-empty")).toHaveTextContent(
      "没有可归属的学生",
    );
    const studentSelect = screen.getByTestId("picker-student");
    // 只有占位项：未绑定账号的行没有 student_id，猜不出来也不该猜。
    expect(within(studentSelect).getAllByRole("option")).toHaveLength(1);
    stub.restore();
  });
});
