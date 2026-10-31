#!/bin/bash
set -eu

source_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
install_root="${WECHAT_READER_HOME:-$HOME/.wechat-reader}"
mkdir -p "$install_root"
log="$install_root/install.log"
finish() {
    status=$?
    if [ "$status" -ne 0 ]; then
        echo ""
        echo "[安装未完成] 请检查网络后重新运行。详细记录：$log"
        tail -n 12 "$log" 2>/dev/null || true
    fi
    if [ "${WECHAT_READER_NONINTERACTIVE:-0}" != "1" ] && [ -t 0 ]; then
        read -r -p "按回车关闭窗口…" ignored || true
    fi
    exit "$status"
}
trap finish EXIT
printf '\nwechat-reader 一键安装\n首次安装需要联网下载运行环境，请稍候。\n\n'
export UV_UNMANAGED_INSTALL="$install_root/tools"
export UV_PYTHON_INSTALL_DIR="$install_root/python"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$install_root/cache}"
uv="$install_root/tools/uv"
echo "准备安装工具…"
if [ ! -x "$uv" ]; then
    curl --fail --location --silent --show-error --connect-timeout 15 --max-time 180 \
        https://astral.sh/uv/install.sh -o "$install_root/uv-installer.sh" >>"$log" 2>&1
    sh "$install_root/uv-installer.sh" >>"$log" 2>&1
fi
echo "准备独立 Python 环境…"
python="$install_root/venv/bin/python"
if [ ! -x "$python" ]; then
    "$uv" venv --python 3.11 "$install_root/venv" >>"$log" 2>&1
fi
echo "安装 wechat-reader…"
"$uv" pip install --python "$python" --upgrade "$source_dir" >>"$log" 2>&1
"$python" -m wechat_reader_setup
