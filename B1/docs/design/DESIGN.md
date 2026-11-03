# AutoGrader 设计系统（B1 Web 客户端）

> 派生自 [`awesome-design-md` 的 Linear 设计系统](https://github.com/VoltAgent/awesome-design-md)，
> 按 AutoGrader 的产品形态做了三处明确偏离（见 §1.3）。
> 落地唯一真源是 [`src/styles.css`](../../src/styles.css)：本文件描述规则，CSS 只负责实现；
> 两者不一致时以 CSS 为准并回改本文档。

## 1. 视觉基调与来源

### 1.1 一句话

**工程化的克制 + 一层极淡的光。** 中性灰承载信息，唯一的蓝紫强调色只出现在可交互元素上，
状态色只出现在状态上；渐变只用来做"光"（页面顶部的低饱和光晕、主按钮的纵向微渐变、
面板顶部的 1px 发丝），**不用彩色渐变填充区块、不用渐变表达状态**。

### 1.2 为什么是 Linear 而不是 Apple 式

评测系统的页面是**证据与状态**的页面：成绩矩阵、逐用例证据、名单映射、用户与角色。
这类页面最怕"装饰压过读数"。Linear 的系统在这三点上正好对上：

1. 中性 chrome + 唯一强调色，把注意力留给"通过/失败/未测量"；
2. 8px 节奏与 hairline 分区，够密也能装下密表；
3. 文档里描述的是产品内界面（面板、工具栏、状态胶囊、命令面板），不是营销首屏。

### 1.3 三处偏离（本系统特有，不是 Linear 原样）

| 偏离 | 规则 | 原因 |
|---|---|---|
| 中文字距例外 | 拉丁文标题可用 -0.02em；**中文正文与标题的字距不小于 -0.02em**，正文行高 1.6 | Linear 的 -1.05px@48px 是给拉丁文的，中文这样排会挤 |
| 状态色板补齐 | Linear 只给了 success；本系统补 warn / bad / idle，并把"未测量、不可用"定为**中性灰**而不是错误态 | `null` 不能渲染成 0 分；"检查过、无问题"与"没检查"必须是两种外观 |
| 浅色为主 | 默认浅色，深色跟随 `prefers-color-scheme`；不做手动切换 | 教室投影、成绩打印、名单核对都在亮环境 |

### 1.4 语气

冷静、精确、不劝服。没有欢迎语、没有插图、没有装饰性图标堆叠。
界面上每一处颜色都对应一个真实语义（可交互 / 通过 / 警告 / 失败 / 未测量）。

## 2. 色彩体系

实现为 `:root` 的 CSS 变量，深色在 `@media (prefers-color-scheme: dark)` 内整体覆盖。

### 2.1 中性色与强调色

| 角色 | Token | 浅色 | 深色 |
|---|---|---|---|
| 页面底色 | `--color-bg` | `#f7f8f8` | `#08090a` |
| 卡面/面板 | `--color-surface` | `#ffffff` | `#0f1011` |
| 次级面（表头、输入、分组） | `--color-surface-2` | `#f3f4f5` | `#191a1b` |
| 三级面（hover、轨道、禁用） | `--color-surface-3` | `#e9ebee` | `#232426` |
| 行 hover | `--color-surface-hover` | `#eef0f3` | `#2a2b2e` |
| 发丝线 | `--color-border` | `#e3e5e9` | `rgba(255,255,255,0.08)` |
| 强线（输入 hover、表格外框） | `--color-border-strong` | `#d0d6e0` | `rgba(255,255,255,0.16)` |
| 正文 | `--color-text` | `#101113` | `#f7f8f8` |
| 次要文字 | `--color-text-muted` | `#4f5560` | `#a7adb6` |
| 弱化文字（表头、时间、ID） | `--color-text-faint` | `#6b717b` | `#8a8f98` |
| 强调（唯一彩色） | `--color-accent` | `#5e6ad2` | `#8b8ff5` |
| 强调 hover | `--color-accent-strong` | `#4d57bd` | `#a0a3ff` |
| 强调弱底 | `--color-accent-soft` | `rgba(94,106,210,0.10)` | `rgba(113,112,255,0.12)` |

### 2.2 状态色（语义，不可装饰性使用）

| 语义 | Token | 浅色 | 深色 | 用在哪 |
|---|---|---|---|---|
| 通过 / 完成 / 有效 | `--color-ok` + `--color-ok-soft` + `--color-ok-dot` | `#127a43` / `rgba(23,134,74,.10)` / `#27a644` | `#3dd68c` / `rgba(61,214,140,.10)` / `#3dd68c` | `pill--ok`、成功提示条、覆盖度条形 |
| 警告 / 需人工确认 | `--color-warn` + `--color-warn-soft` | `#95560a` / `rgba(217,119,6,.12)` | `#f0a04b` / `rgba(240,160,75,.11)` | `pill--warn`、advisory 面板、`identity-broken` |
| 失败 / 拒绝 / 删除 | `--color-bad` + `--color-bad-soft` | `#b42318` / `rgba(217,45,32,.09)` | `#ff6b6b` / `rgba(255,107,107,.11)` | `pill--bad`、错误提示条、被拒行、危险按钮 |
| 中性 / 未测量 / 不可用 / 空闲 | `pill--idle`（`surface-2` + `text-muted`） | 同左 | 同左 | **"没测到"与"不可用"一律走中性，不走错误色** |

**硬规则**

- 状态色只用于状态，不用于品牌、标题、导航、按钮（危险按钮除外）。
- 同一屏里"权威分"与"AI 参考分"不得同色同形：参考分用虚线边 + 弱化灰 + 不加粗。
- 颜色不是唯一信号：每个状态胶囊带一个 6px 圆点 + 文案，色盲用户也能读。

### 2.3 渐变（只做光与层级）

| Token | 用途 | 规则 |
|---|---|---|
| `--gradient-page` | 页面背景：顶部一层低饱和径向光晕 + 极淡纵向渐变 | 深色下换成 indigo 光晕（透明度 ≈ 0.16） |
| `--gradient-hero` | 导览页两列上方的光晕 | 只在 `landing`/`auth-page` 用，内容页不用第二处光 |
| `--gradient-accent` | 主按钮（180° 微渐变）+ 进度条 | 起止色差 ≤ 10% 亮度，避免"塑料按钮" |
| `--gradient-hairline` | 页头底边、面板顶部 1px 发丝 | 两端透明，中间实色，不切角 |

**禁止**：彩色渐变大面积铺底、渐变胶囊、渐变表格行、渐变文字超过一个（目前只给导览页品牌名）。

## 3. 排版

### 3.1 字体

```css
--font-sans: "Inter", "SF Pro Text", -apple-system, BlinkMacSystemFont, "Segoe UI",
  "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", system-ui, sans-serif;
--font-mono: "Berkeley Mono", "JetBrains Mono", "SFMono-Regular", ui-monospace,
  "Cascadia Code", Menlo, Consolas, monospace;
```

不加载网络字体：Inter 缺席时按系统栈降级，中文落到 PingFang SC / 微软雅黑 / Noto Sans SC。
`font-feature-settings: "cv01", "ss03"` 只在 Inter 存在时生效，缺失时无副作用。

### 3.2 层级

| 角色 | 字号 | 字重 | 行高 | 字距 |
|---|---|---|---|---|
| 页面标题 `h1` | 26px | 600 | 1.2 | -0.02em |
| 面板标题 `h2` | 15px | 600 | 1.4 | -0.01em |
| 卡片标题 `h3` | 15px | 600 | 1.4 | -0.01em |
| 正文 | 15px | 400 | 1.6 | -0.011em |
| UI 文字（按钮/导航/输入） | 14px | 500 | 1.4 | 0 |
| 表格正文 | 14px | 400 | 1.45 | 0 |
| 表头 / 标签 | 11.5px | 600 | 1.3 | 0.06em（大写） |
| 元信息（时间、ID、说明） | 12.5px | 400 | 1.5 | 0 |
| 等宽（ID、code、诊断） | 12px | 400 | 1.5 | 0 |
| 等宽小标（Mono Label） | 11.5px | 500 | 1.4 | 0.02em（大写） |

- 字重只有 400 / 500 / 600；**不用 700**（中文会糊）。
- 数字一律 `font-variant-numeric: tabular-nums`（分数、时长、序号、计数）。
- 中文标题不套用拉丁文的紧字距；`h1`/`h2` 的 -0.02em 是上限。

## 4. 组件形态

| 组件 | 形态 | 状态与规则 |
|---|---|---|
| 主按钮 `.button` | 6px 圆角，36px 高（触控/移动 42px），横向 16px，白字，`--gradient-accent` + 顶部 1px 内高光 | hover 提亮 + 上移 1px；active 回落；disabled 换 `surface-3` + `text-faint`（**不是半透明蓝**） |
| 次按钮 `.button--ghost` | `surface-2` + 1px 发丝线，14px 文字 | hover `surface-3`；无阴影 |
| 危险按钮 `.button--danger` | 透明底 + 红字 + 红色 1px 边框 | hover 红色弱底；主危险动作才用实心红 |
| 输入 `.field input/select/textarea` | 38px 高，6px 圆角，1px 发丝线，白底 | focus：accent 边框 + 3px 弱光环；hover 加深边框；错误走 `field__error` 文案 + `aria-invalid` |
| 表格 `.table` | 表头 11.5px 大写 + 弱化色；行高 44px；行分隔 hairline；`nowrap`；数字右对齐 tabular | hover 行 `surface-hover`；被拒行 `bad-soft`；表内控件 30px 高 |
| 状态胶囊 `.pill` | 999px 圆角，12px/500，6px 圆点 + 文案，1px 同色系透明边框 | ok/warn/bad/idle 四态；**idle 是中性而非错误** |
| 提示条 `.banner` | 8px 圆角 + 3px 左侧色条 + 同色系弱底 | info/warn/error/success 四态；错误用 `role="alert"` |
| 分页页签 `.tabs` | 分段控件：外层 `surface-2` 圆角 8px + 1px 发丝线，选中项白底 + 发丝线 | 未选中项透明；键盘左右方向键仍由组件负责 |
| 面板 `.panel` | 白面 + 1px 发丝线 + 12px 圆角 + 极轻投影 + 顶部 1px 渐变发丝 | **不用 18px 大圆角与"卡片浮起"阴影** |
| 证据卡 `.case-evidence` / `.case-card` | `surface-2` 底、8px 圆角、无边框 | 证据靠底色分组，不靠再描一层边 |
| 弹窗 `.modal` | 12px 圆角，深遮罩 + 8px 模糊，唯一允许的抬升阴影层 | 焦点进弹窗、Escape 关闭、焦点归还（组件已实现） |
| 抽屉 `.drawer` | 右侧小屏导航，`surface` 底 + 左发丝线 | 打开锁滚动、Escape/遮罩关闭 |
| 图表 `.chart` | 280px 高，颜色全部读 token（`getComputedStyle`） | 主题切换时颜色跟着变，不硬编码 |
| 编辑器 `.editor` | Monaco 容器，8px 圆角 + 发丝内环，文本降级用 `surface-2` | 不低于 360px 高 |

### 4.1 状态映射（页面文案 → 视觉）

| 界面语义 | 视觉 |
|---|---|
| 运行中 / 排队中 | 中性胶囊（`pill--idle`）+ 文案，不伪装成成功 |
| 通过 / 完成 | `pill--ok` |
| 技术失败 / 基础设施错误 | `pill--bad`（与"代码错误"同样用红，但由文案区分，**不合并成一种**） |
| 未测量 / 无数据 / 未完成 | 中性胶囊 + 明确文案，**不显示 0** |
| 不可用 / 未启用（409/503） | 中性信息态（`banner--info`），不写成"失败" |
| 需人工确认 / 归属 | `pill--warn` |
| AI 参考分（advisory） | 虚线边框 + 弱化灰 + 不加粗，权重低于权威分 |

## 5. 布局与密度

- 内容列：外壳最大 1440px，正文列 1080px；导览页 1200px；认证卡 420px（注册 550px）。
- 节奏：8px 基准（4/8/12/16/24/32/48）；面板内边距 20px 24px。
- 密度：控件 38px、表格行 44px、页头 56px、侧栏 232px。
- 侧栏导航项 38px（触控抽屉 44px）；页签 30px。
- 断点：`767px`（侧栏收进抽屉、导览单列）、`520px`（认证卡去壳）、`860px`（出题向导单列）。
- 每页恰好一个 `h1`；面板 `h2`、卡片内 `h3`，不跳级。

## 6. 层次与光影

| 层 | 处理 | 用途 |
|---|---|---|
| 0 | 页面底色 + 顶部光晕 | 画布 |
| 1 | `surface` + 1px 发丝线 | 面板、卡面、表格容器 |
| 2 | `surface-2` | 表头、输入、代码块、证据卡 |
| 3 | `--shadow-card`（1px 2px 的极轻投影） | 面板的"可点击感" |
| 4 | `--shadow-raised` | **只有**弹窗、抽屉、下拉菜单 |

- 深色下不用阴影做层次，用亮度分层（`#08090a → #0f1011 → #191a1b → #232426`）+ 半透明白发丝线。
- 焦点环：`0 0 0 3px var(--color-accent-soft)` + `0 0 0 1px var(--color-accent)`，不用浏览器 outline。

## 7. Do / Don't

**Do**

- 让灰色承担 95% 的界面，强调色只给可点击与"当前"。
- 用发丝线和底色分层表达结构；阴影只给真正浮起的层。
- 状态用"圆点 + 文案 + 同色系弱底"，颜色永远不是唯一信号。
- 中文维持 ≥ -0.02em 字距、1.5–1.6 行高。
- 所有数字 `tabular-nums`，所有 ID 用 `.mono-id`。

**Don't**

- 不要把"未测量/不可用"画成红色错误，也不要在未完成时显示 0 分。
- 不要用彩色渐变做区块背景、胶囊或表格行；渐变只做顶部光晕与 1px 发丝。
- 不要用 700 字重、18px 大圆角、彩色大阴影、毛玻璃叠毛玻璃。
- 不要给 AI 参考分任何"看起来像成绩"的外观（实心色、粗体、进度条）。
- 不要用图标代替文案表达状态；不要给状态加动画闪烁。

## 8. 响应式与动效

- 动效时长 120–240ms；曲线 `cubic-bezier(0.22, 0.61, 0.36, 1)`；只动 `transform`/`opacity`/`background`。
- hover 位移 ≤ 4px；面板不缩放；按钮 active 只回落 1px。
- `prefers-reduced-motion: reduce` 下动画压到 0.001ms，状态反馈仍由颜色与焦点环即时给出。
- 触控断点下控件高度 ≥ 42px，侧栏改抽屉。

## 9. 实现映射与变更流程

- token 全在 `src/styles.css` 的 `:root` / 深色媒体查询里；组件类名不变，**只改 CSS**（沿用 phase-02 的做法，避免碰 `.tsx` 与测试）。
- 新增组件类前先看 `src/ui/components.tsx` 是否有对应原语；业务语义留在 feature 目录。
- 改动 `src/styles.css` 或 `src/ui/components.tsx` 前，先在 `docs/phases/active/` 登记当轮阶段文档。
- 本轮落地记录见 [`../phases/active/phase-10-linear-design-system.md`](../phases/active/phase-10-linear-design-system.md)。

## 10. 视觉证据（浅/深两套）

真实构建产物 + 无头 Chromium 实拍，1440px 宽：

| 页面 | 浅色 | 深色 |
|---|---|---|
| 导览页（真实页面） | [`screenshots/landing-light.png`](screenshots/landing-light.png) | [`screenshots/landing-dark.png`](screenshots/landing-dark.png) |
| 登录浮窗（点「登录」后的真实交互） | [`screenshots/login-light.png`](screenshots/login-light.png) | [`screenshots/login-dark.png`](screenshots/login-dark.png) |
| 组件总览（表格/胶囊/按钮/提示条/页签/证据卡） | [`screenshots/components-light.png`](screenshots/components-light.png) | [`screenshots/components-dark.png`](screenshots/components-dark.png) |

组件总览页是把同一份构建后 CSS 挂到代表性标记上的临时产物（不入库），
用于在**没有后端**的环境里复核表格、状态胶囊与表单形态；它不是真实业务页面的截图。
