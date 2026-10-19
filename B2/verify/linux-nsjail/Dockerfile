# =============================================================================
# Linux nsjail 验收环境（AutoGrader B2 · 大类 5「Linux 沙箱验收」）
#
# 目的：宿主是 Windows，nsjail 依赖 Linux 命名空间与 cgroup v2，**无法原生运行**
#       （ADR-0004「后果」节）。因此把**真实**的 nsjail 验收放进一个特权 Linux
#       容器执行。本镜像的职责只有一件：让容器内有一个能真正启动、隔离真正生效
#       的 nsjail，且构建过程可复现。
#
# 本机实测得到的三条硬事实（详见同目录 README.md「实测记录」）：
#   1. Debian bookworm 官方 apt 源**没有 nsjail 包**——`apt-cache search nsjail`
#      返回空，只有 firejail/jailkit。故本镜像**从源码构建** nsjail 并钉版本。
#      （任务书假设「nsjail 为 apt 包」不成立，已如实记录。）
#   2. 容器内 nsjail 需要额外权限：`--privileged` + `--cgroupns=host`。缺任一项都会
#      在 mount namespace / cgroup 阶段失败。这正是 ADR-0004 §「遗留风险」第 1 条
#      「容器化部署中运行 nsjail 需要额外权限，与最小权限原则产生张力」的落点。
#   3. 本机 Docker Hub 加速器 mirror.ntruss.com DNS 不可解析；容器内直连 github.com
#      不稳定（GnuTLS recv error），故基础镜像可覆盖、源码构建带重试。
#
# 分层说明：apt 安装单独一层（约 10 分钟，命中缓存后重试源码构建不再重跑）；
# 源码构建单独一层并带 5 次重试，避免网络抖动使整次构建作废。
#
# 构建（在本仓库根目录执行）：
#   docker build -f verify/linux-nsjail/Dockerfile -t autograder-b2-nsjail:local .
# 运行见 README.md「如何运行」。
# =============================================================================

# 基线：Debian bookworm + Python 3.12（任务书要求 Python 3.12；bookworm 主源只有
# 3.11，用官方 python:3.12-slim-bookworm 比启用 backports 更可复现）。
# 本机 Docker Hub 加速器失效时用 --build-arg 覆盖为可访问的镜像源。
ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE}

# nsjail 上游发布标签（钉死版本；升级需同步 README 的实测矩阵）
ARG NSJAIL_VERSION=3.4
# pip 索引：本机 PyPI 直连不可用，默认走清华镜像（宿主 pip.ini 同样配置）。
# 在可直连 PyPI 的环境用 --build-arg PIP_INDEX=https://pypi.org/simple 覆盖。
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /usr/local/bin/

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# -----------------------------------------------------------------------------
# 层 1：nsjail 构建依赖（bison/flex/protobuf/libnl/libcap/kconfig-frontends）。
# libnl-route-3-dev 是必需的：缺它 net.cc 会因 <netlink/route/link.h> 编译失败
# （本机实测报错，已记录在 README）。
# -----------------------------------------------------------------------------
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        bison \
        flex \
        libprotobuf-dev \
        protobuf-compiler \
        libnl-3-dev \
        libnl-route-3-dev \
        libcap-dev \
        libseccomp-dev \
        kconfig-frontends \
        g++ \
        make \
        pkg-config \
        git \
        ca-certificates; \
    rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# 层 2：源码获取 + 编译 + 安装 nsjail（带重试）。
# `make` 内部还会 `git submodule update --init` 拉 kafel 子模块，同属网络操作，
# 因此对整个「获取 + 编译」序列重试；每一轮前清干净工作目录保证幂等。
# -----------------------------------------------------------------------------
RUN set -eux; \
    git config --global http.version HTTP/1.1; \
    git config --global advice.detachedHead false; \
    ok=0; \
    for i in 1 2 3 4 5; do \
        rm -rf /usr/local/src/nsjail; \
        mkdir -p /usr/local/src; \
        if git clone --depth 1 --branch "${NSJAIL_VERSION}" \
               https://github.com/google/nsjail.git /usr/local/src/nsjail \
           && make -C /usr/local/src/nsjail -j"$(nproc)"; then \
            ok=1; \
            break; \
        fi; \
        echo "nsjail 获取/编译第 ${i} 次失败，重试中……"; \
        sleep 5; \
    done; \
    test "${ok}" = "1"; \
    test -x /usr/local/src/nsjail/nsjail; \
    install -m 0755 /usr/local/src/nsjail/nsjail /usr/local/bin/nsjail; \
    rm -rf /usr/local/src/nsjail

