"""宿主适配层：只做三件事——声明 descriptor、把四个路由族拼成一个 `APIRouter`、把分项健康汇成
宿主的 `HealthReport`。装配策略在 `autograder_b2.runtime`，本模块不做装配决策。

## `descriptor.routes` 与 `module.yaml`

`module.yaml` 的 `routes` 是**前缀声明**（`/api/v1/submissions`、`/api/v1/evaluation-runs`、
`/api/v1/grades`、`/api/v1/intake`、`/api/v1/reference-verifications`），
`B4/src/autograder_b4/registry.py::compare_descriptor_to_manifest`
要求 descriptor 与之**逐项相等**（排序后比较），因此这里保持原值不动，服务的是这些具体路径：

| 具体路径 | 所属前缀 | 路由构建处 |
|---|---|---|
| `POST /api/v1/submissions`、`GET /api/v1/submissions/{id}` | `/api/v1/submissions` | `intake.api` |
| `GET /api/v1/evaluation-runs/{id}`、`/events`、`/report`、`/appeal-pack`、`/commentary`、`POST .../commentary`、`GET /api/v1/evaluation-runs/quality-summary` | `/api/v1/evaluation-runs` | `api.evaluation_routes` |
| `GET /api/v1/grades`、`/api/v1/grades/export` | `/api/v1/grades` | `api.grades_routes` |
| `GET /api/v1/intake/quarantines`、`POST .../{quarantine_id}/resolve`（CHG-0014） | `/api/v1/intake` | `intake.api`（两条读/写路径都由 `intake.quarantine` 判定） |
| `POST /api/v1/reference-verifications`（`CHG-0019`） | `/api/v1/reference-verifications` | `api.reference_routes` |

`/api/v1/grades` 一行曾是**已记录的声明缺口**（CHG-0005 缺口 3）：成绩两条路径没有被任何 `routes`
前缀覆盖，于是 `b2.yaml` 开头那句 "Every path below is declared in B2/module.yaml `routes`" 对它们不成立，
宿主/registry 的"重复路由"保护也看不见 B2 对 `/api/v1/grades` 的归属（它只做字面相等比较）。
MAIN 已裁决（2026-09-16）补入该前缀；`/api/v1/intake` 由 CHG-0014 以同一方式补入——
隔离区复核是"邮件没能变成提交"这件事的出口，因此有自己的前缀，而不是塞进 `/api/v1/submissions`。
`/api/v1/reference-verifications` 由 `CHG-0019` 以同一方式补入：它是**出题发布门禁**的执行入口，
既不属于提交、也不属于成绩，因此同样有自己的前缀。
`quality-summary` 与 `appeal-pack` 落在既有的 `/api/v1/evaluation-runs` 前缀下，无需改声明；
前者是**字面段**，注册顺序必须在 `{evaluation_run_id}` 之前（`CHG-0015` §3，见 `api.evaluation_routes`）。

## `descriptor.events` 与 `module.yaml`

SDK 的 `ModuleDescriptor.events` 是**扁平元组**，无法表达 publishes/subscribes 两个方向，而
registry 比较的是 `publishes ∪ subscribes`（`ModuleManifest.events`）并要求两侧相等。因此这里列出
`module.yaml` 声明的全部 6 个 subject（5 个 publishes + 1 个 subscribes）：漏掉任何一个都会让
registry 判定"descriptor.events omits ... declared in module.yaml"并中止宿主启动。
"""

from __future__ import annotations

from autograder_platform import HealthReport, ModuleDescriptor
from fastapi import APIRouter

from autograder_b2.api.evaluation_routes import build_evaluation_router
from autograder_b2.api.grades_routes import build_grades_router
from autograder_b2.api.reference_routes import build_reference_router
from autograder_b2.intake.api import build_router as build_intake_router
from autograder_b2.runtime import B2Runtime, build_b2_runtime


class B2Module:
    descriptor = ModuleDescriptor(
        module="B2",
        contract_version="0.18.0",
        provides=("b2-submissions",),
        requires=("host", "b4-auth", "b3-questions"),
        routes=(
            "/api/v1/submissions",
            "/api/v1/evaluation-runs",
            "/api/v1/grades",
            # CHG-0014: quarantine review lives under its own prefix -- it is not a submission
            # endpoint, it is how a teacher finds mail that never became one. B4's registry
            # compares these to `module.yaml` item for item, so both sides carry it.
            "/api/v1/intake",
            # CHG-0019: reference verification is the execution entry of the publish gate. It is
            # neither a submission nor a grade, so it carries its own prefix on both sides.
            "/api/v1/reference-verifications",
        ),
        events=(
            "autograder.v1.submission.accepted",
            "autograder.v1.evaluation.started",
            "autograder.v1.evaluation.progress",
            "autograder.v1.evaluation.completed",
            "autograder.v1.evaluation.failed",
            # 订阅方向（`module.yaml: events.subscribes`）：SDK 的扁平元组装不下方向，只能并进来。
            "autograder.v1.question.version.published",
        ),
    )

    def __init__(self) -> None:
        self._runtime: B2Runtime | None = None

    def _runtime_for(self, context) -> B2Runtime:
        # 宿主在启动时调用 `router()`；此处按需要装配一次并按引用复用（装配不连库、不连 broker）。
        if self._runtime is None:
            settings_source = getattr(context, "settings", {}) or {}
            self._runtime = build_b2_runtime(settings_source)
        return self._runtime

    def router(self, context) -> APIRouter:
        """四个路由族的唯一注册点：intake 4 条 + 评测 7 条 + 成绩 2 条 + 参考解答验证 1 条。"""
        runtime = self._runtime_for(context)
        router = APIRouter()
        router.include_router(build_intake_router(runtime.intake))
        router.include_router(build_evaluation_router(runtime))
        router.include_router(build_grades_router(runtime))
        router.include_router(build_reference_router(runtime))
        return router

    def health(self, context) -> HealthReport:
        healthy, detail = self._runtime_for(context).health_summary()
        return HealthReport("B2", healthy, detail)

    def configure_app(self, app, context) -> None:
        """Install B2's RFC 9457 handlers on the host application.

        A router cannot register exception handlers, so without this the host answers
        B2's domain errors (and its ``RequestValidationError`` mapping) as bare 500s.
        """
        from autograder_b2.api.errors import install_problem_handlers

        install_problem_handlers(app)


def create_module() -> B2Module:
    return B2Module()
