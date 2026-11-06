# Phase 13 换页动效、下拉圆角与索引页层级

本文件记录本轮改动。**没有改动任何功能**：接口、跳转、角色校验、表单提交逻辑一行未动；
只有一处结构性改动（`.app-content` 加 `key`，见下），以及若干 CSS。

## 需求

1. 索引页（导览页）允许有更明显的改动；
2. 内部「点开有下拉/展开」的方块改成圆角；
3. 页面切换不要生硬；
4. 下拉展开加动效（参考给的四个站点）。

## 做了什么

### 1. 页面切换：按侧栏顺序上下滚动

**方向由两个页面在侧栏里的先后决定**（`navigation.ts` 的 `mainNavIndex`）：

- 回到更靠上的入口（序号变小，例如 成绩 → 题库）→ **画面上滚**，新页面从下方 44px 升起落位；
- 去更靠下的入口（序号变大，例如 题库 → 课程）→ **画面下滚**，新页面从上方 44px 落入；
- 首次进入、或不属于任何主入口（导览页 / 认证页 / 404）→ 不做方向动画。

实现：`AppShell` 在渲染期用 ref 保留"上一次渲染的路径"，算出方向写进 `data-nav-direction`；
`.app-content` 仍用 `key={pathname}` 重挂，所以动画只在真正换页时跑一次，
同一页改筛选/分页不会重放。320ms、`--ease-out`，只位移内容区，侧栏与顶栏不动——
视线锚点稳定，滚动才显得顺。`prefers-reduced-motion` 下关闭。

**为什么只动新页面、不复刻旧页面**：React 在路由切换的同一帧就把旧页面卸载了，
要让"旧页滚出去"必须留一份旧 DOM。两条路都评估过：
① 原生 View Transitions——实现过，但实机感觉卡顿，**按要求已完全移除**（文件删除，无残留）；
② 克隆旧 DOM 做覆盖层——成绩页有 ECharts canvas，`cloneNode` 出来的画布是空白的，
   反而更难看。所以改成"新页面按正确方向滚入"：方向语义保住了，代价最小、没有额外图层。

`AppShell` 的 `key` 只跟 **pathname** 走，同一页改查询串（筛选、分页、tab）**不会重挂**，
页面状态不丢。

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

### 5. 动态光晕只在导览页出现

`GeminiGlow` 原来挂在 `Root` 上、全站常驻。现在**只在导览页（`/`）渲染**：

- `Root` 不再渲染它；`AppShell` 的公开分支里，只有 `isLanding` 时挂载 `<GeminiGlow />`；
- 内部页面（作业、题库、成绩、复核、用户管理）与认证页改为 `body` 上那层静态渐变
  （`--gradient-page`）做底色——这层兜底本来就有，注释里写的就是"光晕不跑时页面依然是成品样子"，
  所以内页不会变白、观感也没有落差；
- 好处：长期停留在业务页时不持续占用合成器（六个色相每 3 秒换一次），
  也不会把读数的注意力从表格上拉走；
- `AppShell.test.tsx` 补了两条断言锁住范围：导览页有 `gemini-glow`，进到 `/assignments` 后必须为 `null`。

### 6. 加载页换成"五柱 + 弹跳球"

按用户提供的 Uiverse 样板（Nawsome 的 loader）替换原来的"光条横扫"骨架屏：

- 结构：`.boot__loader` 里 5 根 `.boot__bar` + 1 个 `.boot__ball`；
- 几何与关键帧原样保留（球 4 秒从左跳到右再跳回、柱子随球经过依次抬起，中间那根不动）；
- 只换颜色：柱子 `--color-text-faint`、小球 `--color-accent` 并加一圈淡光环，
  让它像产品的一部分而不是贴上去的第三方组件；
- 去掉样板里的 `-webkit-` 重复关键帧（现代浏览器不需要），合并同值关键帧；
- `prefers-reduced-motion` 下停掉跳动，只留静态的一组柱子和球。

### 7. 内部页背景换成点阵网格

按用户提供的 Uiverse 片段（`radial-gradient(... 2px, transparent 0)` + `30px 30px`），
只加在 `.app-shell` 上——导览页（`.landing`）与认证页（`AuthLayout`）都不经过它，欢迎页不受影响。

