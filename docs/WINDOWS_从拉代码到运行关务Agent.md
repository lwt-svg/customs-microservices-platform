# Windows：从 GitHub 拉代码到运行关务 Agent

本文面向第一次运行本项目的学习者。项目使用的是本地构造数据和本地 Sandbox，不连接真实企业系统，也不会提交真实账号、密码、Cookie 或 Token。

## 一、项目能做什么

启动后可以在本机体验：

- Shipment Case 案件主线；
- LINK 订舱业务；
- 装载规划与候选方案比较；
- 产地证 COO Sandbox RPA；
- 商检 CIQ Sandbox RPA；
- Playwright 浏览器 Worker；
- 人工确认、失败重试、幂等和处理记录；
- Java BFF → Python 服务 → MySQL/Nacos 的本机调用链。

RPA 的结果是本地 Sandbox 草稿，例如 `LINK_DRAFT_SAVED`、`COO_DRAFT_SAVED` 和 `CIQ_DRAFT_SAVED`。这不是连接真实关贸通、真实商检系统或真实产地证平台的生产申报。

## 二、Windows 需要准备什么

建议使用 Windows 10/11，并准备：

1. Git；
2. Docker Desktop，并确认 Docker Engine 已启动；
3. Python 3.12；
4. Conda 或 Miniconda；
5. Java 21；
6. Maven；
7. Node.js 与 npm；
8. Chrome 或 Edge。

项目会使用 Docker 启动本地 MySQL 8 和 Nacos。不要把企业账号、真实 Token 或真实文件复制进这个学习仓库。

## 三、从 GitHub 拉取代码

打开 PowerShell，执行：

```powershell
git clone https://github.com/lwt-svg/customs-microservices-platform.git
cd customs-microservices-platform
```

查看当前提交：

```powershell
git log -1 --oneline
git status
```

`git status` 正常情况下应显示工作区干净。

## 四、创建 Python 环境

如果电脑已经有 Conda 环境，可以直接使用。没有时执行：

```powershell
conda create -n customs-affairs-os-lab python=3.12 -y
conda activate customs-affairs-os-lab
```

