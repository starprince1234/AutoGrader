/**
 * ECharts 的加载（非组件模块，便于 Fast Refresh 与测试注入）。
 * 只导出加载器与类型，组件在 `GradesChart.tsx`。
 */

export type EChartsInstance = {
  setOption: (option: unknown) => void;
  resize: () => void;
  dispose: () => void;
};

export type EChartsLoader = () => Promise<{
  init: (element: HTMLElement) => EChartsInstance;
}>;

export const loadECharts: EChartsLoader = async () => {
  const echarts = await import("echarts");
  return { init: (element) => echarts.init(element) as unknown as EChartsInstance };
};
