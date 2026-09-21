"""Generate a module skeleton that satisfies MAIN's specification.

Why this exists: the project's headline property is "a module that obeys the spec can be
plugged in without changing MAIN" (PRD section 8.6, ADR-0013). B5 proved it once, by hand.
This makes it repeatable and, more usefully, turns the spec into something executable: if the
generated skeleton does not pass the gates, then the spec has a requirement nobody wrote down.

What it generates (all of it required by `autograder_host.checks.check_module_governance` and
`check_manifests`):

    <module>/module.yaml           contract_version read from the contract, never typed
    <module>/contracts.lock        digest computed by `autograder_platform.contract_digest`
    <module>/AGENTS.md             execution rules for whoever works in this module
    <module>/README.md             entry point
    <module>/pyproject.toml        installable package
    <module>/src/<pkg>/*.py        plugin factory, descriptor, router, settings
    <module>/tests/test_plugin.py  a first test that would fail if the descriptor broke
    <module>/docs/**               the 24 required documentation paths

Two steps cannot be automated, and the script says so instead of pretending:

  1. **The contract comes first.** Your endpoints must be declared in
     `MAIN/contracts/openapi/<module>.yaml` before the module may serve them, so a stub is
     written to `MAIN/.runtime/scaffold/<module>-openapi.yaml` for MAIN to adopt through
     `docs/contracts/change-protocol.md`. Until then `check_route_declarations()` will fail,
     which is correct: a module claiming URL space the contract does not describe is exactly
     what that gate exists to catch.
  2. **Installation is explicit.** Add the package to `MAIN/pyproject.toml`
     (`[project.optional-dependencies].modules` and `[tool.uv.sources]`), then
     `uv sync --all-groups --extra modules`.

Usage:
    python MAIN/scripts/scaffold_module.py B6 --summary "相似度检测" --provides b6-similarity \
        --requires b4-auth --route /api/v1/similarity
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

MAIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MAIN_ROOT.parent
sys.path.insert(0, str(MAIN_ROOT / "host" / "src"))
sys.path.insert(0, str(MAIN_ROOT / "sdk" / "src"))

from autograder_platform import contract_digest, declared_contract_version  # noqa: E402

REQUIRED_DOCS = [
    "docs/requirements/scope.md",
    "docs/requirements/traceability.md",
    "docs/requirements/acceptance.md",
    "docs/architecture/overview.md",
    "docs/architecture/components.md",
    "docs/architecture/models.md",
    "docs/architecture/boundaries.md",
    "docs/api/contract.md",
    "docs/api/events.md",
    "docs/database/schema.md",
    "docs/database/migrations.md",
    "docs/testing/strategy.md",
    "docs/testing/cases.md",
    "docs/adr/README.md",
    "docs/runbooks/local-development.md",
    "docs/runbooks/troubleshooting.md",
    "docs/phases/archive/README.md",
    "docs/collaboration/onboarding.md",
    "docs/collaboration/workpackages.md",
    "docs/collaboration/interfaces.md",
    "docs/collaboration/definition-of-done.md",
    "docs/collaboration/glossary.md",
    "docs/knowledge/README.md",
]

#: One line per required document: what belongs there. The scaffold writes a real (if short)
#: statement rather than a TODO, because this project's status vocabulary has no "unfinished
#: placeholder" state -- an unimplemented module says `骨架` and means it.
DOC_PURPOSE = {
    "docs/requirements/scope.md": "本模块负责什么、不负责什么、与哪些模块有边界。",
    "docs/requirements/traceability.md": "需求编号 → 工作包 → 状态 → 证据位置。",
    "docs/requirements/acceptance.md": "每条验收标准的逐条细则与验证方式。",
    "docs/architecture/overview.md": "上下文、组件、数据流与失败模式。",
    "docs/architecture/components.md": "内部构件与职责划分。",
    "docs/architecture/models.md": "领域模型、状态机与时序。",
    "docs/architecture/boundaries.md": "与其他模块的边界，以及**明确不做**的事。",
    "docs/api/contract.md": "消费的契约端点与提供的能力（字段定义以 `MAIN/contracts/**` 为准，本文不复制）。",
    "docs/api/events.md": "发布/订阅的事件与其去标识化约束。",
    "docs/database/schema.md": "本模块拥有的 schema 与表（DDL 以迁移为唯一事实源）。",
    "docs/database/migrations.md": "迁移约定、版本表与 head。",
    "docs/testing/strategy.md": "分层、覆盖风险与门禁命令。",
    "docs/testing/cases.md": "用例目录与真实读数。",
    "docs/adr/README.md": "本模块范围内的决策记录索引（跨模块决策在 MAIN 的 ADR 目录）。",
    "docs/runbooks/local-development.md": "本地起服务、跑测试的确切命令与实测结果。",
    "docs/runbooks/troubleshooting.md": "已知症状 → 原因 → 处理。",
    "docs/phases/archive/README.md": "已归档阶段的索引与归档门禁。",
    "docs/collaboration/onboarding.md": "新成员只读三个文件即可跑通门禁并认领工作。",
    "docs/collaboration/workpackages.md": "工作包划分与认领情况。",
    "docs/collaboration/interfaces.md": "对外接口的当前状态与缺口。",
    "docs/collaboration/definition-of-done.md": "本模块的完成定义。",
    "docs/collaboration/glossary.md": "模块内术语。",
    "docs/knowledge/README.md": "踩过的坑与知识笔记索引。",
}

PLUGIN_TEMPLATE = '''"""{module} plugin: {summary}."""

from __future__ import annotations

from autograder_platform import ModuleContext, ModuleDescriptor, PlatformModule
from fastapi import APIRouter

#: What this module provides to the rest of the platform. Capability names are free strings
#: today; keep them lower-case and hyphenated (see MAIN/docs/adr/README.md's open questions).
PROVIDES = {provides!r}
REQUIRES = {requires!r}


class {module}Module(PlatformModule):
    """The composition host loads this and nothing else -- no MAIN code knows {module} exists."""

    def __init__(self) -> None:
        self._descriptor = ModuleDescriptor(
            module="{module}",
            contract_version="{contract_version}",
            provides=PROVIDES,
            requires=REQUIRES,
            routes={routes!r},
        )

    @property
    def descriptor(self) -> ModuleDescriptor:
        return self._descriptor

    def router(self, context: ModuleContext) -> APIRouter:
        # `context.settings` is this module's own settings view (ADR-0008): unprefixed global
        # variables with every `{module}_`-prefixed one re-exposed under its bare name.
        from .api import build_router

        return build_router(context)

    def health(self):
        from .api import health_report

        return health_report()


def create_module() -> PlatformModule:
    """The entry point named in `module.yaml`."""
    return {module}Module()
'''

API_TEMPLATE = '''"""{module} HTTP surface.

