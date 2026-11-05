# Phase 12 导览页外观：卡片门户 + 排版索引

本文件记录本轮改动。**只改外观**：结构、按钮、弹窗、跳转与角色校验逻辑一行未动，
`LandingPage.tsx` 没有修改，测试用例也没改。

## 方案来源

用户从四个参考站里选了「方案二 + 方案一」：

- **方案二 · 卡片门户**（参考 htmlrev.com 的 Cardly / April 这类卡片式落地页）
- **方案一 · 排版纪律**（参考 designprompts.dev 的 Swiss Minimalist：编号、发丝线、字距节奏）

## 具体改动（全部在 `src/styles.css` 的「导览页」一段）

| 项 | 改动 | 为什么不动结构 |
|---|---|---|
| 两栏比例 | `grid-template-columns: minmax(0, 1.12fr) minmax(0, 1fr)` | 教师端入口多，给略宽份额形成主次；窄屏仍是单列 |
| 卡片 hover | 只过渡 `border-color` / `box-shadow`，描边加深；**不位移、不放大** | 指针移动时版面稳定，点击手感与改动前一致 |
| 行首编号 | `li::before` + CSS 计数器（`decimal-leading-zero`），等宽字体、`tabular-nums` | **零 DOM 改动**，编号是装饰性的、`pointer-events: none`，不参与焦点与点击 |
| 行内边距 | `padding: 12px 8px 12px 44px`（左侧留给编号） | 整行仍是点击区域，`min-height` 未变（桌面 62px / 窄屏 56px） |
| 行 hover | 文字块 `translateX(2px)`（只动 transform）+ 编号变 accent | 不触发重排，不会"跳" |
| 禁用态 | `li:has(.landing__link--disabled):hover::before` 保持灰色 | 角色不匹配的入口不能被视觉误导成"可点" |
| 说明文字 | `.landing__lead { max-width: 46ch }` | 宽屏上不拉成难扫的长句 |

保留的既有设计约束：顶端 2px 发丝线教师端用 accent、学生端保持中性（绿色只属于「通过」）；
登录按钮、账户区、登录浮窗、入口的角色提示全部原样。

## 真实执行

| 检查 | 结果 |
|---|---|
| `tsc -b --pretty false` | 通过（exit 0） |
| `eslint . --max-warnings 0` | 通过（exit 0） |
| `vitest run src/features/home/LandingPage.test.tsx src/app/AppShell.test.tsx` | **24 个用例通过**（用例本身未改） |
| 横向溢出检查（430px 视口实测 `scrollWidth` 与 `innerWidth`） | 相等，**无横向溢出** |
| 截图复核 | 1440px 双栏（教师端略宽、01–05 / 01–02 编号对齐）、430px 单列 |

## 未验证

- 深色模式只是沿用既有 token（`--gradient-surface` / `--color-text-faint` / `--color-accent`），
  没有单独在真实深色系统下复核。
- 卡片 hover 与行 hover 的"手感"需要真人用鼠标确认；截图看不到悬停态。
- `:has()` 依赖现代浏览器（Chrome 105+ / Safari 15.4+ / Firefox 121+）；只在禁用态高亮逻辑里用到，
  不支持时退化为"禁用行 hover 时编号也变蓝"，不影响任何操作。

## 回滚

纯 CSS，`git revert <commit>` 即可整体回退。
