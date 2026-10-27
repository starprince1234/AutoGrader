import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom 未实现滚动 API；路由器导航会调用它，测试里静默为无操作即可。
window.scrollTo = () => {};

afterEach(() => {
  cleanup();
});