Every path here must be declared in `MAIN/contracts/openapi/{module_lower}.yaml` **before** it
is served: `check_route_declarations()` fails when a module claims URL space the contract does
not describe, and that gate is deliberately strict because a client generated from the
contract is the only supported way to call this module.
"""

from __future__ import annotations

from fastapi import APIRouter

from autograder_platform import HealthReport, ModuleContext


def build_router(context: ModuleContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1{bare_route}/status")
    def status() -> dict[str, str]:
        """Placeholder-free status: this module implements no capability yet."""
        return {{"module": "{module}", "state": "骨架"}}

    return router


def health_report() -> HealthReport:
    """Readiness for this module. Return an unhealthy report rather than raising."""
    return HealthReport(module="{module}", healthy=True, detail="骨架：尚未实现能力")
'''

TEST_TEMPLATE = '''"""{module} plugin contract test.

The assertion is deliberately about the *descriptor*, because that is what the composition host
validates at start-up: a module whose declared contract version drifts from its `module.yaml`
cannot be loaded at all (see `MAIN/host/src/autograder_host/checks.py::check_module_descriptors`).
"""

from __future__ import annotations

import yaml
from pathlib import Path

from autograder_{module_lower}.plugin import create_module


def test_descriptor_matches_the_manifest() -> None:
    manifest = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "module.yaml").read_text(encoding="utf-8")
    )
    descriptor = create_module().descriptor
    assert descriptor.module == manifest["module"]
    assert descriptor.contract_version == manifest["contract_version"]
    assert set(descriptor.routes) == set(manifest["routes"])
    assert set(descriptor.provides) == set(manifest["provides"])
'''

AGENTS_TEMPLATE = """# {module} Agent 规则

