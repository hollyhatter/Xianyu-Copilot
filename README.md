# Xianyu-Copilot

一套“闲鱼网页版 IM 自动回复”脚本模板：使用 **DrissionPage + 已登录的 Chrome（remote debugging）** 读取未读红点并自动回复。


## 目录结构

- `browser_engine.py`：连接 Chrome、抓取未读、按昵称点击会话、拟人化输入并发送
- `automation/poller.py`：低 token 轮询器（有未读才调用 OpenClaw）
- `openclaw_client.py`：调用 `openclaw agent --json` 生成回复文本
- `context_manager.py`：本地 SQLite（可用于保存上下文/指纹）
- `prompts/*.txt`：话术模板（已脱敏，需自行改成你的业务规则）
- `launchd/*.plist.example`：macOS `launchd` 定时触发模板

## 运行前置

- macOS
- Python 3.9+（建议 venv）
- 已安装并登录的 Google Chrome
- Chrome 以 remote debugging 启动
- （可选）OpenClaw CLI（用于生成回复）

## 启动 Chrome（remote debugging）

使用独立 profile 目录启动，避免影响日常浏览器：

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/Documents/XianyuAgentProfile"
```

然后在该 Chrome 中打开并登录：`https://www.goofish.com/im`

## 快速运行（手动）

```bash
export CHROME_DEBUG_PORT=9222
export XIANYU_IM_URL="https://www.goofish.com/im"
export XIANYU_ITEM_DESC="（你的商品/服务名称）"

# OpenClaw（可选）
export NODE_BIN="node"
export OPENCLAW_BIN="openclaw"

python3 automation/poller.py
```



## 免责声明

仅用于学习与自动化测试研究。请遵守平台规则与当地法律法规，风险自担。

