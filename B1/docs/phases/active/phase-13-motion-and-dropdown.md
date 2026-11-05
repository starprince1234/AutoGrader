# Phase 13 换页动效、下拉圆角与索引页层级

本文件记录本轮改动。**没有改动任何功能**：接口、跳转、角色校验、表单提交逻辑一行未动；
只有一处结构性改动（`.app-content` 加 `key`，见下），以及若干 CSS。

## 需求

1. 索引页（导览页）允许有更明显的改动；
2. 内部「点开有下拉/展开」的方块改成圆角；
3. 页面切换不要生硬；
4. 下拉展开加动效（参考给的四个站点）。

## 做了什么

### 1. 页面切换（`.app-content` 入场）

`AppShell` 给内容容器加了 `key={location.pathname}`，换页时容器重挂一次，CSS
`page-enter`（淡入 + 上浮 8px、240ms、`--ease-out`）随之重跑。

- 为什么用 `key`：不重挂就没有"入场"这回事；而 `key` 只跟 **pathname** 走，
  同一页改查询串（筛选、分页、tab）**不会重挂**，页面状态不丢。
- 为什么不用 GSAP / ScrollTrigger：换页只需要一次 240ms 的入场，引一个动画库
  会多 ~50KB 依赖和一套降级逻辑，收益不成比例。CSS 动画同样跑在合成器上。

### 2. 下拉：圆角 + 自绘展开菜单 + 弹出动画

新增 `src/ui/Select.tsx`，并把全仓 **34 处** `<select>` 换成 `<Select>`（16 个文件，纯标签替换 + 补一行 import）。

做法是"**原生控件 + 自绘外观**"：
- 原生 `<select>` 仍然留在原位（`id`、`label for`、键盘、读屏、表单值全靠它），只是 `opacity: 0`、
  铺满触发器那一块，作为真正的交互层；
- 鼠标按下时 `preventDefault()` 拦住**系统下拉菜单**，改开我们自己渲染的 `.combo__menu`：
  **12px 圆角、`shadow-raised`、`combo-pop` 弹出动画（-6px + 0.97 缩放 + 淡入，140ms）**，
  选中项用 accent、禁用项变灰；
- 触发器是 8px 圆角 + 自绘 V 形箭头，展开时箭头翻转 180°，`focus-within` 有 accent 光环；
- 选值：写回原生 select 的 value 并派发原生 `change` 事件 → React 的 `onChange` 照常触发，
  **调用点里 `(event) => setX(event.target.value)` 一行没改**；
- 键盘/读屏路径不变（焦点在原生 select 上，空格仍是系统菜单），
  所以 **40 处 `userEvent.selectOptions` 测试一行没改，也全部通过**。

表格里的下拉（`.table .combo__trigger`）跟随行内控件的 30px 尺寸。

### 3. 展开动效（我们自己控制的那部分）

可展开的名单（`.allowlist-entries`，注册名单与课程名单两处共用）加 `reveal-in`：
淡入 + 从 -4px 落位、180ms。展开有"内容落下"的反馈，收起仍是瞬时的（保持"顺手"）。

### 4. 索引页层级（外观）

- 两栏间距 24 → 32px，卡片内边距 26 → 28px，行高 62 → 64px（点击区域更大）
- 标题 24 → 26px；eyebrow 字距 0.08 → 0.12em
- 卡片顶端色带 2 → 3px，教师端改成实色 accent → 渐隐
- 行首编号 11 → 12px 并加粗（仍是 CSS 计数器，零 DOM 改动）

## 改动文件

| 文件 | 改动 |
|---|---|
| `src/app/AppShell.tsx` | `.app-content` 加 `key={location.pathname}`（换页重挂以触发入场动画） |
| `src/styles.css` | `page-enter` / `reveal-in` 关键帧与挂载点；select 圆角与自绘箭头；索引页间距/字号/色带；`prefers-reduced-motion` 下关闭这两个动画 |
| `docs/phases/active/phase-13-motion-and-dropdown.md` | 本文档 |

## 真实执行

| 检查 | 结果 |
|---|---|
| `tsc -b --pretty false` | 通过（exit 0） |
| `eslint . --max-warnings 0` | 通过（exit 0） |
| `vitest run --pool=threads --maxWorkers=2`（全量） | **55 个文件 / 480 个用例通过**（exit 0） |
| 样式表结构 | 大括号平衡（366/366），`prefers-color-scheme` 两处媒体查询完好 |
| 截图核对 | 索引页（深色主题）；下拉控件圆角与自绘箭头；名单展开态 |

**关于测试环境**：默认的 `forks` 池在本机能起的子进程数被限制，整轮会报
`Failed to start forks worker` 并带出若干"超时型"失败。同一批文件单独跑、
或改用 `--pool=threads` 后全部通过——这是环境限制，不是代码问题。
单独复跑那三个"失败"文件（AssignmentComposer / QuestionListPage / GradesPage）31 个用例全过。

## 未验证

- 换页动画**没有实机视频核对**（headless 不产帧）；只能确认动画挂载点与时长正确。
  如果觉得 240ms 偏慢或想改成纯淡入，改 `page-enter` 一行即可。
- 深色主题是本机当前系统偏好（`prefers-color-scheme: dark = true`），浅色主题下的这两处
  改动没有单独截图复核。
- 原生 select 的展开列表动效受限于操作系统绘制，未实现（见上）。
- `prefers-reduced-motion` 下动画被关闭，但没有在真实设备上开"减少动态效果"验证。

## 回滚

`git revert <commit>` 即可；CSS 与一处 `key` 都是独立的。
