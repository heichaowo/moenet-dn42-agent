# MoeNet DN42 Agent

纯粹的 DN42 节点 Agent，负责：
- 从 control-plane 拉取配置
- 渲染并应用 BIRD/WireGuard 配置
- 保存 last_state.json 用于灾难恢复
- 上报健康状态

## 项目结构

```
moenet-dn42-agent/
├── src/
│   ├── main.py              # Agent 主程序入口
│   ├── config.py            # 配置加载
│   ├── client/
│   │   └── control_plane.py # Control-Plane API 客户端
│   ├── state/
│   │   └── manager.py       # last_state.json 管理
│   ├── renderer/
│   │   ├── bird.py          # BIRD 配置渲染
│   │   └── wireguard.py     # WireGuard 配置渲染
│   ├── executor/
│   │   ├── bird.py          # BIRD 配置应用
│   │   └── wireguard.py     # WireGuard 接口管理
│   └── daemon/
│       └── sync.py          # 后台同步守护进程
├── templates/
│   ├── bird_peer.conf.j2
│   └── wireguard.conf.j2
├── systemd/
│   └── moenet-agent.service
├── requirements.txt
└── README.md
```

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 配置
cp config.example.json config.json
# 编辑 config.json 设置 control-plane URL 和节点信息

# 运行
python src/main.py
```

## 配置文件

```json
{
  "control_plane_url": "https://cp.moenet.work",
  "control_plane_token": "your_token",
  "node_name": "jp.edge.moenet.work",
  "sync_interval": 60,
  "heartbeat_interval": 30,
  "state_path": "/var/lib/moenet-agent/last_state.json"
}
```

## Systemd 服务

```bash
sudo cp systemd/moenet-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable moenet-agent
sudo systemctl start moenet-agent
```

## 与 Control-Plane 交互

Agent 通过以下 API 与 control-plane 交互：

| API | 用途 |
|-----|------|
| `GET /api/v1/agent/config` | 拉取节点配置 |
| `POST /api/v1/agent/heartbeat` | 发送心跳 |
| `POST /api/v1/agent/state` | 上报 last_state.json |

## last_state.json

Agent 会持久化保存已应用的配置，用于：
1. 配置变更检测（对比 version_hash）
2. 灾难恢复（control-plane 可从 agent 收集重建）

---

**注意**: Telegram Bot 和用户管理功能已迁移至 `moenet-dn42-control-plane` 仓库。
