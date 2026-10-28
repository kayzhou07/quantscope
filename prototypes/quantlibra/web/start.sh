#!/bin/bash
# QuantLibra 一键启动脚本
# 用法：./start.sh   （或 bash start.sh）

cd "$(dirname "$0")"

# 1. 检查 token
if [ -z "$TUSHARE_TOKEN" ]; then
  echo "❌ 未设置 TUSHARE_TOKEN 环境变量。"
  echo "   请先执行：export TUSHARE_TOKEN=你的token"
  echo "   然后重新运行：./start.sh"
  exit 1
fi

# 2. 检查端口是否已在运行
if lsof -i :8088 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "✅ 服务器已在运行（端口 8088）"
else
  echo "🚀 启动服务器..."
  nohup python3 -u server.py > server.log 2>&1 &
  sleep 2
  echo "✅ 服务器已启动（PID $!），日志：server.log"
fi

# 3. 打开浏览器
echo "🌐 打开 http://localhost:8088"
sleep 1
open http://localhost:8088 2>/dev/null || xdg-open http://localhost:8088 2>/dev/null || echo "请手动打开 http://localhost:8088"

echo ""
echo "停止服务器：pkill -f 'server.py'"
