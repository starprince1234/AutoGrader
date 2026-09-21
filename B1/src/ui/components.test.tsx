import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "../api/problem";
import { CONTRACT_VERSION } from "../app/contractVersion";
import { ErrorState, Modal, Panel, Table } from "./components";

/**
 * UI 原语的行为测试（WCAG 2.2 AA 的最小可验证集 + 诊断信息复制）。
 * 这些结论无法靠静态检查证明：焦点是否真的移动、Tab 是否真的被关住、
 * 剪贴板不可用时是否真的没有假装成功。
 */

afterEach(() => {
  vi.restoreAllMocks();
});

/** 打开/关闭弹窗的真实组件：焦点归还必须有真实的触发元素。 */
function TriggerAndModal() {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" data-testid="trigger-toggle" onClick={() => setOpen(true)}>
        打开弹窗
      </button>
      {open ? (
        <Modal title="标题" onClose={() => setOpen(false)} testId="dialog">
          <p>内容</p>
        </Modal>
      ) : null}
    </div>
  );
}

describe("弹窗的可访问性（WCAG 2.2）", () => {
  function openModal() {
    const onClose = vi.fn();
    render(
      <div>
        <button type="button" data-testid="trigger">
          打开
        </button>
        <Modal title="评测证据下钻" onClose={onClose} testId="dialog">
          <button type="button">弹窗内按钮</button>
        </Modal>
      </div>,
    );
    return onClose;
  }

  it("打开后焦点进入弹窗，Escape 关闭并触发 onClose", async () => {
    const user = userEvent.setup();
    const onClose = openModal();

    const dialog = screen.getByTestId("dialog");
    await waitFor(() => expect(dialog).toHaveFocus());
    expect(dialog).toHaveAttribute("aria-modal", "true");
    // 标题通过 aria-labelledby 关联，而不是仅在 title 是字符串时才有名字
    expect(dialog).toHaveAccessibleName("评测证据下钻");

    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Tab 被关在弹窗内：正反两个方向都在弹窗元素之间循环", async () => {
    const user = userEvent.setup();
    openModal();

    const dialog = screen.getByTestId("dialog");
    const closeButton = screen.getByRole("button", { name: "关闭" });
    const innerButton = screen.getByRole("button", { name: "弹窗内按钮" });

    // 从弹窗本体出发的 Shift+Tab 落到最后一个可聚焦元素
    dialog.focus();
    await user.tab({ shift: true });
    expect(innerButton).toHaveFocus();

    // 正向越过最后一个元素回到第一个
    await user.tab();
    expect(closeButton).toHaveFocus();
    expect(screen.getByTestId("trigger")).not.toHaveFocus();
  });

  it("关闭后焦点归还给触发元素（不是丢回页面开头）", async () => {
    const user = userEvent.setup();
    render(<TriggerAndModal />);

    await user.click(screen.getByTestId("trigger-toggle"));
    await waitFor(() => expect(screen.getByTestId("dialog")).toHaveFocus());
    await user.click(screen.getByRole("button", { name: "关闭" }));

    expect(screen.getByTestId("trigger-toggle")).toHaveFocus();
  });
});

describe("面板与表格的可访问性", () => {
  it("titleLevel=1 的面板渲染 h1（页面标题），默认渲染 h2（分节标题）", () => {
    render(
      <div>
        <Panel title="作答与提交" titleLevel={1}>
          <p>内容</p>
        </Panel>
        <Panel title="查询条件">
          <p>内容</p>
        </Panel>
      </div>,
    );

    expect(screen.getByRole("heading", { level: 1, name: "作答与提交" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "查询条件" })).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("排序声明输出 aria-sort，未声明的列没有该属性", () => {
    render(
      <Table<{ id: string; name: string }>
        testId="table"
        label="示例表"
        rows={[{ id: "1", name: "张三" }]}
        getRowKey={(row) => row.id}
        columns={[
          { key: "name", header: "姓名", render: (row) => row.name, sort: "ascending" },
          { key: "id", header: "ID", render: (row) => row.id, sort: "none" },
        ]}
      />,
    );

    expect(screen.getByRole("table", { name: "示例表" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "姓名" })).toHaveAttribute(
      "aria-sort",
      "ascending",
    );
    expect(screen.getByRole("columnheader", { name: "ID" })).toHaveAttribute("aria-sort", "none");
  });
});

describe("一键复制诊断信息（RQ-B-1-99 验收 2）", () => {
  function apiError() {
    return new ApiError({
      status: 404,
      problem: {
        type: "https://autograder.local/problems/not-found",
        title: "Not Found",
        status: 404,
        detail: "报告尚不可读，请联系 zhangsan@example.edu",
        instance: "/api/v1/evaluation-runs/x/report",
      },
      requestId: "0c3fb6d6-4f3a-4b1e-9d0f-1a2b3c4d5e6f",
      url: "/api/v1/evaluation-runs/x/report",
    });
  }

  it("剪贴板可用时复制出 request_id + ProblemDetails + 契约版本", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn(async (_text: string) => {});
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(<ErrorState error={apiError()} />);
    await user.click(screen.getByTestId("copy-diagnostics"));

    expect(await screen.findByTestId("diagnostics-copied")).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledTimes(1);
    const copied = JSON.parse(writeText.mock.calls[0][0]) as Record<string, unknown>;
    expect(copied.request_id).toBe("0c3fb6d6-4f3a-4b1e-9d0f-1a2b3c4d5e6f");
    expect(copied.contract_version).toBe(CONTRACT_VERSION);
    expect(copied.problem).toMatchObject({ status: 404, title: "Not Found" });
    // 服务端 detail 里的邮箱不得原样被带走（复用 redaction，而不是手拼字符串）
    expect(JSON.stringify(copied)).not.toContain("zhangsan@example.edu");
    expect(JSON.stringify(copied)).toContain("[redacted]");
  });

  it("剪贴板不可用时给出可手动选择的文本，而不是假装复制成功", async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });

    render(<ErrorState error={apiError()} />);
    await user.click(screen.getByTestId("copy-diagnostics"));

    expect(screen.queryByTestId("diagnostics-copied")).toBeNull();
    const textarea = await screen.findByTestId("diagnostics-text");
    expect((textarea as HTMLTextAreaElement).value).toContain('"contract_version"');
    expect((textarea as HTMLTextAreaElement).value).toContain(
      '"request_id": "0c3fb6d6-4f3a-4b1e-9d0f-1a2b3c4d5e6f"',
    );
  });
});
