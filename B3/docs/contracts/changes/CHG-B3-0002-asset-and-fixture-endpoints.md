# CHG-B3-0002 资产 / fixture 的上传与读取端点未成文

- **提出方**：B3（WP-B3-A）
- **日期**：2026-09-15
- **状态**：**已裁决并生效（MAIN，2026-09-18）：四个操作全部成文；B3 实现未变**
- **规程**：`../../../MAIN/docs/contracts/change-protocol.md`

## 1. 观察到的差异

| 侧 | 内容 |
|---|---|
| 需求侧（权威） | `RQ-B-3-10` 验收 1/5：题目可保存富文本题干与**至少一张图片**，且"题干图片通过受控资源接口提供，未授权访问返回 `403`"。`RQ-B-3-50` 验收 1/2：manifest 声明输入 fixture 集合（含内容摘要），fixture 内容以摘要绑定进 package。 |
| 契约侧（权威） | `MAIN/contracts/openapi/b3.yaml` 的写路径只有 `POST /questions`、`PATCH /questions/{id}`、`POST /question-versions/{id}/publish|archive`、`POST /assignments`。**没有**图片上传、图片读取、fixture 上传端点；`QuestionDraftInput.adapter.files[]` 只有 `{path, content_sha256, mount}` 且 `additionalProperties: false`，因此 fixture 的**字节**没有携带位置。 |
| 附带缺口 | `POST /api/v1/questions` 的请求体（`QuestionDraftInput`）不含 `course_id`，而题目必须按课程作用域创建。 |

结论：需求要求的两个能力（题干图片的受控读写、fixture 字节入库）在冻结契约中**没有入口**；B3 不做这两件事就无法通过 `RQ-B-3-10` 验收 5 与 `RQ-B-3-50` 验收 2。

## 2. 请 MAIN 裁决的问题

1. 是否为下列操作补成文（P3 契约变更）？
   - `POST /api/v1/questions/{question_id}/assets`（上传题干图片）
   - `GET /api/v1/questions/{question_id}/assets/{asset_id}`（受控读取，`403` 语义）
   - `POST /api/v1/question-versions/{question_version_id}/fixtures`（上传 fixture，返回 B3 计算的摘要）
   - `POST /api/v1/questions` 增加 `course_id`（query 参数）
2. 若 MAIN 决定图片/ fixture 走**别的**通道（例如 B4 的对象存储 + 签名 URL），请给出权威端点；B3 将改为写入对象存储并只保留摘要。
3. 摘要的权威方确认：B3 认为**服务端计算**的摘要才是权威，客户端在 manifest 中声明的 `content_sha256` 仅用于比对（见 `compile/manifest.py: assert_fixture_binding`）。若 B2 期望"以客户端声明为准"，请指出。

## 3. B3 当前实现（契约内落地）

- 三个新操作**全部落在 `module.yaml` 已声明的既有前缀之下**（`/api/v1/questions`、`/api/v1/question-versions`），**未新增路由前缀**；这与统计端点当初的处理方式一致（当时的处理是先实现、随后由 MAIN 在 `b3.yaml` 中成文）。
- 未提供**任何**兼容层或别名路由；上述四个操作之外的路径集合与 `b3.yaml` 完全一致（`tests/test_plugin.py::test_every_route_path_is_under_a_declared_prefix` 断言全部路径都落在声明前缀内）。
- 在上表操作被 MAIN 成文之前，**其他模块不得依赖**它们（`docs/api/contract.md` 已标注 `B3 本地扩展（未成文）`）。
- `course_id` 采用 query 参数而不是请求体，是为了不违反 `QuestionDraftInput` 的 `additionalProperties: false`。

## 4. B3 侧的痕迹

- 实现：`src/autograder_b3/api/router.py`（三个操作 + `course_id` 参数）、`src/autograder_b3/domain/assets.py`、`src/autograder_b3/infra/storage.py`。
- 测试：`tests/test_questions_api.py`（上传/越权 403/安全响应头/类型白名单/仅草稿可传）、`tests/test_versions.py`（fixture 摘要绑定与不匹配拒绝）。
- 文档：`docs/api/contract.md` 的"我提供的端点"表内标注 `未成文（CHG-B3-0002）`。

