# B1 知识笔记索引

## 收录标准

- 同一根因至少出现**两次**且有稳定修复，才收录为笔记。
- 命名：`YYYY-MM-DD-short-problem.md`。
- 模板：现象 / 重复触发条件 / 根因 / 最终修复 / 预防措施 / 关联证据。

## 当前笔记索引

| 笔记 | 主题 | 状态 |
|---|---|---|
| （暂无正式笔记） | — | — |

> 当前无满足"同一根因出现两次"门槛的正式笔记。

## 候选（各出现 1 次，**未达收录标准**，暂存于排障手册）

| 候选 | 现象 | 当前记录位置 |
|---|---|---|
| `openapi-typescript` 在 Windows 反斜杠路径下误判文档格式 | `Unsupported schema format, expected 'openapi: 3.x'` | `../runbooks/troubleshooting.md` 第 3 条 |
| pnpm 共享 store 位于工作区外时写盘被拒 | `ERR_PNPM_EPERM`（改 store 位置会引发 `ERR_PNPM_UNEXPECTED_STORE`） | `../runbooks/troubleshooting.md` 第 2 条 |
| jsdom 下 Monaco/ECharts 无法真实初始化 | `Cannot read properties of null (reading 'clearRect')`、缺 Web Worker | `../runbooks/troubleshooting.md` 第 5 条 |

> 再次遇到同一根因并确认稳定修复后，按模板提升为正式笔记（`YYYY-MM-DD-short-problem.md`）。