- 颜色走 token：浅色 `rgba(16,17,19,.12)`、深色 `rgba(255,255,255,.09)`，间距 28px、点半径 1.8px；
- **文字区一律不是点阵**：卡片/面板/表格/表单都是不透明表面，点只从卡片缝隙、侧栏空白、
  页面边距里透出来；唯一压在点阵上的文字是页面标题，所以点的透明度压得很低（12%），
  大字底下依然清楚；
- 顶栏是半透明 + `backdrop-filter`，点会隐约透过去，观感上是磨砂条而不是纯色条。

### 8. 所有「加载中…」前面加一块脉冲指示

用户的原话是"**所有初次进入页面或初次点击某个按钮会出现「加载中」三个字**，希望在前面加入这个样式，
使其看起来不单调"。所以做的不是某一个页面，而是把**等待提示这个原语**本身补上一块指示器。

造型取自 Uiverse 上 cosnametv 的 loader：3 列网格、六个方块按下标错峰闪动（`scale` + `opacity`）。

- `InlineLoader`（`src/ui/components.tsx`）：六个空 `<span>`，`aria-hidden` —— 装饰性的方块对读屏完全隐形，
  朗读出来的仍然只有那句提示；颜色用 `currentColor`，跟着 `.muted` 或按钮文字走，深浅主题都不用另写规则；
- **`Spinner` 改这一处，覆盖约 20 处等待提示**（作业、题目、成绩、名单、复核、用户管理……）；
- 另外 6 处不走 `Spinner` 的纯文字等待也一并接上：作业列表与作业详情的"本人进度"单元格、
  题目正文里的图片占位（`SafeMarkdown`、`SubmitPage`）、人工评分抽屉的收件箱、新建作业里的版本列表；
- 按钮里出现"加载中…"的两处（列表的"加载更多"、申诉的"读取申诉包"）同样加在文字前，按钮高度仍是 36px。

**尺寸与排版**：单块 5px、间隙 2px，整体 19×12px，`vertical-align: -3px` 与汉字视觉中线对齐。
外层是 `inline-grid` 而不是 flex——原来那二十来处 `<p>` 的对齐方式、行高、段落间距、单元格折行一律不变；
只有 `.button .inline-loader` 去掉右边距（按钮本身已经是 `inline-flex` + 6px `gap`，否则会变成 12px）。
波形最暗只到 0.35：5px 的方块再暗就整块看不见，看起来像少了两颗而不是在呼吸。
`prefers-reduced-motion` 下停在半亮的静态六块。

## 改动文件

| 文件 | 改动 |
|---|---|
| `src/app/AppShell.tsx` | `.app-content` 加 `key={location.pathname}`（换页重挂以触发入场动画） |
| `src/ui/components.tsx` | 新增 `InlineLoader`；`Spinner` 与 `LoadMoreActions` 的等待态带上指示器（文字与行为不变） |
| `src/styles.css` | `page-enter` / `reveal-in` 关键帧与挂载点；select 圆角与自绘箭头；索引页间距/字号/色带；`.inline-loader` 与 `inline-loader-blink`；`prefers-reduced-motion` 下关闭相关动画 |
| `src/features/**` | 6 处纯文字等待（作业列表/详情、`SafeMarkdown`、`SubmitPage`、人工评分抽屉、新建作业、申诉面板）接入同一个指示器 |
| `src/ui/components.test.tsx` | 三条断言：指示器在文字前、`aria-hidden`、"加载更多"取页时带动画且保持禁用 |
| `docs/phases/active/phase-13-motion-and-dropdown.md` | 本文档 |

## 真实执行

| 检查 | 结果 |
|---|---|
| `tsc -b --pretty false` | 通过（exit 0） |
| `eslint . --max-warnings 0` | 通过（exit 0） |
| `vitest run --pool=threads --maxWorkers=2`（全量） | **56 个文件 / 500 个用例通过** |
| 样式表结构 | 大括号平衡，`prefers-color-scheme` 两处媒体查询完好 |
| 截图核对 | 等待行 / 表格单元格 / 正文内联 / 禁用按钮 / 深色主题 / 五个动画相位（`_work/ui-probe/inline-loader-4.png`） |

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
