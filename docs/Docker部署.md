# Docker 部署（NAS / 云服务器）

## 快速开始

```bash
git clone <本仓库> && cd tiku-studio
docker compose up -d --build
```

打开 `http://<主机IP>:8100`。首次启动自动建库并导入种子题库。

## 数据持久化

`docker-compose.yml` 已把 `./data` 挂载进容器（数据库 + 收件箱 + 题图库都在这里）。
升级镜像不会丢数据；备份 = 备份 `data/` 目录。

## 局域网设备访问

手机/平板连同一 WiFi，访问 `http://<NAS或主机IP>:8100`。界面自适应。

## 公网访问（可选）

三种常见方式，按安全性排序：

1. **Tailscale / WireGuard 组网**（推荐）：所有设备装 Tailscale，用虚拟内网 IP 访问，不暴露公网端口。
2. **反向代理 + HTTPS + 密码**：Nginx/Caddy 反代 8100，套 HTTPS（Caddy 自动证书），
   并加 Basic Auth。本系统面向家庭使用，没有内置账号体系，公网裸奔不可取。
3. **frp 等 内网穿透**：同上，务必加访问控制。

## compose 文件说明

```yaml
services:
  tiku:
    build: .
    ports: ["8100:8100"]
    volumes: ["./data:/app/data"]   # 全部持久化数据
    environment:
      - TIKU_PORT=8100
    restart: unless-stopped
```

镜像内已完成：Python 依赖安装、前端构建（多阶段构建里用 Node 构建）、Playwright Chromium 安装
（因此容器内可以直接导出 PDF）。

## 常见问题

- **构建慢**：主要时间在 Playwright Chromium 下载；国内网络可加镜像源环境变量后重试。
- **时区**：如需本地时间，在 compose 里加 `TZ=Asia/Shanghai`。
- **Agent 拆解在服务器上能用吗**：Agent 模式需要在你本地跑（它要读 PDF 和网页），产物 JSON
  上传到服务器「资料库 → 收件箱导入」即可；或直接在网页用 LLM 拆解。
