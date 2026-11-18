#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
case "$ROOT" in *'"'*|*'\'*|*'['*|*']'*|*'{'*|*'}'*|*'*'*|*'?'*|*'%'*|*'
'*|*"$(printf '\r')"*) echo '请将软件放到不含引号、换行或通配符的目录'; exit 1;; esac
chmod +x "$ROOT/测功机" "$ROOT/启动测功机.sh"
# Ubuntu 24.04+ restricts unprivileged user namespaces through AppArmor.
if [ -f /etc/apparmor.d/abi/4.0 ] && command -v apparmor_parser >/dev/null 2>&1; then
  PROFILE=$(mktemp)
  trap 'rm -f "$PROFILE"' EXIT
  ID=$(printf '%s' "$ROOT" | sha256sum | cut -c1-12)
  printf 'abi <abi/4.0>,\ninclude <tunables/global>\nprofile inntorque-%s "%s/versions/*/inntorque-bench" flags=(unconfined) {\n  userns,\n}\n' "$ID" "$ROOT" > "$PROFILE"
  sudo install -m 644 "$PROFILE" "/etc/apparmor.d/inntorque-$ID"
  sudo apparmor_parser -r "/etc/apparmor.d/inntorque-$ID"
fi
APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
printf '[Desktop Entry]\nType=Application\nName=Inntorque 测功机\nExec="%s/测功机"\nTerminal=false\nCategories=Science;Engineering;\n' "$ROOT" > "$APPS/inntorque-portable.desktop"
chmod +x "$APPS/inntorque-portable.desktop"
echo '本地启动入口已配置。串口和 USB 权限按适配器要求配置；没有开启设备输出。'
