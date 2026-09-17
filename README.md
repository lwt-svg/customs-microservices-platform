# Customs Microservices Platform

沙丘 OS 的本地关务微服务学习平台。项目使用构造数据和独立沙箱，不连接企业 Nacos、FAT、
数据库或任何线上系统。

## 当前技术栈

- Vue 3、TypeScript 与 Vite 后台管理界面；
- Java 21、Spring Boot、Spring Data JPA 与 Flyway 平台 BFF；
- Python、FastAPI、SQLAlchemy 与 Alembic LINK 微服务；
- 本地 MySQL 8 与 Nacos；
- Java BFF 通过 Nacos 发现并调用 Python 服务；
- 组织、用户、角色和权限基础模型。

## 项目结构

```text
customs-microservices-platform/
├── platform-web/                Vue 3 后台管理系统
├── platform-bff/                Java Spring Boot 平台 BFF
├── services/
│   └── link-booking-service/    Python FastAPI LINK 服务
├── docs/                        中文实验笔记
├── compose.yaml                 本地 MySQL 与 Nacos
└── .env.example                 安全的本地配置示例
```

## 安全边界

- 只使用构造或脱敏的学习数据；
- `.env.local` 永远不提交 Git；
- Java 和 Python 使用独立数据库 schema；
- Vue 只访问 Java BFF，不直接连接 Python 或数据库；
- 不连接企业服务、账号或网络环境。

## 第 12 节：Java Spring Boot BFF 初始化

`platform-bff` 是沙丘 OS 的 Java 平台层起点。当前只包含：

- `GET /api/v1/platform/health`：供 Vue 或学习者确认 BFF 已启动；
- `GET /actuator/health`：供本机或容器健康探针使用；
- 统一响应对象 `ApiResponse`；
- 一个不会连接 MySQL、Nacos、Python 或企业资源的 MockMvc 测试。

启动和验证：

```bash
cd platform-bff
mvn test
mvn spring-boot:run
curl http://127.0.0.1:8080/api/v1/platform/health
curl http://127.0.0.1:8080/actuator/health
```

预期业务健康检查响应：

```json
{"code":0,"message":"success","data":{"status":"UP","service":"platform-bff"}}
```

## LINK 订舱申请 API（第一条数据库闭环）

服务使用独立 MySQL schema `link_booking_service`，绝不与其他 Python 服务共表或跨库写入。
首次启动前，在本机 Docker MySQL 中创建该 schema 并授权给本地学习用户（命令不会显示密码）：

```bash
docker compose --env-file .env.local exec -T mysql sh -c \
  'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "CREATE DATABASE IF NOT EXISTS link_booking_service; GRANT ALL PRIVILEGES ON link_booking_service.* TO '\''$MYSQL_USER'\''@'\''%'\''; FLUSH PRIVILEGES;"'
```

确认 `.env.local` 里的 `LINK_DATABASE_URL` 指向 `link_booking_service` 后，执行迁移和测试：

```bash
cd services/link-booking-service
alembic upgrade head
pytest
ruff check .
uvicorn app.main:app --reload --port 8000
```

API 均只适用构造或脱敏数据：

- `POST /api/v1/booking-applications`：创建 `DRAFT` 草稿；
- `GET /api/v1/booking-applications` 与 `GET /api/v1/booking-applications/{application_number}`：查询申请；
- `POST /api/v1/booking-applications/{application_number}/status`：状态只允许 `DRAFT → SUBMITTED → CONFIRMED` 或 `SUBMITTED → CANCELLED`。

首期没有任何 PDF/XLSX 上传或解析接口。未来解析功能必须把真实文件放在项目外或 Git 忽略的 `private-fixtures/`，且日志不得输出原文、地址、金额、商品明细或客户内容。

## 本地启动

```bash
cp .env.example .env.local
docker compose --env-file .env.local up -d
```

基础设施启动后，可在浏览器打开：

- Nacos: <http://127.0.0.1:8848/nacos>
- MySQL: `127.0.0.1:3306`（仅供本地学习服务访问）

> `.env.local` 被 Git 忽略。不要填入企业账号、Token、地址或真实数据。
