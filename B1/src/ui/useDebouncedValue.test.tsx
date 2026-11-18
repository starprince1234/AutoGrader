import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useDebouncedValue } from "./useDebouncedValue";

afterEach(() => {
  vi.useRealTimers();
});

describe("useDebouncedValue", () => {
  it("停手 delayMs 后才返回新值，期间保持旧值", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(
      ({ value }) => useDebouncedValue(value, 250),
      { initialProps: { value: "张" } },
    );
    expect(result.current).toBe("张");

    rerender({ value: "张三" });
    expect(result.current).toBe("张");

    act(() => vi.advanceTimersByTime(249));
    expect(result.current).toBe("张");

    act(() => vi.advanceTimersByTime(1));
    expect(result.current).toBe("张三");
  });

  it("连续输入只保留最后一次", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(
      ({ value }) => useDebouncedValue(value, 250),
      { initialProps: { value: "" } },
    );
    rerender({ value: "张" });
    act(() => vi.advanceTimersByTime(100));
    rerender({ value: "张三" });
    act(() => vi.advanceTimersByTime(100));
    rerender({ value: "张三丰" });
    act(() => vi.advanceTimersByTime(249));
    expect(result.current).toBe("");

    act(() => vi.advanceTimersByTime(1));
    expect(result.current).toBe("张三丰");
  });
});
