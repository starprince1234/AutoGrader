import { describe, expect, it } from "vitest";
import { useState } from "react";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../testing/renderWithProviders";
import { CaseEditor } from "./CaseEditor";
import { emptyCase, type CaseForm } from "./draft";

/**
 * 用例编辑器：分值仍是手输、退出码预置 0、高级项默认收起、名字可留空、
 * 复制/拖拽/删除二次确认都在这里固定下来。
 *
 * 组件是受控的，所以用一个最小壳持有数组状态——和真实页面里 `form.cases` 的角色一致。
 */
function Harness({ initial }: { initial: CaseForm[] }) {
  const [cases, setCases] = useState(initial);
  return <CaseEditor cases={cases} onChange={setCases} />;
}

function renderCases(initial: CaseForm[] = [emptyCase()]) {
  return renderWithProviders(<Harness initial={initial} />);
}

describe("分值与退出码", () => {
  it("分值仍是手输的数字输入框（不是下拉）", () => {
    renderCases();
    const weight = screen.getByLabelText("分值");
    expect(weight.tagName).toBe("INPUT");
    expect(weight).toHaveValue("1");
  });

  it("期望退出码预置为 0，收在高级配置里", async () => {
    const user = userEvent.setup();
    renderCases();

    // 默认收起：字段不在页面上。
    expect(screen.queryByLabelText("期望退出码")).toBeNull();
    await user.click(screen.getByTestId("case-advanced-0"));
    expect(screen.getByLabelText("期望退出码")).toHaveValue("0");
  });
});

describe("高级配置折叠", () => {
  it("默认收起，点一次展开（命令行参数 / 期望标准错误 / 退出码），再点收起", async () => {
    const user = userEvent.setup();
    renderCases();

    expect(screen.queryByTestId("case-advanced-fields-0")).toBeNull();
    expect(screen.getByTestId("case-advanced-0")).toHaveTextContent("展开高级配置");

    await user.click(screen.getByTestId("case-advanced-0"));
    expect(screen.getByTestId("case-advanced-fields-0")).toBeInTheDocument();
    expect(screen.getByLabelText("命令行参数（空格分隔）")).toBeInTheDocument();
    expect(screen.getByLabelText("期望标准错误")).toBeInTheDocument();
    expect(screen.getByTestId("case-advanced-0")).toHaveTextContent("收起高级配置");

    await user.click(screen.getByTestId("case-advanced-0"));
    expect(screen.queryByTestId("case-advanced-fields-0")).toBeNull();
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

describe("复制与排序", () => {
  it("复制用例：在原卡之后插入一份完整副本", async () => {
    const user = userEvent.setup();
    renderCases([{ ...emptyCase(), name: "基本用例", stdin: "a\n" }]);

    await user.click(screen.getByTestId("case-copy-0"));

    expect(screen.getByTestId("case-1")).toBeInTheDocument();
    expect(screen.getByTestId("case-1")).toHaveTextContent("基本用例");
    // 副本带着原来的标准输入：只改差异部分，不用全部重填。
    const stdins = screen.getAllByLabelText("标准输入") as HTMLTextAreaElement[];
    expect(stdins[0].value).toBe("a\n");
    expect(stdins[1].value).toBe("a\n");
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
