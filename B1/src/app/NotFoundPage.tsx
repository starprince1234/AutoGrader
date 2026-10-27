import { Link } from "@tanstack/react-router";
import { t } from "../i18n";
import { PageTitle } from "../ui/components";

/**
 * 未匹配的站内地址。路由器默认会渲染一段英文调试文本，这对课程用户没有意义；
 * 这里只说明"没有这个页面"并给一个真实可用的出口。
 */
export function NotFoundPage() {
  return (
    <div className="stack">
      <PageTitle>{t("notFound.title")}</PageTitle>
      <p className="muted">{t("notFound.body")}</p>
      <div className="inline">
        <Link className="button" to="/assignments">
          {t("notFound.back")}
        </Link>
      </div>
    </div>
  );
}