首次安装本项目依赖：

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements-local.txt
python -m playwright install chromium
```

如果 PowerShell 找不到 `conda`，先关闭并重新打开 Anaconda Prompt，或者把 Miniconda 加入当前终端 PATH。

## 五、创建本地配置

在仓库根目录执行：

```powershell
Copy-Item .env.example .env.local
```

`.env.local` 只用于本机，不要执行 `git add .env.local`，也不要把它发到 GitHub。

第一次学习可以先保留示例配置。需要修改配置时，只修改 `.env.local`，不要修改 `.env.example`。

## 六、首次安装项目依赖

项目已经提供 Windows 安装脚本。首次运行建议执行：

```powershell
.\scripts\windows\setup-local.ps1
```

如果 PowerShell 禁止执行脚本，可以只对当前用户开放本地脚本：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

然后重新执行安装脚本。

安装脚本会检查 Docker、Java、Maven、Node、npm，安装 Python 与 Playwright 依赖，安装两个 Vue 前端依赖，并预下载 Maven 依赖。

## 七、启动本地系统

### 1. 启动基础模式

基础模式适合学习 LINK 七步、案件和装载规划：

```powershell
.\scripts\windows\run-local.ps1 -Mode core
```

### 2. 启动完整模式

完整模式会额外启动模拟承运人和异步回调：

```powershell
.\scripts\windows\run-local.ps1 -Mode full
```

### 3. 开启本地规则解读

不使用外部模型、没有 API 费用的学习方式：

```powershell
.\scripts\windows\run-local.ps1 -Mode full -InsightMode rules
```

`rules` 使用本地确定性规则解释装载候选。它的作用是帮助学习 Agent 的工具调用和验证流程，不代表大模型推理。

### 4. 停止服务

另开一个 PowerShell 窗口，在仓库根目录执行：

```powershell
.\scripts\windows\stop-local.ps1
```

## 八、打开哪些地址

完整模式启动后：

| 服务 | 地址 | 用途 |
|---|---|---|
| 平台前端 | http://127.0.0.1:5173 | 业务工作台 |
| LINK Python | http://127.0.0.1:8000/docs | LINK 服务接口 |
| Agent Runtime | http://127.0.0.1:8200/docs | 装载与 Shipment Agent 接口 |
| Java BFF | http://127.0.0.1:8080/actuator/health | 平台后端健康检查 |
| 模拟承运人 API | http://127.0.0.1:8100/docs | 本地承运人服务 |
| 模拟承运人门户 | http://127.0.0.1:5174 | 本地承运人页面 |
| Nacos | http://127.0.0.1:8848/nacos | 本地服务注册中心 |

浏览器只打开 `5173` 即可。前端通过 Java BFF 调用后端，不需要手动访问 Python 服务。

## 九、先做不启动服务的自检

这一步只检查构造数据、接口边界和幂等重试，不访问网络或数据库：

```powershell
conda activate customs-affairs-os-lab
python scripts/shipment-agent-e2e.py --self-test
```

成功时应看到：

```text
self-test PASS
```

## 十、运行完整 Shipment Agent 测试

先启动完整模式，再打开另一个 PowerShell：

```powershell
cd customs-microservices-platform
conda activate customs-affairs-os-lab
python scripts/shipment-agent-e2e.py
```

这条测试会自动构造一票脱敏案件，包含货物明细、尺寸、重量、装载约束、LINK 资源、人工确认和模拟承运人回调。它验证：

```text
创建任务
→ 创建 LINK 草稿
→ 创建 Shipment Case
→ 检查资料完整性
→ 补充缺失资料
→ 生成装载候选
→ 人工批准方案
→ 发布正式版本
→ 提交 LINK Sandbox
→ 模拟承运人异步回调
→ Agent 自动唤醒
→ 人工批准结案
```

不要重复手工填写脚本里的业务编号；脚本会自动生成唯一编号。

## 十一、运行 LINK、产地证、商检三条 RPA

先启动完整模式，再执行：

```powershell
python scripts/rpa-sandbox-e2e.py
```

想观察浏览器实际操作过程：

```powershell
python scripts/rpa-sandbox-e2e.py --headed --browser-channel msedge
```

如果使用 Google Chrome，可以尝试：

```powershell
python scripts/rpa-sandbox-e2e.py --headed --browser-channel chrome
```

成功时应看到三类结果：

```text
LINK_BOOKING_STAGE -> LINK_DRAFT_SAVED
COO_STAGE          -> COO_DRAFT_SAVED
CIQ_STAGE          -> CIQ_DRAFT_SAVED
```

脚本访问的只能是本机 `127.0.0.1` Sandbox。它不会访问真实企业网站。

## 十二、怎么在页面里测试

1. 打开 http://127.0.0.1:5173；
2. 进入案件或 Agent 中心；
3. 创建或打开一个 Shipment Case；
4. 查看资料准备状态；
5. 按页面提示补充缺失信息；
6. 打开装载工作台，查看多个候选方案；
7. 人工批准一个候选方案；
8. 进入 LINK、产地证或商检 RPA 区域；
9. 查看 RPA Job 的状态、步骤、事件和结果；
10. 在处理记录中查看 Agent 的每次决策和工具调用。

页面上的“人工批准”是故意保留的边界。Agent 可以分析、规划和调用工具，但不能把“模型认为成功”当作业务完成。

## 十三、常见问题

### 1. 报错找不到 `.env.local`

执行：

```powershell
Copy-Item .env.example .env.local
```

### 2. Docker 没有启动

打开 Docker Desktop，等待 Engine 显示 Running，再执行启动脚本。

### 3. 端口已经被占用

检查端口：

```powershell
Get-NetTCPConnection -LocalPort 5173,8000,8100,8200,8080 -ErrorAction SilentlyContinue
```

先停止本项目旧进程：

```powershell
.\scripts\windows\stop-local.ps1
```

### 4. MySQL 密码错误

这通常表示 Docker 中已有旧数据卷，卷里的初始化密码和当前 `.env.local` 不一致。学习环境没有需要保留的数据时，可以执行：

```powershell
docker compose --env-file .env.local down -v
.\scripts\windows\run-local.ps1 -Mode full -InsightMode rules
```

`down -v` 会删除本地学习数据库，只在确认不需要旧数据时使用。

### 5. RPA 浏览器没有启动

先确认 Playwright 浏览器已安装：

```powershell
python -m playwright install chromium
```

然后重新运行：

```powershell
python scripts/rpa-sandbox-e2e.py --headed
```

### 6. 想看日志

日志位于：

```text
runtime/logs/
```

重点查看：

```text
runtime/logs/link-booking-service.log
runtime/logs/load-planning-service.log
runtime/logs/platform-bff.log
runtime/logs/platform-web.log
runtime/logs/link-carrier-simulator.log
```

## 十四、代码阅读顺序

第一次阅读建议按下面顺序：

1. `scripts/shipment-agent-e2e.py`：理解一票业务怎样跑完；
2. `scripts/rpa-sandbox-e2e.py`：理解三条 RPA 怎样构造任务和验证结果；
3. `services/load-planning-service/app/api/rpa_jobs.py`：RPA Job 接口；
4. `services/load-planning-service/app/services/rpa_jobs.py`：RPA 状态和幂等逻辑；
5. `services/load-planning-service/app/rpa/browser_worker.py`：Playwright Worker；
6. `services/load-planning-service/app/rpa/certificate_profiles.py`：产地证和商检字段模板；
7. `services/load-planning-service/app/services/shipment_agent_runs.py`：Shipment Agent 状态；
8. `platform-bff`：Java BFF 如何统一调用 Python 服务；
9. `platform-web`：前端如何显示案件、Agent 和 RPA 结果。

## 十五、验证完成标准

你可以认为本地学习项目跑通，当下面几项都满足：

- `--self-test` 通过；
- Python 测试全部通过；
- Shipment Agent E2E 能创建并完成一票合成案件；
- LINK、COO、CIQ 三条 Sandbox RPA 都返回成功；
- 页面能看到任务、步骤、证据、失败原因和人工批准；
- 关闭并重新启动服务后，能用新的测试编号重复运行；
- 你能解释 Agent、确定性规则、RPA Worker、Java BFF 和 Python 服务分别负责什么。

项目当前是本地学习和演示系统。真实企业接入还需要真实身份、权限、网站适配器、法规审核、生产数据治理和人工审批制度，不能直接把 Sandbox 当成生产系统。
