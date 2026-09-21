import { useEffect, useRef, useState } from "react";
import { t } from "../../i18n";
import type { ScoreBucket } from "./aggregate";
import { loadECharts, type EChartsInstance, type EChartsLoader } from "./echartsLoader";

/**
 * 分数分布图（ECharts 6）。
 *
 * 与 Monaco 同理：图表加载器**可注入**，失败时降级为纯文本表格，
 * 因此组件测试不依赖 canvas 这类不可控能力，且不会因为图表挂了而丢掉数据。
 */
export function GradesChart({
  buckets,
  loader = loadECharts,
}: {
  buckets: ScoreBucket[];
  loader?: EChartsLoader;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [failed, setFailed] = useState(false);
  // 0 行时画一张空坐标系只是占地方的装饰：没有数据就没有分布可言。
  const empty = buckets.every((bucket) => bucket.count === 0);

  useEffect(() => {
    let disposed = false;
    let chart: EChartsInstance | null = null;

    if (empty) return;

    // Colours come from the stylesheet instead of being hardcoded here: this chart was
    // written against the dark theme (#5b9dff bars, #2b303b grid lines) and kept drawing
    // bright blue bars over dark grid lines after B1 moved to the light Apple-style
    // palette. Reading the tokens makes the chart follow the theme -- including the
    // automatic dark-mode switch -- from one source of truth.
    const token = (name: string, fallback: string): string => {
      const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return value === "" ? fallback : value;
    };
    const accent = token("--color-accent", "#0071e3");
    const faint = token("--color-text-faint", "#86868b");
    const separator = token("--color-border", "#d2d2d7");

    void loader()
      .then(({ init }) => {
        if (disposed || !containerRef.current) return;
        chart = init(containerRef.current);
        chart.setOption({
          backgroundColor: "transparent",
          grid: { left: 40, right: 16, top: 24, bottom: 32 },
          xAxis: {
            type: "category",
            data: buckets.map((bucket) => bucket.label),
            axisLabel: { color: faint, fontSize: 12 },
            axisLine: { lineStyle: { color: separator } },
            axisTick: { show: false },
          },
          yAxis: {
            type: "value",
            minInterval: 1,
            axisLabel: { color: faint, fontSize: 12 },
            axisLine: { show: false },
            // Grid lines are separators, not content: as light as the hairlines used
            // elsewhere, so a single bar does not look sliced by them.
            splitLine: { lineStyle: { color: separator, width: 1 } },
          },
          series: [
            {
              type: "bar",
              data: buckets.map((bucket) => bucket.count),
              barMaxWidth: 48,
              itemStyle: { color: accent, borderWidth: 0, borderRadius: [6, 6, 0, 0] },
            },
          ],
        });
      })
      .catch(() => {
        if (!disposed) setFailed(true);
      });

    return () => {
      disposed = true;
      chart?.dispose();
    };
  }, [buckets, loader, empty]);

  if (empty) {
    return (
      <p className="muted" data-testid="distribution-empty">
        {t("common.empty")}
      </p>
    );
  }

  if (failed) {
    return (
      <table className="table" data-testid="distribution-table">
        <tbody>
          {buckets.map((bucket) => (
            <tr key={bucket.label}>
              <th scope="row">{bucket.label}</th>
              <td>{bucket.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }

  return (
    <div
      className="chart"
      ref={containerRef}
      data-testid="distribution-chart"
      role="img"
      aria-label={t("grades.distribution")}
    />
  );
}
