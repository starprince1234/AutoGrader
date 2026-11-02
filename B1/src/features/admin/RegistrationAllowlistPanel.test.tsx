import { afterEach, describe, expect, it, vi } from "vitest";
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
import { sessionCreated } from "../../testing/contractFixtures";
import { signIn } from "../../auth/sessionStore";
import { RegistrationAllowlistPanel } from "./RegistrationAllowlistPanel";

/**
 * 注册名单标签：一个文件 = 一个班级，点开看学生，行内可删。
 *
 * 断言的是行为边界而不是外观：
 * - 「添加名单」直接开文件选择器，选中即上传（没有中转确认）；
 * - 班级列表显示文件名派生的班级名与人数；
 * - 展开后才读该班学生，且学生行可以单个删除；
 * - 删除班级是整体删除（连带名单行），界面上有明确文案。
 */

let stub: FetchStub | null = null;

afterEach(() => {
  stub?.restore();
  stub = null;
  vi.restoreAllMocks();
});

const BATCH = "11111111-1111-4111-8111-111111111111";
const ENTRY_A = "22222222-2222-4222-8222-222222222222";
const ENTRY_B = "33333333-3333-4333-8333-333333333333";

function csvFile(name = "软工01班.csv"): File {
  const content = "姓名,学号\n黄俊杰,2024141460192\n李四,2024141460193\n";
  return new File([content], name, { type: "text/csv" });
}

function routes(options: { batches?: unknown[]; entriesStatus?: number } = {}): StubbedRoute[] {
  return [
    {
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () => jsonResponse(sessionCreated({ roles: ["admin"], access_token: "t" })),
    },
    {
      method: "GET",
      path: "/api/v1/registration-allowlists/batches",
      respond: () =>
        jsonResponse({
          items: options.batches ?? [
            {
              batch_id: BATCH,
              class_name: "软工01班",
              filename: "软工01班.csv",
              row_count: 2,
              uploaded_at: "2026-10-29T12:00:00.000Z",
            },
          ],
        }),
    },
    {
      method: "GET",
      path: `/api/v1/registration-allowlists/batches/${BATCH}/entries`,
      respond: () =>
        options.entriesStatus === 500
          ? problemResponse({ status: 500, title: "Server Error", detail: "boom" })
          : jsonResponse({
              batch_id: BATCH,
              items: [
                { entry_id: ENTRY_A, student_number: "2024141460192", display_name: "黄俊杰" },
                { entry_id: ENTRY_B, student_number: "2024141460193", display_name: "李四" },
              ],
            }),
    },
    {
      method: "POST",
      path: "/api/v1/registration-allowlists",
      respond: () =>
        jsonResponse({
          batch_id: BATCH,
          class_name: "软工01班",
          row_count: 2,
          skipped_row_count: 0,
          duplicate_student_numbers: [],
          content_sha256: "a".repeat(64),
        }),
    },
    {
      method: "DELETE",
      path: `/api/v1/registration-allowlists/batches/${BATCH}`,
      respond: () => new Response(null, { status: 204 }),
    },
    {
      method: "DELETE",
      path: `/api/v1/registration-allowlists/entries/${ENTRY_A}`,
      respond: () => new Response(null, { status: 204 }),
    },
  ];
}

async function openPanel() {
  stub = installFetchStub(routes());
  await signIn("root", "pw");
  renderWithProviders(<RegistrationAllowlistPanel />);
  return screen.findByTestId("allowlist-panel");
}

describe("注册名单面板", () => {
  it("列出导入的班级：班级名来自文件名，显示人数", async () => {
    await openPanel();
    const list = await screen.findByTestId("allowlist-batches");
    expect(within(list).getByText("软工01班")).toBeInTheDocument();
    expect(screen.getByTestId(`allowlist-count-${BATCH}`)).toHaveTextContent("2 人");
  });

  it("没有班级时提示去添加名单", async () => {
    stub = installFetchStub(routes({ batches: [] }));
    await signIn("root", "pw");
    renderWithProviders(<RegistrationAllowlistPanel />);
    expect(await screen.findByTestId("allowlist-empty")).toHaveTextContent("添加名单");
  });

  it("添加名单：选中文件即上传，成功后提示班级与人数", async () => {
    const panel = await openPanel();
    const input = within(panel).getByTestId("allowlist-add") as HTMLInputElement;
    await userEvent.upload(input, csvFile());
    expect(await screen.findByTestId("allowlist-notice")).toHaveTextContent("已添加「软工01班」");
    const posted = stub?.requests.find(
      (request) =>
        request.method === "POST" &&
        request.url.pathname === "/api/v1/registration-allowlists",
    );
    expect(posted).toBeTruthy();
  });

  it("展开班级才读学生，并显示姓名与学号", async () => {
    await openPanel();
    await userEvent.click(await screen.findByTestId(`allowlist-expand-${BATCH}`));
    const entries = await screen.findByTestId(`allowlist-entries-${BATCH}`);
    expect(within(entries).getByText("黄俊杰")).toBeInTheDocument();
    expect(within(entries).getByText("2024141460192")).toBeInTheDocument();
    expect(within(entries).getByText("李四")).toBeInTheDocument();
  });

  it("学生行右侧的删除按钮发 DELETE，删的是这一个学生", async () => {
    await openPanel();
    await userEvent.click(await screen.findByTestId(`allowlist-expand-${BATCH}`));
    await screen.findByTestId(`allowlist-entries-${BATCH}`);
    await userEvent.click(screen.getByTestId(`allowlist-delete-entry-${ENTRY_A}`));
    await waitFor(() => {
      const deleted = stub?.requests.some(
        (request) =>
          request.method === "DELETE" &&
          request.url.pathname === `/api/v1/registration-allowlists/entries/${ENTRY_A}`,
      );
      expect(deleted).toBe(true);
    });
  });

  it("删除班级发 DELETE /batches/{id} 并提示已删除", async () => {
    await openPanel();
    await userEvent.click(await screen.findByTestId(`allowlist-delete-${BATCH}`));
    await waitFor(() => {
      const deleted = stub?.requests.some(
        (request) =>
          request.method === "DELETE" &&
          request.url.pathname === `/api/v1/registration-allowlists/batches/${BATCH}`,
      );
      expect(deleted).toBe(true);
    });
    expect(await screen.findByTestId("allowlist-notice")).toHaveTextContent("已删除班级");
  });

  it("学生读取失败时显示错误，而不是空的名单", async () => {
    stub = installFetchStub(routes({ entriesStatus: 500 }));
    await signIn("root", "pw");
    renderWithProviders(<RegistrationAllowlistPanel />);
    await userEvent.click(await screen.findByTestId(`allowlist-expand-${BATCH}`));
    expect(await screen.findByText(/boom|请求失败|错误/i)).toBeInTheDocument();
  });
});
