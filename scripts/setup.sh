#!/usr/bin/env bash
# 幂等创建/修复本地开发环境，并自动排除 macOS 自带的 Python 3.8。
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

pick_python() {
    local candidate path
    for candidate in "${PYTHON:-}" python3.14 python3.13 python3.12 python3.11 python3; do
        [[ -n "$candidate" ]] || continue
        path="$(command -v "$candidate" 2>/dev/null || true)"
        [[ -n "$path" ]] || continue
        [[ "$path" == /usr/bin/python3 ]] && continue
        if "$path" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            printf '%s\n' "$path"
            return 0
        fi
    done
    return 1
}

PY="$(pick_python)" || {
    echo "ERROR: 未找到 Python >= 3.11。macOS 自带的 /usr/bin/python3 (3.8.2) 不可用。" >&2
    echo "请安装 3.11+，或显式指定：PYTHON=/path/to/python3.13 ./scripts/setup.sh" >&2
    exit 1
}

echo "使用解释器: $PY ($("$PY" -V 2>&1))"

# 已存在的 venv 用同一解释器重跑即可修正 pyvenv.cfg，不删已装依赖。
if [[ -f .venv/pyvenv.cfg ]]; then
    echo "检测到已有 .venv，校验并修正配置..."
fi
"$PY" -m venv .venv

PY_VERSION="$(./.venv/bin/python -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
CFG_VERSION="$(awk -F' = ' '/^version = /{print $2}' .venv/pyvenv.cfg)"
if [[ "$PY_VERSION" != "$CFG_VERSION" ]]; then
    echo "ERROR: pyvenv.cfg ($CFG_VERSION) 与 .venv/bin/python ($PY_VERSION) 不一致" >&2
    echo "运行: \"$PY\" -m venv --clear .venv && ./.venv/bin/pip install -e '.[dev]'" >&2
    exit 1
fi

./.venv/bin/pip install -e ".[dev]" --disable-pip-version-check --quiet

echo
echo "环境就绪: $ROOT/.venv  ($PY_VERSION)"
echo "下一步:"
echo "  source .venv/bin/activate"
echo "  cp .env.example .env"
echo "  python -m shopping_deal_agent demo"
