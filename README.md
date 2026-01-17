# MoeNet DN42 Agent

纯粹的 DN42 节点 Agent，负责：

- 🔄 从 control-plane 拉取配置并自动应用
- 🔐 动态管理 WireGuard 隧道和防火墙规则
- 🔄 自动注册节点，获取唯一 `node_id` 并持久化
- 🕸️ 管理 Mesh 网络 (IGP underlay)，支持定期重试
- 🌐 配置 Loopback 接口 (dummy0) 及 DN42 地址
- 💾 保存 last_state.json 用于灾难恢复
- ❤️ 上报健康状态

## 项目结构

```
moenet-dn42-agent/
├── src/
│   ├── main.py               # Agent 主程序入口
│   ├── config.py             # 配置加载
│   ├── client/
│   │   └── control_plane.py  # Control-Plane API 客户端
│   ├── state/
│   │   └── manager.py        # last_state.json 管理
│   ├── renderer/
│   │   ├── bird.py           # BIRD 配置渲染
│   │   └── wireguard.py      # WireGuard 配置渲染
│   ├── executor/
│   │   ├── bird.py           # BIRD 配置应用
│   │   ├── wireguard.py      # WireGuard 接口管理
│   │   └── firewall.py       # 动态 iptables 管理
│   ├── daemon/
│   │   ├── sync.py           # Peer 配置同步守护进程
│   │   └── mesh_sync.py      # Mesh 网络同步
│   └── api/
│       └── server.py         # HTTP API (供 bot 调用)
├── templates/
│   ├── bird_peer.conf.j2
│   └── wireguard.conf.j2
├── requirements.txt
└── README.md
```

## 快速开始

### 通过 Ansible 部署（推荐）

```bash
# 在 moenet-dn42-infra 仓库中运行
ansible-playbook deploy_agents.yml -l jp.edge.moenet.work
```

### 手动部署

```bash
# 安装依赖（使用 venv，适用于 Debian 12+）
python3 -m venv venv
source venv/bin/activate
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
  "node_name": "jp-edge",
  "sync_interval": 60,
  "heartbeat_interval": 30,
  "state_path": "/var/lib/moenet-agent/last_state.json",
  "bird_config_dir": "/etc/bird/peers.d",
  "bird_ctl": "/var/run/bird/bird.ctl",
  "wg_config_dir": "/etc/wireguard",
  "api_host": "0.0.0.0",
  "api_port": 54321,
  "api_token": "",
  "dn42_ipv4": "172.22.x.x",
  "dn42_ipv6": "fd00:xxx::1",
  "dn42_link_local": "",
  "region": "JP",
  "location": "Tokyo",
  "provider": "Aliyun",
  "is_open": true,
  "max_peers": 0,
  "allow_cn_peers": false,
  "supports_ipv4": true,
  "supports_ipv6": true
}
```

### 节点显示配置

| 字段 | 说明 | 示例 |
|------|------|------|
| `region` | 地区代码 | JP, HK, US |
| `location` | 城市名 | Tokyo, Hong Kong |
| `provider` | 供应商 | Aliyun, Vultr |

### Peering 配置

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `is_open` | 是否开放 Peering | true |
| `max_peers` | 最大 Peer 数 (0=无限) | 0 |
| `allow_cn_peers` | 允许中国大陆 IP | false |
| `supports_ipv4` | 支持 IPv4 | true |
| `supports_ipv6` | 支持 IPv6 | true |

## 与 Control-Plane 交互

Agent 通过以下 API 与 control-plane 交互：

| API | 用途 |
|-----|------|
| `POST /api/v1/agent/register` | **自动注册节点** (首次启动时调用) |
| `GET /api/v1/agent/config` | 拉取节点 peer 配置 |
| `POST /api/v1/agent/heartbeat` | 发送心跳 |
| `POST /api/v1/agent/state` | 上报 last_state.json |
| `GET /api/v1/mesh/config/{node}` | 获取 Mesh 网络配置 |
| `POST /api/v1/mesh/register-key/{node}` | 注册 Mesh WireGuard 公钥 |

## Agent API

Agent 在本地提供 HTTP API (默认端口 54321)：

| 端点 | 用途 |
|------|------|
| `GET /status` | 获取 Agent 状态 |
| `POST /sync` | 立即触发配置同步 |
| `GET /peers` | 查看当前活跃的 peers |

**安全**: Agent API 端口通过防火墙限制，仅允许 Control Plane IP 访问。

## 自动注册与 Node ID

Agent 首次启动时会自动向 Control Plane 注册：

1. 检测节点类型 (RR/Edge) 根据 hostname
2. 上报 IP 地址、agent 版本
3. Control Plane 自动分配唯一 `node_id` (1-62)
4. Agent 将 `node_id` 持久化到 `config.json`，避免重启后依赖 CP

### Node ID 与 IP 分配

| 字段 | 计算方式 | 示例 (node_id=2) |
| ---- | -------- | ---------------- |
| `dn42_ipv4` | `172.22.188.{node_id}` | `172.22.188.2` |
| `loopback_ipv6` | `fd00:4242:7777::{node_id}` | `fd00:4242:7777::2` |

### Loopback 配置

Agent 自动配置 `dummy0` 接口：

- 添加 `/32` IPv4 和 `/128` IPv6 地址（基于 node_id）
- **不会添加** 整个 `/26` 或 `/48` 前缀（这会导致路由问题）
- 清理旧的/过时的地址

无需手动在 Control Plane 添加节点！

## last_state.json

Agent 会持久化保存已应用的配置，用于：

1. 配置变更检测（对比 version_hash）
2. 灾难恢复（control-plane 可从 agent 收集重建）

---

**相关仓库**:

- [moenet-dn42-control-plane](https://github.com/heichaowo/moenet-dn42-control-plane) - API 和 Telegram Bot
- [moenet-dn42-infra](https://github.com/heichaowo/moenet-dn42-infra) - Ansible 部署和 Terraform
