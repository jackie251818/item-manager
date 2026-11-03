# CHANGELOG

所有值得记录的改动都在这里。项目版本号跟随 GitHub Release tag（如 v1.0.0）。

## v1.0.0 — 2026-11-03

### 🎉 首次发布

完整功能清单：

**数据模型**
- `persons` / `items` / `assets` / `borrows` / `users` / `audit_log` 六表
- 强一致状态机：`borrow.status='borrowed'` ↔ `asset.status='borrowed'`

**核心功能**
- 首页：按部门分组人员卡片（默认折叠）+ 一键归还某个人
- 物品管理：分类汇总 + 数量修改自动同步 assets 表 + 搜索
- 借用记录：分页（每页 30）+ 逾期 tab + 逾期行红色高亮 + 批量归还 + 搜索
- Excel 导出汇总表
- Excel 导入汇总表（幂等 upsert：按 人员姓名/物品名称/资产编号 查重）

**安全加固**
- 密码：`werkzeug.security.generate_password_hash` scrypt 存储
- CSRF：session token + `@before_request` 拦截所有 POST（login 白名单）
- 防暴力破解：5 次锁 60 秒（内存计数，重启清零）
- Session：`HttpOnly=True` + `SameSite=Lax`
- 路径穿越：`ALLOWED_UPLOAD_SUBDIRS` 白名单 + `os.path.realpath` 校验
- 图片签名：`/u/<path>?sig=&exp=` HMAC-SHA256 防枚举
- 审计日志：所有 DELETE 写 `audit_log` 表
- 强制 `Cache-Control: no-store`

**性能 & 体验**
- 首页 N+1 查询 → 一次 GROUP BY 拉全
- 借用记录分页（LIMIT/OFFSET + 省略号页码）
- Flask `RotatingFileHandler` 5MB × 3 日志轮转
- 项目改名「物品使用登记系统」+ SVG favicon

**部署**
- 多阶段 Dockerfile（Python slim + gunicorn）
- `docker-compose.yml` + `.env.example`
- systemd service 文件模板（Debian 12）
- 健康检查 endpoint

### Known Issues
- app.py 单文件 ~1600 行，后续可拆 routes/ 蓝图
- 前端 JS 部分页面级 inline script，后续可抽 static/
- SQLite 单文件，多进程写入有锁（当前 systemd 单 worker 够用）
