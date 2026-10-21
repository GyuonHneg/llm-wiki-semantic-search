#!/usr/bin/env bash
# 从钥匙串读取 token 并输出到 stdout，供调用方注入环境变量。
# 设计原则：token 不落盘、不进日志、不进仓库。
set -euo pipefail

SERVICE="${WIKI_KEYCHAIN_SERVICE:-com.example.wiki-bridge}"

case "$(uname -s)" in
  Darwin)
    security find-generic-password -s "$SERVICE" -w 2>/dev/null && exit 0
    ;;
esac

# 非 macOS / 钥匙串未配置时，退回 secret 文件或 CI 变量
for f in /run/secrets/wiki_token "${WIKI_TOKEN_FILE:-}"; do
  [ -n "$f" ] && [ -r "$f" ] && { cat "$f"; exit 0; }
done

echo "未找到 token：请在钥匙串添加 service=$SERVICE，或设置 WIKI_TOKEN_FILE" >&2
exit 1
