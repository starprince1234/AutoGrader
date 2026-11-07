import { describe, expect, it } from "vitest";
import { useState } from "react";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../testing/renderWithProviders";
import { CaseEditor } from "./CaseEditor";
import { emptyCase, type CaseForm } from "./draft";
import type { ExecutionMode } from "./execution";

/**
 * 用例编辑器：分值仍是手输、退出码预置 0、高级项默认收起、名字可留空、
 * 复制/拖拽/删除二次确认都在这里固定下来。
 *
 * 组件是受控的，所以用一个最小壳持有数组状态——和真实页面里 `form.cases` 的角色一致。
 */
function Harness({ initial, mode = "per_case" }: { initial: CaseForm[]; mode?: ExecutionMode }) {
  const [cases, setCases] = useState(initial);
  return <CaseEditor cases={cases} executionMode={mode} onChange={setCases} />;
}

function renderCases(initial: CaseForm[] = [emptyCase()], mode: ExecutionMode = "per_case") {
  return renderWithProviders(<Harness initial={initial} mode={mode} />);
}

describe("分值与退出码", () => {
  it("分值仍是手输的数字输入框（不是下拉），且不再预置 1", () => {
    renderCases();
    const weight = screen.getByLabelText("分值");
    expect(weight.tagName).toBe("INPUT");
    expect(weight).toHaveValue("");
  });

  it("不再有「展开高级配置」折叠面板（2.0 删除项）", () => {
    renderCases();
    expect(screen.queryByTestId("case-advanced-0")).toBeNull();
    expect(screen.queryByTestId("case-advanced-fields-0")).toBeNull();
    // 折叠区里的三项输入不再出现在页面上。
    expect(screen.queryByLabelText("命令行参数（空格分隔）")).toBeNull();
    expect(screen.queryByLabelText("期望标准错误")).toBeNull();
    expect(screen.queryByLabelText("期望退出码")).toBeNull();
  });
});

describe("用例名可以留空", () => {
  it("留空时卡片标题按序号显示「用例1」「用例2」", () => {
    renderCases([emptyCase(), emptyCase()]);
    expect(screen.getByTestId("case-0")).toHaveTextContent("用例1");
    expect(screen.getByTestId("case-1")).toHaveTextContent("用例2");
  });

  it("填了名字就用名字", async () => {
    const user = userEvent.setup();
    renderCases();
    await user.type(screen.getByLabelText(/用例名/), "边界情况");
    expect(screen.getByTestId("case-0")).toHaveTextContent("边界情况");
  });
});

describe("排序", () => {
  it("不再有「复制用例」按钮（2.0 删除项）", () => {
    renderCases([emptyCase(), emptyCase()]);
    expect(screen.queryByTestId("case-copy-0")).toBeNull();
    expect(screen.queryByTestId("case-copy-1")).toBeNull();
    // 删除仍然在，并且仍然走二次确认。
    expect(screen.getByTestId("case-remove-0")).toBeInTheDocument();
  });

  it("拖拽卡片即可调整顺序（顺序就是 ordinal）", async () => {
    renderCases([
      { ...emptyCase(), name: "第一个" },
      { ...emptyCase(), name: "第二个" },
    ]);

    // 把第 1 张拖到第 2 张的位置上。
    fireEvent.dragStart(screen.getByTestId("case-drag-0"));
    fireEvent.dragOver(screen.getByTestId("case-1"));
    fireEvent.drop(screen.getByTestId("case-1"));

    expect(screen.getByTestId("case-0")).toHaveTextContent("第二个");
    expect(screen.getByTestId("case-1")).toHaveTextContent("第一个");
  });
});

describe("删除要二次确认", () => {
  it("点删除只弹确认框，确认之后卡片才消失", async () => {
    const user = userEvent.setup();
    renderCases([emptyCase(), emptyCase()]);

    await user.click(screen.getByTestId("case-remove-0"));
    expect(screen.getByTestId("case-delete-confirm")).toBeInTheDocument();
    // 还没确认：两张卡都在。
    expect(screen.getByTestId("case-1")).toBeInTheDocument();

    await user.click(screen.getByTestId("case-delete-confirm-action"));
    expect(screen.queryByTestId("case-1")).toBeNull();
    expect(screen.getAllByRole("group")).toHaveLength(1);
  });

  it("取消删除时什么都不发生", async () => {
    const user = userEvent.setup();
    renderCases([emptyCase(), emptyCase()]);

    await user.click(screen.getByTestId("case-remove-1"));
    await user.click(screen.getByTestId("case-delete-cancel"));

    expect(screen.getByTestId("case-1")).toBeInTheDocument();
    expect(screen.queryByTestId("case-delete-confirm")).toBeNull();
  });
});

