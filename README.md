# 物品使用登记系统 (Item Manager)

[![Release](https://img.shields.io/github/v/release/jackie251818/item-manager?style=for-the-badge)](https://github.com/jackie251818/item-manager/releases/latest)
[![GHCR](https://img.shields.io/badge/docker-ghcr.io%2Fjackie251818%2Fitem--manager-blue?style=for-the-badge&logo=docker)](https://github.com/jackie251818/item-manager/pkgs/container/item-manager)
[![License](https://img.shields.io/badge/license-MIT-green?style=for-the-badge)](https://github.com/jackie251818/item-manager/blob/main/LICENSE)

> 当前版本 **v1.0.0** · Docker 镜像 `ghcr.io/jackie251818/item-manager:latest` · Digest `sha256:73397f036c07176d313399babbcdf6de4050c8af205b10ab7d776550b9d31a81`

一个基于 Python Flask 的**物品借用登记管理系统**。
以「人员」为主导视角，管理「物品」分类、「单件资产」和「借用记录」，支持 Excel 批量导入导出、一键归还、逾期提醒、审计日志。

**生产服务器**（192.168.40.252:8080）已使用 Docker 镜像部署运行。

---

## ✨ 功能特性

- 🏢 **部门分组折叠视图** —— 首页按部门分组展示人员，点击展开借用物品
- 📦 **三级数据模型** —— 物品分类 (items) → 单件资产 (assets) → 借用记录 (borrows)
- ✅ **一键归还** —— 单件归还、批量归还（records 页勾选）、按人归还（首页卡片按钮）
- 📊 **分类汇总** —— 总数 / 已借 / 可用 实时从 assets 表聚合（杜绝静态字段漂移）
- 📥 **Excel 批量导入** —— 按人员姓名 + 物品名称 + 资产编号 幂等 upsert
- 📤 **Excel 汇总导出** —— 一键导出全量数据
- ⚠️ **逾期高亮** —— 借用超过预计归还日期的记录整行变红 + 独立 tab
- 🔍 **搜索筛选** —— records 页搜索 姓名/物品/资产编号/备注；items 页搜索 物品名/分类/型号
- 🔐 **安全加固** —— CSRF 防护 / 密码哈希 / 防暴力破解 / 路径穿越防护 / 审计日志
- 📝 **审计日志** —— 所有 DELETE 操作自动记录（谁、什么时候、删了什么）
- 🖼️ **图片上传** —— 人员头像 / 物品照片，路径穿越防护 + HMAC 签名 URL
- 🔄 **Flask 日志轮转** —— 5MB × 3 份，同时输出到 stderr（Docker / systemd 友好）

## 🏗️ 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11 + Flask 3.0 |
| 生产 WSGI | Gunicorn |
| 数据库 | SQLite 3 (单文件) |
| 前端 | 原生 HTML + CSS + JS（无框架）|
| 导入导出 | openpyxl 3.1 |
| 部署 | Docker (Gunicorn) · 生产已切换 |
| 容器仓库 | GitHub Container Registry (ghcr.io) |

## 🚀 快速开始

### 方式 1：Docker（推荐 — 生产已采用）

```bash
# 拉取镜像
docker pull ghcr.io/jackie251818/item-manager:latest

# 运行（数据持久化到 ./data）
mkdir -p data/uploads
docker run -d --name item-manager \
  -p 8080:5000 \
  -v $(pwd)/data:/app/data \
  -e ADMIN_PASSWORD='your-strong-password' \
  -e SECRET_KEY='your-random-secret' \
  --restart unless-stopped \
  ghcr.io/jackie251818/item-manager:latest
# http://localhost:8080 登录 admin / 你设的密码
```

**健康检查**：内置，每 30s ping `/login`，3 次失败算 unhealthy。

### 方式 2：docker-compose

```bash
git clone https://github.com/jackie251818/item-manager.git
cd item-manager

cp .env.example .env      # 编辑 ADMIN_PASSWORD + SECRET_KEY
docker compose up -d      # 启动
docker compose logs -f item-manager   # 看日志
docker compose ps         # 状态
```

### 方式 3：systemd（裸机部署 — 历史方式，生产已切换 Docker）

```bash
git clone https://github.com/jackie251818/item-manager.git /opt/item-manager
cd /opt/item-manager
pip3 install -r requirements.txt

sudo tee /etc/systemd/system/item-manager.service > /dev/null <<'EOF'
[Unit]
Description=Item Manager - 物品使用登记系统
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/item-manager
Environment=DB_PATH=/opt/item-manager/data/items.db
Environment=SECRET_KEY=change-me-prod-secret
Environment=ADMIN_USERNAME=admin
Environment=ADMIN_PASSWORD=your-strong-password
Environment=PORT=8080
ExecStart=/usr/bin/python3 /opt/item-manager/app.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now item-manager.service
```

### 方式 4：pip + Flask dev server（开发）

```bash
git clone https://github.com/jackie251818/item-manager.git
cd item-manager

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

ADMIN_PASSWORD=your-password python3 app.py
# http://localhost:5000
```

## ⚙️ 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DB_PATH` | `/app/data/items.db` | SQLite 数据库文件路径 |
| `PORT` | `5000` | 监听端口（Docker 内部）|
| `ADMIN_USERNAME` | `admin` | 初始管理员用户名 |
| `ADMIN_PASSWORD` | `hmt666688` | 初始管理员密码（首次启动哈希入库）|
| `SECRET_KEY` | `item-manager-secret-key-2024` | Flask session + CSRF + 图片签名密钥 |
| `UPLOAD_PATH` | `/app/data/uploads` | 图片上传目录 |

> ⚠️ **生产必须改**：`SECRET_KEY` + `ADMIN_PASSWORD`。
> **重要**：systemd 部署时忘记设 `DB_PATH` 会连到 `/app/data/items.db`（空壳），容器化部署自动正确。

## 📡 API 速查

所有 JSON POST 需带 `Content-Type: application/json`，form POST 需加 hidden field `<input name="csrf_token">`。
base.html 里的 CSRF 自动注入脚本会处理，curl/脚本调用需手动传 `X-CSRF-Token` header。

### 借用记录

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/records?status=all\|borrowed\|returned\|overdue&q=&page=1` | 分页列表 + 搜索 |
| POST | `/borrow/add` | 新增借用 |
| POST | `/borrow/<id>/edit` | 编辑（预计归还日期/备注）|
| POST | `/borrow/<id>/return` | 归还 |
| POST | `/borrows/batch_return` | 批量归还 `{"ids":[1,2,3]}` |
| POST | `/borrows/batch_return_by_person/<person_id>` | 按人一键归还 |
| POST | `/borrow/<id>/delete` | 删除（写审计）|

### 人员

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/person/add` | 新增 |
| POST | `/person/<id>/edit` | 编辑 |
| POST | `/person/<id>/delete` | 删除（写审计）|

### 物品 & 资产

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/item/add` | 新增物品 |
| POST | `/item/<id>/edit` | 编辑物品（改数量自动同步 assets 表）|
| POST | `/item/<id>/delete` | 删除（写审计）|
| POST | `/asset/<id>/delete` | 删除单件（写审计）|

### 导入导出

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/export/summary` | 导出全量 Excel |
| POST | `/import/summary` | 导入 Excel（幂等 upsert）|

## 🗄️ 数据库表结构（SQLite）

```
persons      —— 人员信息（id, name, department, phone, avatar, note）
items        —— 物品分类（id, name, category, model, serial, total_count, image）
assets       —— 单件资产（id, item_id, asset_no, status, image, model, serial, note）
borrows      —— 借用记录（id, person_id, item_id, asset_id, status, borrow_date, expected_return_date, actual_return_date, note）
users        —— 管理员账号（id, username, password_hash）
audit_log    —— 审计日志（id, actor, action, target_type, target_id, detail, created_at）
```

### 关联关系

```
items (1) ──→ (N) assets
persons (1) ──→ (N) borrows
assets (1) ──→ (0..1) borrows (status='borrowed' 时)
```

### 状态机

- `assets.status`：`available` / `borrowed`
- `borrows.status`：`borrowed` / `returned`
- **强一致**：`borrow.status='borrowed'` ↔ 对应 `asset.status='borrowed'`

## 🔒 安全设计

| 项 | 实现 |
|---|---|
| 密码存储 | `werkzeug.security.generate_password_hash` / `check_password_hash`（scrypt）|
| CSRF | session 存 token + `@before_request` 校验 form/header/JSON body |
| Session | `HttpOnly=True` + `SameSite=Lax` |
| 暴力破解 | 5 次错误锁 60 秒（内存计数，重启清零）|
| 路径穿越 | `realpath` + `allowed_file` 白名单 + `ALLOWED_UPLOAD_SUBDIRS` |
| 图片签名 | `/u/<path>?sig=&exp=` HMAC-SHA256 签名 + 时间戳过期 |
| 删除审计 | 所有 DELETE 写 audit_log（actor/action/target/detail）|
| `after_request` | 强制 `Cache-Control: no-store` |

## 📁 目录结构

```
item-manager/
├── app.py                 # Flask 主程序（~1600 行）
├── Dockerfile             # Gunicorn 多阶段生产镜像
├── docker-compose.yml     # Compose 一键起
├── .env.example           # 环境变量模板
├── .gitignore
├── requirements.txt       # Flask + gunicorn + openpyxl
├── CHANGELOG.md
├── README.md
├── templates/
│   ├── base.html          # 导航 + CSRF 自动注入
│   ├── login.html
│   ├── index.html         # 首页（人员分组 + 一键归还）
│   ├── items.html         # 物品管理（搜索 + 数量修改）
│   ├── persons.html       # 人员管理
│   └── records.html       # 借用记录（分页 + 逾期 + 批量归还 + 搜索）
└── static/
    ├── app.js             # 共用逻辑（模态框/展开/CSRF fetch patch）
    ├── favicon.svg        # 紫色清单 + 勾选
    └── style.css
```

## 🛠️ 运维速查（Docker 部署 — 生产当前方式）

```bash
# 容器状态
docker ps --filter name=item-manager
docker inspect item-manager --format '{{.State.Health.Status}}'   # healthy / unhealthy

# 实时日志
docker logs -f --tail 200 item-manager
tail -f /opt/item-manager-docker/data/item-manager.log

# 重启（镜像更新后）
docker pull ghcr.io/jackie251818/item-manager:latest
docker stop item-manager && docker rm item-manager
# 然后重跑 docker run 命令（或用 docker-compose.yml）

# 数据备份
cp -r /opt/item-manager-docker/data /opt/item-manager-docker/data.bak-$(date +%Y%m%d-%H%M)

# 数据恢复
docker stop item-manager
cp -r data.bak-xxx/* /opt/item-manager-docker/data/
docker start item-manager
```

### systemd（已停，历史参考）

```bash
systemctl status item-manager
systemctl restart item-manager
journalctl -u item-manager -f --since "10 min ago"
# 原 service 已 disable；如需切回，见 README 方式 3
```

## 🔄 发版流程

```bash
# 1. 本地
git commit -am "feat: xxx"
git tag v1.x.x -m "release v1.x.x"
git push origin main --tags

# 2. 构建 & 推 Docker 镜像
docker build -t ghcr.io/jackie251818/item-manager:v1.x.x .
docker push ghcr.io/jackie251818/item-manager:v1.x.x

# 3. 创建 GitHub Release（API 或 gh CLI）
POST https://api.github.com/repos/jackie251818/item-manager/releases
  body: { tag_name, name, body (CHANGELOG), draft: false }

# 4. 生产更新
docker pull ghcr.io/jackie251818/item-manager:latest
docker stop item-manager && docker rm item-manager
docker run -d ... （同第一次）
```

## 📊 生产现状（2026-11-03）

| 项 | 值 |
|---|---|
| 服务器 | 192.168.40.252 |
| 端口 | 8080 |
| 部署方式 | Docker（容器名 `item-manager`）|
| 镜像 | `ghcr.io/jackie251818/item-manager:latest` |
| 数据路径 | `/opt/item-manager-docker/data/` |
| 数据库 | persons=106, items=16, assets=968, borrows=289 |
| 版本 | v1.0.0 |
| systemd | 已停（历史 `/etc/systemd/system/item-manager.service` 保留供参考）|

## 📜 CHANGELOG

详细改动见 [CHANGELOG.md](CHANGELOG.md)。

## 🤝 许可证

MIT License