面向在本仓库 `{module}/` 下工作的 AI Agent。改动前先读完本文件，并遵守
[../MAIN/docs/collaboration/module-doc-spec.md](../MAIN/docs/collaboration/module-doc-spec.md) 的三条铁律。

## 开始任务前

1. 读 [docs/README.md](docs/README.md)、[docs/requirements/traceability.md](docs/requirements/traceability.md)。
2. 读 MAIN 侧的任务书与相关 ADR：`../MAIN/docs/workstreams/{module}.md`、`../MAIN/docs/adr/README.md`。
3. 确认契约版本与摘要：本仓库 [contracts.lock](contracts.lock)（**不得改动**它）。
4. 检查 Git 状态，保留他人未提交的修改。

## 我负责什么 / 不负责什么

- 我负责：{summary}（**骨架阶段：尚未实现**）。
- 我不负责：题目与题库（B3）、提交与评测与分数（B2）、身份与课程（B4）、UI（B1）。
- 我不是 `MAIN/contracts/**` 的作者，也不修改它。

## 硬规则

- **契约先行**：接口改动先走 `../MAIN/docs/contracts/change-protocol.md`，再改实现。
- 只读写自己的 schema；禁止跨 schema 外键 / SQL / ORM / 视图；禁止超级用户连接。
- 禁止 mock 冒充已验证能力；禁止兼容层 / 别名 / 降级路径。
- 状态词只用：`骨架` / `待实现` / `已完成` / `阻塞`，且必须与真实证据一致。
- 安全：源码、参考答案、隐藏用例、凭据、超范围 PII 不得进入契约、示例、日志或事件。

## 验证

```powershell
cd {module}
uv sync --all-groups
uv run ruff check .
uv run pytest -q
```

跨模块门禁在 MAIN 侧执行：`cd ../MAIN && .\\.venv\\Scripts\\python.exe -m autograder_host.checks`。

## 契约变更

发现契约与实现不一致 → 不偷改契约、也不偷偷改实现对齐 → 提交 `CHG-*`，由 MAIN 判定哪边是权威。
"""

README_TEMPLATE = """# {module} — {summary}

本模块是 AutoGrader 的一个插件：宿主（MAIN 的 composition host）通过 Python entry point
装载它，**MAIN 代码中没有任何一处知道 {module} 的存在**。这正是本项目"即插即用"所要证明的性质
（见 [../MAIN/docs/adr/ADR-0013-open-module-count.md](../MAIN/docs/adr/ADR-0013-open-module-count.md)）。

## 当前状态

**骨架**：模块可被装载、可上报健康、可被门禁治理，但**尚未实现任何业务能力**。
本文件不得在能力实现前声称它可用。

## 快速开始

```powershell
cd {module}
uv sync --all-groups
uv run pytest -q
```

装载（需要先按 MAIN 的清单把它加入部署）：

```powershell
cd ../MAIN
uv sync --all-groups --extra modules
```

## 契约

本模块的对外接口声明在 `../MAIN/contracts/openapi/{module_lower}.yaml`；本仓库的
`contracts.lock` 固定了它所依据的契约版本与摘要。**模块侧不得复制字段定义**，只写"谁提供/谁消费"。

## 文档入口

