import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../testing/renderWithProviders";
import {
  installFetchStub,
  jsonResponse,
  problemResponse,
  type StubbedRoute,
} from "../../testing/httpBoundary";
import { UUID_RUN, appealPack, sessionCreated } from "../../testing/contractFixtures";
import { signIn, resetSessionForTests } from "../../auth/sessionStore";
import { AppealPackPanel } from "./AppealPackPanel";
import { appealPackPath } from "./useAppealPack";
import type { AppealPack, Role } from "../../api/types";

/**
 * 申诉证据包（CHG-0014）。
 *
 * 断言的是契约里那三件"不能只有界面、必须真的做到"的事：
 * 1. `view` **显式**发送（默认值是 `teacher`，学生沿用默认值就是以教师视图请求）；
 * 2. `redactions` 逐条原文呈现（"移除了什么、为什么"，不是暗示）；
 * 3. 下载的是**响应体原字节**（夹具刻意带缩进：一旦被重新序列化，字节数就对不上）。
 */

/**
 * 带缩进的响应体：与 `JSON.stringify(pack)` 的紧凑形式长度不同，
 * 因此"原字节下载"与"重新序列化"可以被区分开。
 */
function indentedPackBody(overrides?: Partial<AppealPack>): string {
  return JSON.stringify(appealPack(overrides), null, 2);
}

function appealRoutes(
  role: Role | null,
  options: { body?: string; respond?: () => Response } = {},
): StubbedRoute[] {
  const routes: StubbedRoute[] = [];
  if (role !== null) {
    routes.push({
      method: "POST",
      path: "/api/v1/auth/session",
      respond: () => jsonResponse(sessionCreated({ roles: [role], access_token: "t" })),
    });
  }
  routes.push({
    method: "GET",
    path: appealPackPath(UUID_RUN),
    respond: () =>
      options.respond?.() ??
      new Response(options.body ?? indentedPackBody(), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
  });
  return routes;
}

let createdBlobs: Blob[];

beforeEach(() => {
  createdBlobs = [];
  URL.createObjectURL = vi.fn((blob: Blob) => {
    createdBlobs.push(blob);
    return "blob:appeal";
  }) as unknown as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn() as unknown as typeof URL.revokeObjectURL;
});

afterEach(() => {
  vi.restoreAllMocks();
});

async function open(
  role: Role | null,
  options: { body?: string; respond?: () => Response } = {},
) {
  const stub = installFetchStub(appealRoutes(role, options));
  const user = userEvent.setup();
  if (role !== null) await signIn(`${role}1`, "pw");
  renderWithProviders(<AppealPackPanel runId={UUID_RUN} />);
  return { stub, user };
}

describe("申诉证据包（CHG-0014）", () => {
  it("教师加载后：view 显式发送 teacher、redactions 逐条原文、result_digest 可见", async () => {
    const { stub, user } = await open("teacher");

    await user.click(screen.getByTestId("load-appeal-pack"));
    await screen.findByTestId("appeal-pack-content");

    const request = stub.requests.find((item) => item.url.pathname === appealPackPath(UUID_RUN));
    // 契约给 `view` 声明了 default: teacher —— 必须显式发送，不能依赖服务端默认值。
    expect(request?.url.searchParams.get("view")).toBe("teacher");

    const redactions = screen.getByTestId("appeal-redactions");
    expect(redactions).toHaveTextContent("已移除宿主机文件系统绝对路径（描述内部布局）。");
    expect(redactions).toHaveTextContent(
      "未包含学生视图之外的受保护内容（隐藏用例与期望输出）。",
    );
    expect(screen.getByTestId("appeal-result-digest")).toHaveTextContent("4".repeat(64));
    // 重现说明与运行策略摘要：证据包要能脱离本部署被读懂。
    expect(screen.getByTestId("appeal-replay")).toHaveTextContent("result_digest");
    expect(screen.getByTestId("appeal-policy-summary")).toHaveTextContent("read_only_root");
    stub.restore();
  });

  it("学生以自己的视图请求（不沿用默认的 teacher）", async () => {
    const { stub, user } = await open("student");

    await user.click(screen.getByTestId("load-appeal-pack"));
    await screen.findByTestId("appeal-pack-content");

    const request = stub.requests.find((item) => item.url.pathname === appealPackPath(UUID_RUN));
    expect(request?.url.searchParams.get("view")).toBe("student");
    expect(screen.getByTestId("appeal-view")).toHaveTextContent("学生视图");
    stub.restore();
  });

  it("角色未知（未登录）时不发送任何请求，并说明原因", async () => {
    // 会话是模块级单例：先清掉前面用例建立的身份，否则这里拿到的是上一个用例的角色。
    resetSessionForTests();
    const { stub } = await open(null);

    expect(screen.getByTestId("appeal-view-unknown")).toHaveTextContent("不发送任何请求");
    expect(screen.getByTestId("load-appeal-pack")).toBeDisabled();
    expect(
      stub.requests.filter((item) => item.url.pathname === appealPackPath(UUID_RUN)),
    ).toHaveLength(0);
    stub.restore();
  });

  it("下载的是响应体原字节（不重新序列化、不重排字段）", async () => {
    const body = indentedPackBody();
    const { stub, user } = await open("teacher", { body });

    await user.click(screen.getByTestId("load-appeal-pack"));
    await screen.findByTestId("appeal-pack-content");
    await user.click(screen.getByTestId("download-appeal-pack"));

    expect(createdBlobs).toHaveLength(1);
    const blob = createdBlobs[0];
    // 字节数与响应对齐（缩进形式比紧凑形式长，重新序列化会立刻露馅）。
    expect(blob.size).toBe(new TextEncoder().encode(body).length);
    expect(await blob.text()).toBe(body);
    // 下载只发一次请求：显示与下载用的是同一份字节。
    expect(
      stub.requests.filter((item) => item.url.pathname === appealPackPath(UUID_RUN)),
    ).toHaveLength(1);
    expect(screen.getByTestId("appeal-downloaded")).toHaveTextContent(
      `appeal-pack-${UUID_RUN}.json`,
    );
    stub.restore();
  });

  it("服务端拒绝（403）时原样展示失败，不给出伪造的证据包", async () => {
    const { stub, user } = await open("student", {
      respond: () =>
        problemResponse({
          status: 403,
          title: "Forbidden",
          detail: "This run is not in your scope.",
        }),
    });

    await user.click(screen.getByTestId("load-appeal-pack"));
    expect(await screen.findByTestId("appeal-error")).toBeInTheDocument();
    expect(screen.queryByTestId("appeal-pack-content")).toBeNull();
    expect(screen.queryByTestId("download-appeal-pack")).toBeNull();
    stub.restore();
  });

  it("409/404 等失败不产生可下载文件", async () => {
    const { stub, user } = await open("teacher", {
      respond: () =>
        problemResponse({
          status: 409,
          title: "Conflict",
          detail: "The run has no completed report yet.",
        }),
    });

    await user.click(screen.getByTestId("load-appeal-pack"));
    await waitFor(() => expect(screen.getByTestId("appeal-error")).toBeInTheDocument());
    expect(createdBlobs).toHaveLength(0);
    expect(screen.queryByTestId("appeal-pack-content")).toBeNull();
    stub.restore();
  });
});
