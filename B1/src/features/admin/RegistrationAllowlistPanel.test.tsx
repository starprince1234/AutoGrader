import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
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
  allowlistReplacement,
  allowlistState,
  sessionCreated,
} from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { RegistrationAllowlistPanel } from "./RegistrationAllowlistPanel";

/**
 * 注册名单标签：一次上传就是一次**全量替换**。
 *
 * 这里断言的是副作用边界：选择文件不算上传、打开确认框不算上传、取消不发请求、
 * 失败不改变当前生效批次、连接中断不宣称成功也不自动重发。
 */

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
  vi.restoreAllMocks();
});

const CSV = "姓名,学号\n张三,2026000000001\n李四,2026000000002\n";

function csvFile(name = "allowlist-new.csv"): File {
  return new File([CSV], name, { type: "text/csv" });
}

function withSession(routes: StubbedRoute[]): StubbedRoute[] {
  return [
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () =>
        jsonResponse(
          sessionCreated({ roles: ["admin"], scope: [{ course_id: UUID_A, section_id: UUID_B }] }),
        ),
    },
    ...routes,
  ];
}

function uploadCount(): number {
  const requests = stub?.requests ?? [];
  return requests.filter(
    (request) =>
      request.method === "POST" && request.url.pathname === "/api/v1/registration-allowlists",
  ).length;
}

