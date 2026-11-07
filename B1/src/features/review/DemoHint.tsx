import { t } from "../../i18n";

/**
 * 空态里的示例入口。
 *
 * 老师第一次打开复核页时，看到的往往就是"没有待处理记录 / 暂无评测记录"——页头的「查看示例」
 * 是 ghost 按钮，很容易被当成分页或返回类操作。于是空态本身要说一句"可以先看样例"，
 * 点一下就是同一个 demo 模式（不复制界面、不发请求）。
 */
export function DemoHint({ onShow, testId }: { onShow: () => void; testId: string }) {
  return (
    <p className="muted" data-testid={testId}>
      {t("review.demoEmptyHint")}{" "}
      <button
        type="button"
        data-testid={`${testId}-action`}
        onClick={onShow}
        style={{
          border: 0,
          background: "none",
          padding: 0,
          font: "inherit",
          color: "inherit",
          cursor: "pointer",
          textDecoration: "underline",
        }}
      >
        {t("review.demoEnter")}
      </button>
    </p>
  );
}