describe("两处标准输入要能区分", () => {
  it("用例内的标准输入带一个说明气泡", () => {
    renderCases();
    expect(screen.getByTestId("case-stdin-help-0")).toHaveAttribute(
      "title",
      "只对这一个用例生效",
    );
  });
});

describe("评分方式决定用例显示哪些执行声明（Unix 作业扩展）", () => {
  it("per_case：只有文件校验（可选），不显示 SSH / 邮件判定项", () => {
    renderCases();
    expect(screen.getByTestId("case-file-checks-0")).toBeInTheDocument();
    expect(screen.queryByLabelText("SSH 判定项")).toBeNull();
    expect(screen.queryByLabelText("邮件判定项")).toBeNull();
    // 文件校验为空在 per_case 下是合法的（可选），因此不应该有即时错误。
    expect(screen.queryByTestId("case-execution-errors-0")).toBeNull();
  });

  it("文件校验：摘要由本机 WebCrypto 计算并显示，内容原文不进表单断言", async () => {
    const user = userEvent.setup();
    renderCases();

    await user.click(screen.getByTestId("case-file-check-add-0"));
    await user.type(screen.getByLabelText("文件位置"), "week6_1.txt");
    await user.type(screen.getByLabelText("权限位"), "0744");
    await user.type(screen.getByLabelText("期望内容"), "abc");

    // 已知向量：SHA-256("abc")。内容本身不会出现在任何"已知摘要"之外的地方。
    expect(await screen.findByTestId("case-file-check-digest-0-0")).toHaveTextContent(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
    expect(screen.queryByTestId("case-execution-errors-0")).toBeNull();
  });

  it("文件校验：既没有期望内容也没有权限位时立即提示", () => {
    renderCases([{ ...emptyCase(), fileChecks: [{ path: "a.txt", mode: "", content: "", contentSha256: "" }] }]);
    expect(screen.getByTestId("case-execution-errors-0")).toHaveTextContent(
      "既没有期望内容，也没有权限位",
    );
  });

  it("single_run：开启「按分段评分」填分段序号，关闭后回到文件校验", async () => {
    const user = userEvent.setup();
    renderCases([emptyCase()], "single_run");

    // 默认未按分段：显示文件校验；空文件校验在 single_run 下立刻报错。
    expect(screen.getByTestId("case-file-checks-0")).toBeInTheDocument();
    expect(screen.getByTestId("case-execution-errors-0")).toHaveTextContent(
      "要么按分段评分，要么至少填一项文件校验",
    );

    await user.click(screen.getByTestId("case-segment-scored-0"));
    expect(screen.getByTestId("case-segment-0")).toBeInTheDocument();
    expect(screen.queryByTestId("case-file-checks-0")).toBeNull();

    await user.click(screen.getByTestId("case-segment-scored-0"));
    expect(screen.queryByTestId("case-segment-0")).toBeNull();
    expect(screen.getByTestId("case-file-checks-0")).toBeInTheDocument();
  });

  it("single_run：分段序号重复时在整组提示里说明，而不是归到某一张卡片", () => {
    renderCases(
      [
        { ...emptyCase(), name: "一", segmentScored: true, segment: "0" },
        { ...emptyCase(), name: "二", segmentScored: true, segment: "0" },
      ],
      "single_run",
    );
    expect(screen.getByTestId("case-execution-issues")).toHaveTextContent(
      "分段序号 0 被多个用例使用",
    );
  });

  it("ssh_session：每个用例选择一个判定项；重复时给出整组提示", async () => {
    const user = userEvent.setup();
    renderCases(
      [
        { ...emptyCase(), name: "一", sshCheck: "service_ready" },
        { ...emptyCase(), name: "二", sshCheck: "service_ready" },
      ],
      "ssh_session",
    );

    expect(screen.getAllByLabelText("SSH 判定项")).toHaveLength(2);
    expect(screen.getByTestId("case-execution-issues")).toHaveTextContent(
      "SSH 判定项「服务已就绪」被多个用例选择",
    );

    // 把第二条改成另一种判定：重复提示消失。
    await user.selectOptions(screen.getAllByLabelText("SSH 判定项")[1], "auth_succeeded");
    expect(screen.queryByTestId("case-execution-issues")).toBeNull();
  });

  it("email_delivery：判定项下拉出现，未选择时立即提示", () => {
    renderCases([emptyCase()], "email_delivery");
    expect(screen.getByLabelText("邮件判定项")).toBeInTheDocument();
    expect(screen.getByTestId("case-execution-errors-0")).toHaveTextContent(
      "要选择一个邮件判定项",
    );
  });
});