describe("注册名单：当前批次", () => {
  it("显示文件名、人数与导入时间，数字来自服务端响应", async () => {
    stub = installFetchStub(
      withSession([
        { method: "GET", path: "/api/v1/registration-allowlists", respond: () => jsonResponse(allowlistState()) },
      ]),
    );
    await signIn("admin1", "pw");
    renderWithProviders(<RegistrationAllowlistPanel />);

    const batch = await screen.findByTestId("allowlist-current");
    expect(batch).toHaveTextContent("allowlist-2026.csv");
    expect(screen.getByTestId("allowlist-row-count")).toHaveTextContent("36");
    expect(screen.queryByTestId("allowlist-empty")).toBeNull();
  });

  it("零批次显示「尚未导入注册名单」，选文件前「继续」禁用", async () => {
    stub = installFetchStub(
      withSession([
        {
          method: "GET",
          path: "/api/v1/registration-allowlists",
          respond: () =>
            jsonResponse({ batch_id: null, filename: null, row_count: 0, uploaded_at: null }),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<RegistrationAllowlistPanel />);

    expect(await screen.findByTestId("allowlist-empty")).toHaveTextContent(
      "尚未导入注册名单。",
    );
    const submit = screen.getByTestId("allowlist-continue");
    expect(submit).toBeDisabled();

    await user.upload(screen.getByTestId("allowlist-file"), csvFile());
    expect(screen.getByTestId("allowlist-continue")).toBeEnabled();
    expect(screen.getByTestId("allowlist-selected")).toHaveTextContent("allowlist-new.csv");
    // 选文件不是上传。
    expect(uploadCount()).toBe(0);
  });

  it("读取失败显示错误并可重试，不把失败说成「没有批次」", async () => {
    stub = installFetchStub(
      withSession([
        {
          method: "GET",
          path: "/api/v1/registration-allowlists",
          respond: () =>
            problemResponse({ status: 503, title: "Service Unavailable", detail: "database is unavailable" }),
        },
      ]),
    );
    await signIn("admin1", "pw");
    renderWithProviders(<RegistrationAllowlistPanel />);

    expect(await screen.findByTestId("allowlist-load-error")).toBeInTheDocument();
    expect(screen.queryByTestId("allowlist-empty")).toBeNull();
    expect(screen.getByTestId("allowlist-retry-load")).toBeInTheDocument();
  });

  it("读取 403 显示权限问题", async () => {
    stub = installFetchStub(
      withSession([
        {
          method: "GET",
          path: "/api/v1/registration-allowlists",
          respond: () =>
            problemResponse({ status: 403, title: "Forbidden", detail: "administrator only" }),
        },
      ]),
    );
    await signIn("admin1", "pw");
    renderWithProviders(<RegistrationAllowlistPanel />);

    expect(await screen.findByTestId("allowlist-forbidden")).toBeInTheDocument();
    expect(screen.queryByTestId("allowlist-empty")).toBeNull();
  });
});

describe("注册名单：全量替换确认", () => {
  it("只接受 CSV：选 XLSX 提示「注册名单使用 CSV 文件」且不发请求", async () => {
    stub = installFetchStub(
      withSession([
        { method: "GET", path: "/api/v1/registration-allowlists", respond: () => jsonResponse(allowlistState()) },
      ]),
    );
    await signIn("admin1", "pw");
    renderWithProviders(<RegistrationAllowlistPanel />);

    await screen.findByTestId("allowlist-current");
    // `user.upload` 会按 `accept` 过滤掉不匹配的文件，而这里要验的正是"用户绕过选择框
    // 塞进来一个 XLSX"时页面自己的判断，所以直接触发 change。
    fireEvent.change(screen.getByTestId("allowlist-file"), {
      target: {
        files: [
          new File(["x"], "roster.xlsx", {
            type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          }),
        ],
      },
    });

    expect(screen.getByText("注册名单使用 CSV 文件。")).toBeInTheDocument();
    expect(screen.getByTestId("allowlist-continue")).toBeDisabled();
    expect(screen.queryByTestId("allowlist-selected")).toBeNull();
    expect(uploadCount()).toBe(0);
  });

  it("确认框写明当前批次、文件名与替换影响；打开确认框不发请求", async () => {
    stub = installFetchStub(
      withSession([
        { method: "GET", path: "/api/v1/registration-allowlists", respond: () => jsonResponse(allowlistState()) },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<RegistrationAllowlistPanel />);

    await screen.findByTestId("allowlist-current");
    await user.upload(screen.getByTestId("allowlist-file"), csvFile());
    await user.click(screen.getByTestId("allowlist-continue"));

    const modal = await screen.findByTestId("allowlist-confirm-modal");
    expect(within(modal).getByTestId("allowlist-confirm-current")).toHaveTextContent(
      "allowlist-2026.csv",
    );
    expect(within(modal).getByTestId("allowlist-confirm-current")).toHaveTextContent("36 人");
    expect(within(modal).getByTestId("allowlist-confirm-file")).toHaveTextContent(
      "allowlist-new.csv",
    );
    expect(within(modal).getByText(/文件中没有的学号将不能注册/)).toBeInTheDocument();
    expect(uploadCount()).toBe(0);
  });

  it("取消不发 POST，保留已选文件与当前批次", async () => {
    stub = installFetchStub(
      withSession([
        { method: "GET", path: "/api/v1/registration-allowlists", respond: () => jsonResponse(allowlistState()) },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<RegistrationAllowlistPanel />);

    await screen.findByTestId("allowlist-current");
    await user.upload(screen.getByTestId("allowlist-file"), csvFile());
    await user.click(screen.getByTestId("allowlist-continue"));
    await user.click(await screen.findByTestId("allowlist-cancel"));

    expect(screen.queryByTestId("allowlist-confirm-modal")).toBeNull();
    expect(uploadCount()).toBe(0);
    expect(screen.getByTestId("allowlist-selected")).toHaveTextContent("allowlist-new.csv");
    expect(screen.getByTestId("allowlist-current")).toHaveTextContent("allowlist-2026.csv");
  });

  it("确认后只发一个 multipart 请求，成功后显示服务端行数并刷新批次", async () => {
    let replaced = false;
    const pending: { resolve: (() => void) | null } = { resolve: null };
    stub = installFetchStub(
      withSession([
        {
          method: "GET",
          path: "/api/v1/registration-allowlists",
          respond: () => {
            if (!replaced) return jsonResponse(allowlistState());
            return jsonResponse(
              allowlistState({
                batch_id: UUID_B,
                filename: "allowlist-new.csv",
                row_count: 38,
                uploaded_at: "2026-09-16T02:00:00.000Z",
              }),
            );
          },
        },
        {
          method: "POST",
          path: "/api/v1/registration-allowlists",
          respond: ({ request }) => {
            // 请求体必须是 multipart 且带上了所选文件。
            expect(request.formData?.get("file")).toBeInstanceOf(File);
            expect((request.formData?.get("file") as File).name).toBe("allowlist-new.csv");
            return new Promise<Response>((resolve) => {
              pending.resolve = () => {
                replaced = true;
                resolve(jsonResponse(allowlistReplacement()));
              };
            });
          },
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<RegistrationAllowlistPanel />);

    await screen.findByTestId("allowlist-current");
    await user.upload(screen.getByTestId("allowlist-file"), csvFile());
    await user.click(screen.getByTestId("allowlist-continue"));
    await user.click(await screen.findByTestId("allowlist-confirm"));

    // pending 期间第二次点击不得再造一个批次。
    await user.click(screen.getByTestId("allowlist-confirm"));
    expect(uploadCount()).toBe(1);
    expect(screen.getByTestId("allowlist-pending")).toHaveTextContent("正在替换…");

    pending.resolve?.();

    expect(await screen.findByTestId("allowlist-notice")).toHaveTextContent("本次 38 行");
    await waitFor(() =>
      expect(screen.getByTestId("allowlist-current")).toHaveTextContent("allowlist-new.csv"),
    );
    await waitFor(() =>
      expect(screen.getByTestId("allowlist-row-count")).toHaveTextContent("38"),
    );
    expect(uploadCount()).toBe(1);
  });

  it("422 显示服务端原因、保留文件选择与当前批次，不显示成功", async () => {
    stub = installFetchStub(
      withSession([
        { method: "GET", path: "/api/v1/registration-allowlists", respond: () => jsonResponse(allowlistState()) },
        {
          method: "POST",
          path: "/api/v1/registration-allowlists",
          respond: () =>
            problemResponse({
              status: 422,
              title: "Unprocessable Entity",
              detail: "第 3 行的学号不是 13 位数字。",
            }),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<RegistrationAllowlistPanel />);

    await screen.findByTestId("allowlist-current");
    await user.upload(screen.getByTestId("allowlist-file"), csvFile());
    await user.click(screen.getByTestId("allowlist-continue"));
    await user.click(await screen.findByTestId("allowlist-confirm"));

    const invalid = await screen.findByTestId("allowlist-error-invalid");
    expect(invalid).toHaveTextContent("第 3 行的学号不是 13 位数字。");
    expect(screen.queryByTestId("allowlist-notice")).toBeNull();
    // 当前生效批次没有被改，文件选择也还在，可以修正后重新确认。
    expect(screen.getByTestId("allowlist-current")).toHaveTextContent("allowlist-2026.csv");
    expect(screen.getByTestId("allowlist-selected")).toHaveTextContent("allowlist-new.csv");
    expect(screen.getByTestId("allowlist-continue")).toBeEnabled();
  });

  it("403 报权限不足而不是文件格式错", async () => {
    stub = installFetchStub(
      withSession([
        { method: "GET", path: "/api/v1/registration-allowlists", respond: () => jsonResponse(allowlistState()) },
        {
          method: "POST",
          path: "/api/v1/registration-allowlists",
          respond: () =>
            problemResponse({ status: 403, title: "Forbidden", detail: "administrator only" }),
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<RegistrationAllowlistPanel />);

    await screen.findByTestId("allowlist-current");
    await user.upload(screen.getByTestId("allowlist-file"), csvFile());
    await user.click(screen.getByTestId("allowlist-continue"));
    await user.click(await screen.findByTestId("allowlist-confirm"));

    expect(await screen.findByTestId("allowlist-error-forbidden")).toBeInTheDocument();
    expect(screen.queryByTestId("allowlist-error-invalid")).toBeNull();
    expect(screen.queryByTestId("allowlist-notice")).toBeNull();
  });

  it("连接中断不宣称成功也不自动重发：提示状态待确认并可刷新当前名单", async () => {
    stub = installFetchStub(
      withSession([
        { method: "GET", path: "/api/v1/registration-allowlists", respond: () => jsonResponse(allowlistState()) },
        {
          method: "POST",
          path: "/api/v1/registration-allowlists",
          respond: () => {
            throw new TypeError("network error");
          },
        },
      ]),
    );
    const user = userEvent.setup();
    await signIn("admin1", "pw");
    renderWithProviders(<RegistrationAllowlistPanel />);

    await screen.findByTestId("allowlist-current");
    await user.upload(screen.getByTestId("allowlist-file"), csvFile());
    await user.click(screen.getByTestId("allowlist-continue"));
    await user.click(await screen.findByTestId("allowlist-confirm"));

    const uncertain = await screen.findByTestId("allowlist-uncertain");
    expect(uncertain).toHaveTextContent("替换结果未确认");
    expect(screen.queryByTestId("allowlist-notice")).toBeNull();

    const before = stub.requests.filter(
      (request) => request.method === "GET" && request.url.pathname === "/api/v1/registration-allowlists",
    ).length;
    await user.click(within(uncertain).getByRole("button"));

    await waitFor(() =>
      expect(
        stub?.requests.filter(
          (request) =>
            request.method === "GET" &&
            request.url.pathname === "/api/v1/registration-allowlists",
        ).length,
      ).toBe(before + 1),
    );
    // 不自动重放写请求。
    expect(uploadCount()).toBe(1);
  });
});