[docs/README.md](docs/README.md)
"""


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise SystemExit(f"{path} already exists; refusing to overwrite")
    path.write_text(content, encoding="utf-8")
    print(f"  {path.relative_to(REPO_ROOT)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module", help="module name, e.g. B6 (pattern ^B[0-9]{1,3}$)")
    parser.add_argument("--summary", required=True, help="one line describing the module")
    parser.add_argument("--provides", action="append", default=[], help="capability provided")
    parser.add_argument("--requires", action="append", default=[], help="capability required")
    parser.add_argument("--route", action="append", default=[], help="route prefix, e.g. /api/v1/similarity")
    arguments = parser.parse_args(argv)

    import re

    if not re.match(r"^B[0-9]{1,3}$", arguments.module):
        raise SystemExit("module name must match ^B[0-9]{1,3}$ (see ADR-0013)")
    if not arguments.provides:
        raise SystemExit("--provides is required: a module that provides nothing is not loadable")
    if not arguments.route:
        raise SystemExit("--route is required: declare at least one route prefix")

    module = arguments.module
    package = f"autograder_{module.lower()}"
    root = REPO_ROOT / module
    contract_version = declared_contract_version(MAIN_ROOT / "contracts")
    digest = contract_digest(MAIN_ROOT / "contracts")

    print(f"scaffolding {module} at contract {contract_version} ({digest[:12]}…)")
    write(root / "module.yaml", (
        "schema_version: 1\n"
        f"module: {module}\n"
        f"contract_version: {contract_version}\n"
        f"entry_point: {package}.plugin:create_module\n"
        f"provides: [{', '.join(arguments.provides)}]\n"
        f"requires: [{', '.join(arguments.requires)}]\n"
        f"routes: [{', '.join(arguments.route)}]\n"
        "events:\n"
        "  publishes: []\n"
        "  subscribes: []\n"
        "database:\n"
        '  schema: "none"\n'
        '  migration_head: "none"\n'
    ))
    write(root / "contracts.lock", (
        f"contract_version: {contract_version}\n"
        "source: ../MAIN/contracts\n"
        "digest_policy: sha256\n"
        f"digest: {digest}\n"
        "status: baseline\n"
    ))
    write(root / "pyproject.toml", (
        "[project]\n"
        f'name = "{package.replace("_", "-")}"\n'
        'version = "0.1.0"\n'
        f'description = "{arguments.summary}"\n'
        'requires-python = ">=3.12,<3.13"\n'
        "dependencies = [\n"
        '  "autograder-platform==0.1.0",\n'
        '  "fastapi>=0.115,<1",\n'
        '  "pydantic>=2.10,<3",\n'
        "]\n\n"
        "[project.entry-points.'autograder.modules']\n"
        f'{package.replace("_", "-")} = "{package}.plugin:create_module"\n\n'
        "[dependency-groups]\n"
        'dev = ["pytest>=8.3,<9", "pyyaml>=6.0,<7", "ruff>=0.9,<1"]\n\n'
        "[build-system]\n"
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n\n'
        "[tool.hatch.build.targets.wheel]\n"
        f'packages = ["src/{package}"]\n'
    ))
    write(root / "src" / package / "__init__.py", f'"""{arguments.summary}"""\n')
    write(root / "src" / package / "plugin.py", PLUGIN_TEMPLATE.format(
        module=module,
        summary=arguments.summary,
        provides=arguments.provides,
        requires=arguments.requires,
        routes=arguments.route,
        contract_version=contract_version,
    ))
    bare_route = arguments.route[0].removeprefix("/api/v1")
    write(root / "src" / package / "api.py", API_TEMPLATE.format(
        module=module, module_lower=module.lower(), bare_route=bare_route
    ))
    write(root / "tests" / "test_plugin.py", TEST_TEMPLATE.format(
        module=module, module_lower=module.lower()
    ))
    write(root / "AGENTS.md", AGENTS_TEMPLATE.format(module=module, summary=arguments.summary))
    write(root / "README.md", README_TEMPLATE.format(
        module=module, module_lower=module.lower(), summary=arguments.summary
    ))
    write(root / "docs" / "README.md", (
        f"# {module} 文档索引\n\n"
        f"**状态：骨架。** 下面每份文档的用途见 MAIN 的 `module-doc-spec.md`；本索引是入口，"
        "权威事实不在本仓库的散文中，而在 `../MAIN/contracts/**` 与本仓库的迁移里。\n\n"
        + "\n".join(f"- [{path}]({path.removeprefix('docs/')}) — {DOC_PURPOSE[path]}"
                    for path in REQUIRED_DOCS)
        + "\n"
    ))
    for relative in REQUIRED_DOCS:
        write(root / relative, (
            f"# {module} · {relative.removeprefix('docs/').removesuffix('.md')}\n\n"
            f"**状态：骨架。**\n\n"
            f"{DOC_PURPOSE[relative]}\n\n"
            "> 本文在能力实现前不得声称该能力可用；状态词只用 `骨架` / `待实现` / `已完成` / `阻塞`。\n"
        ))
    write(root / "docs" / "phases" / "active" / "phase-00-scaffold.md", (
        f"# Phase 00 — {module} 骨架\n\n"
        "## 已完成\n\n"
        "- 模块可被 composition host 装载（descriptor + entry point + `module.yaml`）。\n"
        "- 契约版本与摘要已由 `MAIN/scripts/relock_contracts.py` 的同一实现计算并锁定。\n"
        "- 文档治理所需文件齐备。\n\n"
        "## 未完成（不得表述为已完成）\n\n"
        "- **尚未实现任何业务能力**：本模块当前只提供健康与占位状态。\n"
        "- 对外接口尚未写入 `MAIN/contracts/openapi/` —— 见下方清单第 1 步。\n"
        "- 未安装到部署（见清单第 2 步），因此尚未在真实组合中验证。\n"
    ))

    stub = MAIN_ROOT / ".runtime" / "scaffold" / f"{module.lower()}-openapi.yaml"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        "openapi: 3.1.0\n"
        "info:\n"
        f"  title: AutoGrader {module} API\n"
        f"  version: {contract_version}\n"
        f"  description: |\n    {arguments.summary}\n"
        "  license:\n    name: Course project (internal)\n"
        "servers:\n  - url: /\n    description: Served by the MAIN composition host.\n\n"
        "security:\n  - bearerAuth: []\n\n"
        "paths:\n"
        f"  {arguments.route[0]}/status:\n"
        "    get:\n"
        "      operationId: getStatus\n"
        f"      summary: {module} status\n"
        "      tags: [" + module.lower() + "]\n"
        "      responses:\n"
        "        '200':\n"
        "          description: Module status.\n"
        "          content:\n"
        "            application/json:\n"
        "              schema:\n"
        "                type: object\n"
        "                required: [module, state]\n"
        "                properties:\n"
        "                  module: {type: string}\n"
        "                  state: {type: string}\n"
        "                additionalProperties: false\n"
        "        '401': {$ref: './common.yaml#/components/responses/Unauthorized'}\n",
        encoding="utf-8",
    )
    print(f"  {stub.relative_to(REPO_ROOT)}  (contract stub for MAIN to adopt)")

    print(
        f"\n{module} scaffolded. Three things remain, and none of them is optional:\n"
        f"  1. MAIN adopts the contract stub `{stub.relative_to(REPO_ROOT)}` as\n"
        f"     `contracts/openapi/{module.lower()}.yaml` through docs/contracts/change-protocol.md,\n"
        f"     bumps the contract version and runs scripts/relock_contracts.py. Until then\n"
        f"     check_route_declarations() fails -- correctly, because the module claims URL\n"
        f"     space the contract does not describe.\n"
        f"  2. Add the package to MAIN/pyproject.toml: `[project.optional-dependencies].modules`\n"
        f"     and `[tool.uv.sources]`, then `uv sync --all-groups --extra modules`.\n"
        f"  3. Claim requirements in {module}/docs/requirements/traceability.md before writing code.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
