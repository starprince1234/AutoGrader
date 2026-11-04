# Phase 10 Linear 设计系统重做（当前阶段）

## 目标与范围

Phase 02 把 B1 的视觉统一为 Apple 式（light-first + 自动深色），解决了"能用但不像产品"的一批
具体缺陷。本阶段的结论是：**评测系统的页面是证据与状态的页面**，需要更克制的 chrome、更密的信息
承载和更突出的状态语义，因此把视觉语言换成派生自 Linear 的设计系统，并补齐它缺的两块（状态色板、
中文字排版例外）。

范围：`src/styles.css`（唯一实现）、`index.html` 的两个 meta 与 favicon、`docs/design/DESIGN.md`（新增）。
**范围外**：任何 `.tsx` 结构、任何 class 名、`MAIN/contracts/**`、服务端行为、新增依赖。

## 设计来源与三处偏离

来源：[`awesome-design-md` 的 Linear 设计系统](https://github.com/VoltAgent/awesome-design-md)。
完整规则见 [`../../design/DESIGN.md`](../../design/DESIGN.md)。相对原系统做了三处明确偏离：

| 偏离 | 做法 | 原因 |
|---|---|---|
| 中文字距 | 标题字距上限 -0.02em，正文行高 1.6；Linear 的 -1.05px@48px 只对拉丁文适用 | 中文按拉丁文紧排会挤成一团 |
| 状态色板 | 补 warn / bad / idle；`idle` 是中性灰，专门承载「未测量 / 不可用 / 未完成」 | Linear 只给了 success；"没测到"不能画成错误，`null` 更不能画成 0 |
| 浅色为主 | 默认浅色，深色跟随 `prefers-color-scheme`，不做手动切换 | 教室投影、成绩打印、名单核对都在亮环境 |

渐变按"只做光"的规则使用：页面顶部低饱和光晕、主按钮 180° 微渐变、面板顶边 1px 发丝、
品牌名一处渐变文字；**没有**彩色渐变填充区块、渐变胶囊或渐变表格行（见 DESIGN.md §2.3）。
随后追加了**常驻背景光晕**（§"追加：Gemini 式常驻光晕"），它是这条规则的唯一例外，
但同样只做背景光、不进内容层。

## 实际变更

| 文件 | 变更 |
|---|---|
| `src/styles.css` | 整体重写为 Linear 式 token 与规则（2538 行）。中性色/强调色/状态色/渐变/圆角/阴影/动效全部 token 化；按钮 6px 圆角 + 微渐变、输入 38px、表格行 44px + 发丝线、状态胶囊改为「圆点 + 文案 + 同色系弱底 + 1px 同色晕边」、页签改分段控件、面板改 12px 圆角 + 顶边渐变发丝、弹窗/抽屉保留唯一抬升阴影、深色下提示条回到中性面只留左侧色条。**类名一个未改** |
| `index.html` | `theme-color` 改 `#f7f8f8` / `#08090a`；favicon 底色改 `#5e6ad2`、圆角 12；注释改指向 `docs/design/DESIGN.md` |
| `docs/design/DESIGN.md` | 新增：色彩/排版/组件/布局/光影/Do-Don't/动效/实现映射，以及上表三处偏离 |
| `docs/README.md` | 索引新增本文件与 `design/DESIGN.md` |
| `README.md` | 顶部状态段新增本轮摘要 |

未被删除的既有约束（重写时逐条保留）：`.table__wrap` 负外边距、单元格 `nowrap` 与固定行高、
`.mono-id` 缩短 UUID、`.advisory-score` 必须比权威分更弱、`.identity-broken` 不能渲染成错误态、
`.row--rejected` 的红色信号、`.hint-chip`、`.tabs` 的 roving tabindex 外观、`prefers-reduced-motion`。

## 验证（真实执行）

```powershell
node node_modules/typescript/bin/tsc -b                        # exit 0
node node_modules/eslint/bin/eslint.js . --max-warnings 0       # exit 0
node node_modules/vitest/vitest.mjs run                         # 合并后 54 文件 / 475 用例，summary 全通过（进程退出码 1，见下）
node node_modules/vite/bin/vite.js build                        # ✓ built，exit 0
node scripts/generate-api-types.mjs --check                     # exit 1：契约类型漂移（见下）
```

- `tsc` / `vitest` / `vite build` 需要写 `tsconfig.tsbuildinfo`、缓存与 `dist/`，本环境的
  DSH 沙箱默认挡掉这些写操作（`EPERM`），因此这三条在放开写权限后执行；命令本身未改。
- **`api:check` 失败（不是本轮引入）**：拉取远端后契约锁已是 `0.25.0`（摘要 `b985940e…`），
  而 `src/api/generated/{common,b2,b3,b4}.ts` 仍是旧生成物。本轮只解 `B1/docs/README.md` 的
  文档冲突，**没有擅自重新生成**（那会改到其他人的契约面），留待 MAIN 侧确认后统一。
- vitest 的进程退出码为 1 而 summary 全部通过，属 `phase-02 §4` 记录的 forks worker 拆卸偶发，
  判定以 summary 为准；合并后用例数由 51/456 增至 54/475（含并行批次 P10/P11 的新用例）。

### 视觉复核（无头 Edge + CDP，真实构建产物）

用 `dist/` 构建产物 + CDP 模拟 `prefers-color-scheme` 浅/深，1440px 宽、`Page.captureScreenshot`
实拍。覆盖三处：导览页（真实页面）、登录浮窗（真实交互：点「登录」打开）、组件预览页（把同一份
构建后 CSS 挂到代表性标记上：表格 + 四种状态胶囊 + 按钮四态 + 输入 + 四种提示条 + 分段页签 +
advisory 卡 + 计数卡 + 覆盖度条）。

截图见 [`../../design/screenshots/`](../../design/screenshots/)：`landing-light/dark.png`、`login-light/dark.png`、
`components-light/dark.png`。复核中据此修了三处：导览两栏由等高拉伸改为按内容高度对齐；
深色提示条由大面积色块改为中性面 + 左侧色条；两栏顶端发丝与页面光晕强度上调。

## 追加：Gemini 式常驻光晕

参考用户提供的 `gemini_background_effect_prompt.md`（外部参考文件，不入库）：
背景从顶部"浸染"出蔚蓝 → 青蓝 → 翠绿 → 亮黄绿 → 鹅黄 → 淡紫，叠极淡水波纹、有呼吸。
要求是**常驻、不需触发、每个页面都在**，因此实现成与页面无关的背景层，而不是某个页面的效果。

| 文件 | 变更 |
|---|---|
| `src/ui/GeminiGlow.tsx`（新增） | 宿主组件：`requestAnimationFrame` 逐帧写 `:root` 变量；30fps 上限；`visibilitychange` 暂停；`prefers-reduced-motion` 只写一帧 |
| `src/ui/glowAnimation.ts`（新增） | 纯函数 `glowFrame(elapsedMs)`：两条色带（退场/进场）的颜色、色标位置与呼吸值 |
| `src/app/Root.tsx` | 在 RouterProvider 之外渲染一次 `<GeminiGlow />`，路由切换不重挂 |
| `src/styles.css` | 新增「常驻光晕层」一节与 `--glow-*` token；移除 `.landing::before` / `.auth-page::before` 两处旧的静态 hero 光晕（已被统一层替代） |
| `src/ui/glowAnimation.test.ts`、`src/ui/GeminiGlow.test.tsx`（新增） | 9 个用例固定几何与降级行为 |

**实现要点与踩过的坑**

1. 第一版把两条色带的色标写成了"head 在 tail 之后"，浏览器按前一个色标钳制，截图里出现
   一条硬边紫色带。改为**每带独立渐变、色标严格递增（head < tail）**，并且每条带两端都淡出。
2. 第一版用 `mask-image` 给渐变加渐隐，遮罩边界本身又是一条硬线；已删除，柔和边界完全交给色标位置。
3. 色带 alpha 之和恒为 1，交接处没有亮度台阶；色带最深 56%，**下半屏永远是底色**。
4. 光晕在 `z-index: -1`，所有内容坐在不透明 `--color-surface` 上，因此读数不受影响。
5. 强度只有一个总开关：浅色 `--glow-strength: 0.6`、深色 `0.3`；纹理透明度 5%，
   动效 30fps，页面不可见时暂停。

**验证读数（本轮实跑）**

| 命令 | 结果 |
|---|---|
| `eslint . --max-warnings 0` | exit 0 |
| `tsc -b` | exit 0 |
| `vitest run` | 56 文件 / 484 用例，summary 全部通过（新增 9 个光晕用例）；进程退出码 1 为已知 worker 拆卸偶发 |
| `vite build` | ✓ built，exit 0 |
| `generate-api-types.mjs --check` | exit 1：生成类型未随 `0.25.0` 契约重新生成（既有状态，本轮未动生成物） |

视觉证据：`screenshots/landing-light/dark.png`、`components-light/dark.png`（数据页在光晕之下的
可读性）、`login-light/dark.png`、`glow-cycle-1..4.png`（一个色彩周期内的四帧）。

## 合并冲突（pull 远端 main）

pull 时 `B1/docs/README.md` 冲突（HEAD 与 `8373ddaff`），仓库里只有这一个文件冲突。两边都在改
同一份索引：远端新增并行批次 `phase-09-submission-and-grades`（P10/P11）与新的契约锁；
本地新增 `design/DESIGN.md`、`phase-10` 的两行引用。解决方式：**以远端为基线**，保留本地新增的
权威表/状态表两行与阶段链，把契约行换成远端拉下来的 `0.25.0`（摘要 `b985940e…`），并据实把
`api:check`、vitest 读数改成当前状态；**没有删除任何远端内容**。

`src/styles.css`、`B1/README.md` 与其余文件无冲突（远端把作答页布局放在页面私有的
`features/submission/submitLayout.css`，只引用全局 token，与新设计兼容）。合并提交未由本环境创建：
本机 `PATH` 上没有 `git`，`git add` / `commit` 需在用户的 Git 客户端完成。

## 未验证项

1. **登录后的真实业务页没有截图**：本机没有可用的 B2/B3/B4 后端，登录后被守卫送回导览页。
   表格/表单/弹窗这些形态用组件预览页（同一份构建后 CSS）复核，但**不是**真实数据页面。
2. ECharts 图表颜色由 `getComputedStyle` 读 token（phase-02 已改），本轮未在真实图表上目视复核。
3. 移动端断点（767 / 860 / 520）只做了 CSS 层面的推演，未在真机或模拟视口实拍。
4. 仅用 Chromium 内核验证：`color-mix()`、`background-clip: text`、`aspect-ratio` 等现代特性
   在 Firefox/Safari 上未实测；已为渐变文字加 `forced-colors` 回退。
5. 未做对比度工具化检查（数值按 WCAG 手算：正文/次要文字 ≥ 4.5:1，白字于 `#5e6ad2` ≈ 4.66:1）。
6. **光晕的性能只有设计约束、没有实测**：30fps 上限是为了省样式重算，但没有在低端集显 / 4K 屏上
   量过帧率与功耗；也没有在真实业务页（长表格、Monaco 编辑器）上长时间观察它对输入延迟的影响。
7. 光晕的 `prefers-reduced-motion` 降级只有单测（"不调用 rAF、只写一帧"），**没有**在系统开启
   "减少动态效果"的真实浏览器里目视确认；打印样式同理，未做打印预览。
8. `glow-cycle-*.png` 是同一页面相隔约 1.4s 的四帧，覆盖的色相不全（只到鹅黄/淡紫附近）；
   六个色相的完整性由 `glowAnimation.test.ts` 的集合断言保证，而不是靠截图。
