import { describe, expect, it } from "vitest";
import { parseAssetUrl } from "./useQuestionAsset";

/**
 * `parseAssetUrl` 是一条安全边界：只有它认出的地址才会走"带令牌取字节 + 自建 blob URL"这条路，
 * 其余地址一律回到 `urlPolicy` 的白名单判定。因此这里逐条固定"什么算受控资产地址"。
 */

const QUESTION = "00000000-0000-4000-8000-0000000000c1";
const ASSET = "00000000-0000-4000-8000-0000000000c2";

describe("受控资产地址解析", () => {
  it("认得契约里的资产路径（相对与同源绝对两种写法）", () => {
    expect(parseAssetUrl(`/api/v1/questions/${QUESTION}/assets/${ASSET}`)).toEqual({
      questionId: QUESTION,
      assetId: ASSET,
    });
    expect(
      parseAssetUrl(`${window.location.origin}/api/v1/questions/${QUESTION}/assets/${ASSET}`),
    ).toEqual({ questionId: QUESTION, assetId: ASSET });
  });

  it("拒绝一切非资产地址（不让任何别的 URL 走带令牌的取字节路径）", () => {
    for (const candidate of [
      "",
      "/api/v1/questions",
      `/api/v1/questions/${QUESTION}/assets`,
      `/api/v1/questions/${QUESTION}/assets/${ASSET}/extra`,
      `/api/v1/question-versions/${ASSET}`,
      `/api/v1/questions/not-a-uuid/assets/${ASSET}`,
      "https://example.invalid/api/v1/questions/" + QUESTION + "/assets/" + ASSET,
      "blob:https://example.invalid/1234",
      "data:image/png;base64,AAAA",
      "//example.invalid/api/v1/questions/" + QUESTION + "/assets/" + ASSET,
      "img/logo.png",
    ]) {
      expect(parseAssetUrl(candidate), candidate).toBeNull();
    }
  });
});
