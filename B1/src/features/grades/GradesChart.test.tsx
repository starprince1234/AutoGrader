import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import { GradesChart } from "./GradesChart";

/**
 * 分数分布图的尺寸跟随容器。
 *
 * ECharts 只在 `init` 时量一次容器，之后不会自己跟着窗口变化；没有显式 `resize()` 时，
 * 窗口放大后图仍停在旧宽度（右侧留白），窗口缩小时画布撑出容器（页面横向滚动）。
 */

const BUCKETS = [
  { label: "0-59%", count: 2 },
  { label: "60-69%", count: 1 },
];

function chartMock() {
  return { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  delete (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
});

describe("分数分布图跟随容器尺寸", () => {
  it("容器尺寸变化时调用 resize，并在卸载时断开监听、销毁实例", async () => {
    const chart = chartMock();
    const init = vi.fn(() => chart);
    const callbacks: Array<() => void> = [];
    class FakeResizeObserver {
      constructor(callback: ResizeObserverCallback) {
        callbacks.push(() => callback([], this as unknown as ResizeObserver));
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = FakeResizeObserver;

    const { unmount } = render(
      <GradesChart buckets={BUCKETS} loader={async () => ({ init })} />,
    );
    await waitFor(() => expect(init).toHaveBeenCalled());
    expect(callbacks).toHaveLength(1);

    callbacks[0]();
    expect(chart.resize).toHaveBeenCalledTimes(1);

    unmount();
    expect(chart.dispose).toHaveBeenCalledTimes(1);
  });

  it("没有 ResizeObserver 时退回 window.resize", async () => {
    const chart = chartMock();
    render(<GradesChart buckets={BUCKETS} loader={async () => ({ init: () => chart })} />);
    await waitFor(() => expect(chart.setOption).toHaveBeenCalled());

    window.dispatchEvent(new Event("resize"));
    expect(chart.resize).toHaveBeenCalledTimes(1);
  });
});