# -----------------------------------------------------------------------------
# 层 3：验收工具 + **构建期自检**。
#
# 这里刻意**不**卸载构建依赖：`apt-get purge --auto-remove` 会连带移除 orphan 依赖
# （本机实测它开始 purging libpython3.11-minimal），有可能删掉与 nsjail 无关但基础镜像
# 依赖的包，属不可控风险；镜像体积不是本环境的约束。构建依赖留在镜像内也不构成沙箱风险
# （沙箱的权限面由 capability/namespace 决定，不由包清单决定）。
#
# 末尾三条自检是**强制**的：`nsjail --help` 必须真的能跑，避免产出「构建成功但 nsjail
# 起不来」的镜像（不得把「构建成功」当「能用」）。
# 注意：nsjail 3.4 **没有** `--version` 选项——传它会打 usage 并以 255 退出（本机实测），
# 故自检用 `--help` 的输出内容作为判据。
# -----------------------------------------------------------------------------
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        iproute2 \
        procps \
        coreutils \
        util-linux \
        tini; \
    rm -rf /var/lib/apt/lists/*; \
    nsjail --help 2>&1 | head -5; \
    nsjail --help 2>&1 | grep -q "Examples:"; \
    test -x /usr/local/bin/nsjail

# -----------------------------------------------------------------------------
# 层 4：验收所需的 Python 依赖。
#
# pytest 驱动 `tests/linux/**`；其余是**项目运行期依赖**（与 `pyproject.toml` 的 dependencies 对齐），
# 因为 `tests/linux/test_nsjail_runner_acceptance.py` 要在容器内**导入真实的 `src/autograder_b2/**`**：
# 把真实 runner（`run_case` + `PosixNsjailLauncher`）与真实编排层接进真 nsjail 跑验收，
# 而不是只在容器外读配置（此前那句"本镜像不导入 src/**"的假设已作废）。
# 构建期自检只导入第三方依赖；`src/**` 由 `PYTHONPATH` 在运行时提供（镜像不 COPY 源码）。
# 若本环境访问不到 PyPI 镜像（本机实测过 Tsinghua 镜像 TLS EOF），构建会在本层失败——此时改用
# `verify/linux-nsjail/README.md` 第 3.3 节的**离线交叉安装**：在宿主执行
#   uv pip install --target .pytest-tmp/linux-site --python-platform x86_64-unknown-linux-gnu ...
# 再用 `-e PYTHONPATH=/work/B2/src:/work/B2/.pytest-tmp/linux-site` 跑容器（依赖不进仓库）。
# -----------------------------------------------------------------------------
RUN set -eux; \
    pip install --no-cache-dir \
        --index-url "${PIP_INDEX}" \
        --trusted-host "${PIP_TRUSTED_HOST}" \
        pytest sqlalchemy "psycopg[binary]" \
        "fastapi>=0.115,<1" "jsonschema>=4.23,<5" "pyjwt>=2.10,<3" \
        "cryptography>=44,<47" "openpyxl>=3.1,<4" "pydantic>=2.10,<3"; \
    python3 -c "import sys, pytest, sqlalchemy, psycopg, fastapi, jsonschema, jwt, cryptography, openpyxl, pydantic; \
print('python', sys.version.split()[0]); \
print('pytest', pytest.__version__); \
print('sqlalchemy', sqlalchemy.__version__); \
print('psycopg', psycopg.__version__); \
print('fastapi', fastapi.__version__); \
print('jsonschema', jsonschema.__version__); \
print('pyjwt', jwt.__version__); \
print('openpyxl', openpyxl.__version__); \
print('pydantic', pydantic.VERSION)"

# nsjail 以 root 运行（需要 CAP_SYS_ADMIN 建立 mount/pid/cgroup namespace），
# 待测代码由 nsjail 内部 uidmap 映射为非 root（见测试内 policy 配置）。
WORKDIR /work
CMD ["/bin/bash"]