---

## 5. MAIN 裁决（2026-09-18）：**四个操作全部成文**

**理由。** 这不是"要不要加能力"的问题，而是"已交付的能力要不要写进契约"的问题：B3 已经实现并通过
测试，`RQ-B-3-10` 验收 5 与 `RQ-B-3-50` 验收 2 依赖它们，而契约里没有入口意味着**其他模块不得依赖**
（B3 自己在 `docs/api/contract.md` 里就是这样标注的），B1 因此无法渲染题干图片。补声明的方向是
唯一合理的选择：反方向（删实现）会让两条 RQ 无法成立。

**已实施的改动（`MAIN/contracts/openapi/b3.yaml`）。**

| 操作 | 声明内容 |
|---|---|
| `POST /api/v1/questions` | 补 `course_id`（`in: query`、`required: true`）——B3 一直按 query 参数接收（`QuestionDraftInput` 是 `additionalProperties: false`，作用域没有地方放），示例与 §1 的既有实现一致 |
| `POST /api/v1/questions/{question_id}/assets` | `operationId: uploadQuestionAsset`，请求体 `AssetUploadInput`，响应 `201 StemAssetUploadResult` |
| `GET /api/v1/questions/{question_id}/assets/{asset_id}` | `operationId: getQuestionAsset`，图片字节 + `401/403/404`；描述写明 `nosniff`、CSP `sandbox`、`ETag = content_sha256`，并写明**为什么浏览器不能用裸 `<img src>`**（需要 bearer 令牌） |
| `POST /api/v1/question-versions/{question_version_id}/fixtures` | `operationId: uploadVersionFixture`，请求体 `FixtureUploadInput`，响应 `201 FixtureUploadResult`；`tags: [internal]`（fixture 是受保护内容，字节永不回读） |

**关键点：声明按"实现已经强制的东西"写，不按"希望它强制的东西"写。** 首轮声明把 `alt_text`
上限写成 300（实际 500）、漏了 fixture 的 `mount`、给 `content_base64` 加了一个实现并不存在的
长度上限、并声称未知资产返回 403。跑一次真实探针后逐条改正——**契约描述得比实现更严，等于对
客户端撒谎**。

**验证（真实执行，对照上表）。** 以教师身份对运行中的 host 走完整生命周期（脚本用完即删，
只走公开 HTTP）：

```text
createQuestion            -> 201   （course_id 走 query，与声明一致）
uploadQuestionAsset       -> 201   response keys = [alt_text, asset_id, content_sha256, content_type, size_bytes]
getQuestionAsset (token)  -> 200   68 bytes; content-type=image/png; X-Content-Type-Options=nosniff;
                                   CSP="default-src 'none'; sandbox"; ETag == content_sha256
getQuestionAsset (no tok) -> 401
getQuestionAsset (unknown)-> 404
uploadVersionFixture      -> 201   response keys = [content_sha256, fixture_id, mount, path, size_bytes]
updateQuestionDraft       -> 200
publishQuestionVersion    -> 422   "stored fixtures are missing from the manifest: cases/input.txt (input)"
                           （fixture 必须在其清单里声明——绑定门在工作）
publishQuestionVersion    -> 200   （补上 adapter.files[] 之后）
getQuestion               -> 200   stem_assets 已填充，status=published
```

**对 B1 的影响：配额 1 因此关闭。** B1 不再显示"契约没有读取资源的端点"，改为带令牌取字节并
渲染自建 object URL（`src/features/questions/useQuestionAsset.ts`）；真实浏览器里已验证图片
从 `blob:` URL 成功解码（`complete=true, naturalWidth=1`）。

**模块侧改动：B3 无。** 只把 `dto/internal.py` 里 `AssetUploadInputModel` / `FixtureUploadInputModel`
的"NOT part of the frozen contract (CHG-B3-0002)"注释改为指向本次裁决；表结构与路由未动。
