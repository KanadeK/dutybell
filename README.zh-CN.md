# DutyBell

**一个家庭计时器，所有屏幕同步；任意一人确认，所有设备同时停止提醒。**

[English](README.md) · [架构](docs/ARCHITECTURE.md) · [API](docs/API.md) · [故障排查](docs/TROUBLESHOOTING.md)

DutyBell 是一个小型、零运行时依赖、可自托管的家庭责任接力计时器。它适合遛狗间隔、
洗衣检查、烤箱查看、植物巡检等非安全关键场景。它不是只有界面的演示：服务端统一计时，
SQLite 保存状态和只追加事件历史，长轮询把更新同步到所有客户端，乐观版本锁会明确拒绝并发
覆盖。

## 能做什么

- 任意已连接设备都能开始、暂停、继续、重置、停止或确认。
- 一台设备确认后，所有客户端收到新状态并停止本地铃声。
- 确认后可自动开始下一轮，并轮换下一位负责人。
- 不需要账号、云数据库、分析 SDK 或第三方前端依赖。
- 导出包含 JSON、CSV、HTML 与 SHA-256 清单的确定性 ZIP，并可离线验证。
- 可作为 PWA 安装到手机主屏幕。

DutyBell **不能**用于服药、火警、工业生产、紧急事件等安全关键提醒。浏览器和网络可能休眠、
断开；涉及健康或安全后果时必须使用经过认证的专用设备。

## 快速运行

需要 Python 3.11 或更高版本：

```bash
git clone https://github.com/KanadeK/dutybell.git
cd dutybell
python -m venv .venv
python -m pip install .
dutybell doctor --database ./data/dutybell.db
dutybell serve --host 0.0.0.0 --port 8742 --database ./data/dutybell.db
```

主机打开 `http://127.0.0.1:8742`；同一可信局域网内的其他设备打开
`http://主机局域网IP:8742`。创建房间后请保存私密加入链接。

链接密钥位于 URL 的 `#` 后，正常 HTTP 请求和服务日志不会收到这部分；页面加载后会把密钥
放入授权请求头。纯 HTTP 的局域网流量仍可能被监听，因此对公网开放前必须使用 HTTPS 反向代理。

Docker 用户可直接运行：

```bash
docker compose up --build
```

设置环境变量 `DUTYBELL_CREATE_TOKEN` 可以限制新房间创建；已有房间仍由各自的私密密钥保护。

## 完整验收

```bash
python -m pip install -r requirements-dev.lock
python scripts/release_check.py
```

门禁会按失败即停止的方式执行：格式、Lint、严格类型检查、90% 分支覆盖率、前端核心测试、
密钥扫描、相隔两秒的字节级可复现构建、压缩包内部时间戳检查、全新虚拟环境安装、CLI 诊断，
以及真实 HTTP 创建/读取/确认/并发冲突流程。只有最后出现 `RELEASE CHECK PASSED` 才算通过；
产物和 `SHA256SUMS` 位于 `dist/`。

失败时不要跳过检查。按照[故障修复矩阵](docs/TROUBLESHOOTING.md#release-gate-repair-matrix)
处理对应阶段，再从头运行完整门禁。

## 数据边界

- 数据只保存在指定 SQLite 文件中；没有追踪器、Cookie、CDN 或第三方字体。
- 房间编号不是秘密，加入密钥才是授权凭据；持有私密链接的人可以读写该房间。
- SQLite 只保存密钥的 SHA-256 摘要；导出文件不会包含密钥。
- 客户端填写的操作者名字只用于事件归属，不等于身份认证。

公网部署前请阅读[威胁模型](docs/THREAT_MODEL.md)。项目采用 [MIT 许可证](LICENSE)。
