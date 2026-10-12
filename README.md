# KGTS

知识图谱教学系统（KGTS）是一个面向教学场景的知识图谱应用仓库，包含 React + Vite 前端、Python API 服务、课程结构化数据、知识图谱维护工具，以及图谱管理页面。

当前推荐的运行方式是根目录的单端口模式：React 构建产物、教学 API、维护 API 和图谱管理页统一由 `render_app.py` 托管，便于本地测试和部署，实际并不使用render免费版的全栈部署，因为需要信用卡进行验证。

> [演示站点浏览，可进行知识图谱体验](http://kgts.southeastasia.cloudapp.azure.com)

## 当前实现状态

- 前端是 React + Vite，源码位于 `frontend/`
- 后端 API 由 Python 服务提供，单端口入口是 `render_app.py`
- 教师端每次点击“生成新题”生成 5 道新题，题库总量不设置上限
- 学生练习每次随机抽取最多 10 题，并排除教师点踩的题目
- 题目选项支持 Markdown / LaTeX 渲染，并会去除重复的 `A. A.` 选项前缀
- 页面已补充移动端适配，手机端优先显示实际工作区，图谱和导航会收敛为窄屏布局
- 备课工作台顶部“课程名称”是保存、导出和逐页讲解生成的最高优先级标题；旧的课件预览标题只作为空输入时的回退
- 前端生产构建启用 TanStack Router 自动路由拆包，并把图谱布局依赖拆成按需加载的 vendor chunk，避免非图谱页面首屏拉取 ELK/Dagre
- TTS 生成结果会校验 WAV 文件头和可播放帧数；本地 Genie、Genie proxy 和 `genie_server` 响应返回空文件或非 WAV 时会显式报错而不是缓存坏音频

## 功能概览

- 教师端：备课、讲稿生成、PPT 解析、练习题生成
- 学生端：学习、练习、复习、问答
- 图谱维护：节点/关系编辑、导入导出、结构化数据同步
- 图谱浏览：图谱主页与后台管理页

## 仓库结构

```text
frontend/                     React + Vite 前端
education/                    教学域主逻辑
maintenance/                  图谱维护域主逻辑
mcp_server/                   MCP 工具定义与服务实现
core/                         图谱、桥接、运行时核心逻辑
models/                       Pydantic / 类型模型
backend/                      兼容旧目录结构的启动器、shim 和图谱后台资产
structured/                   可公开提交的结构化课程数据
data/seed/                    可提交的启动种子数据
render_app.py                 当前推荐的单端口入口
scripts/launchers/            其他启动脚本和实验脚本
requirements/                 可选依赖清单：向量检索、TTS 等
```

`education/`、`maintenance/`、`mcp_server/`、`core/` 是当前主实现；`backend/` 主要保留兼容入口和仍在运行时使用的旧目录资产。

## 快速开始

### 方式一：Windows 一键启动

在仓库根目录运行：

```powershell
.\scripts\launchers\start.ps1
```

或：

```bat
scripts\launchers\start.bat
```

脚本会自动检查 Python 依赖、前端构建产物，并在需要时执行安装与构建。
其他实验启动脚本位于 `scripts/launchers/`，避免根目录继续膨胀。

`start.bat` 默认打开 `http://127.0.0.1:8000/`。如果需要本地 HTTPS，把证书放到：

```text
.runtime/certs/localhost.pem
.runtime/certs/localhost-key.pem
```

再次运行 `scripts\launchers\start.bat` 时会自动改用 `https://127.0.0.1:8000/`。本地开发建议用 `mkcert` 生成受信任证书；Azure 公网部署仍建议由 Nginx + Certbot 或 Azure 平台证书终止 HTTPS，再反代到本机服务。

### 方式二：手动启动

1. 安装后端依赖

```bash
python -m pip install -r requirements.txt
```

2. 安装并构建前端

```bash
cd frontend
npm install
npm run build
cd ..
```

3. 启动单端口服务

```bash
python render_app.py
```

默认访问地址：

- 应用主页：`http://127.0.0.1:8000/`
- 图谱后台：`http://127.0.0.1:8000/admin`
- API 文档：`http://127.0.0.1:8000/docs`

调试接口问题时建议在前台运行 `python render_app.py`，让终端直接显示后端日志。不要用静默后台方式启动长期服务，否则生成题目、图谱导入等错误不容易定位。

## 开发模式

前端开发服务器：

```bash
cd frontend
npm run dev
```

默认会把 `/api` 和 `/env-config.js` 代理到 `http://127.0.0.1:8000`。如需改到其他后端地址，设置：

```bash
VITE_DEV_API_TARGET=http://127.0.0.1:8000
```

后端仍建议在仓库根目录启动：

```bash
python render_app.py
```

## 配置与数据目录

- `.env.example`：示例配置
- `.runtime/`：运行时生成的章节、日志和缓存
- `structured/`：可公开提交的课程结构化数据
- `data/seed/`：可提交的启动种子数据

### 独立书籍图谱

`data/seed/graph_registry.json` 注册彼此独立的 SQLite 图谱。当前包含现有种子图谱、`popgen`（`Pack/PopGen_chapters_2_3_4_6`）和 `genetics`（`GeneticsPack`）。默认 `semantic_search` 会逐图谱检索并在结果中保留 `graph_id`、书名和原始节点 ID；各书不会合并到同一数据库。使用 `python scripts/build_book_graphs.py` 可从两个书包重复构建。
- `.runtime/knowledge_graph.db`：默认运行时图谱数据库位置
- `backend/vector_index_system/knowledge_graph/`：旧版图谱页面和兼容工具目录

常见环境变量：

- `APP_RUNTIME_DIR`：运行时章节、图谱、索引、日志和缓存目录
- `APP_DATA_DIR`：运行时数据目录
- `GRAPH_DB_PATH`：显式指定图谱数据库路径
- `APP_BOOTSTRAP_SEED_DATA`：是否启用种子数据引导
- `DEEPSEEK_API_KEY`：教学生成与问答能力所需 API Key

## 兼容模式

如果需要保留旧的多服务拆分方式，可以使用：

```bash
python backend/start_all.py
```

该模式会分别启动前端静态服务、教育 API、维护 API 和图谱后台页，但日常开发与部署优先使用根目录单端口模式。

## 当前线上部署

约 1 GB 的 Azure for Students VM 推荐使用 SQLite FTS5 + 两跳图扩展的低内存 GraphRAG，避免 Web 进程加载 PyTorch 和 embedding 模型。启用步骤和能力边界见 [免费 VM GraphRAG 优化](docs/azure-student-free-graphrag.md)。

当前公开演示站点 `http://kgts.southeastasia.cloudapp.azure.com` 运行在 Azure for Students VM 上，服务由 VM 内的 Git 仓库、systemd 和 Nginx 管理。更新这个站点时，需要 SSH 到 VM，在 `/home/azureuser/kgts` 中拉取或应用代码、重建前端，并重启对应 systemd 服务。仅把提交推到 GitHub 不会更新当前公开演示站点。

VM 更新流程见 [Azure for Students VM 部署](docs/azure-student-vm.md#更新部署)。如果改动涉及 TTS 后端、TTS 代理或语音配置，例如 `core/tts_*`、`education/tts_router.py`、`scripts/genie_tts_proxy_server.py` 或 `KGTS_TTS_*`，除了 `kgts.service` 外还要重启 `kgts-tts.service`。

## Azure App Service 部署（可选，不是当前演示站点）

`main` 分支保留 GitHub Actions 工作流：

```text
.github/workflows/main_kgts-interactive-learning-system.yml
```

该工作流会先构建 React 前端，再打包单端口 Python 应用，并通过 `azure/webapps-deploy` 把 zip 包部署到 Azure App Service。它只适用于另行配置的 App Service，不是当前公开演示 VM 的更新路径。App Service 这条路径也不是从本机用 Azure CLI 推送应用代码；本机 Azure CLI 只用于登录、创建或检查 Azure 资源。

App Service 部署包会排除运行时目录、模型权重、旧版向量索引目录、TTS 资产，以及 `data/seed/vector_index/` 预建 FAISS 索引，避免低资源实例冷启动和 zip 包体积被大文件拖垮。`data/seed/knowledge_graph.db` 仍作为轻量初始图谱种子随包发布；线上需要向量索引时会在 `.runtime/vector_index` 中按低内存配置使用缓存或 hashing fallback。

部署认证支持两种方式：

- 推荐应急路径：在 GitHub repository secrets 中配置 `AZURE_WEBAPP_PUBLISH_PROFILE`，内容为 Azure Portal 中 App Service 的 Publish profile XML。workflow 会直接用该 secret 部署 zip 包，不需要 `azure/login`。
- OIDC 路径：保留 `AZUREAPPSERVICE_CLIENTID_*`、`AZUREAPPSERVICE_TENANTID_*`、`AZUREAPPSERVICE_SUBSCRIPTIONID_*`。如果 Actions 报 `No subscriptions found`，说明该 client-id 对应的 managed identity / service principal 没有当前 subscription 或 Web App 的可见权限，需要在 Azure IAM 中给它至少 App Service 所在 resource group 的 Contributor / Website Contributor 角色，并确认 federated credential 匹配 `repo:mysteriousFourier/Knowledge-Graph-Teaching-System:ref:refs/heads/main`。

大图谱数据库和预建 FAISS 索引不要放进 Git 或 App Service zip 包。App Service 如需使用本地生成的最新版运行时数据，先在 App Settings 中设置持久化路径：

```text
APP_RUNTIME_DIR=/home/site/kgts-runtime
GRAPH_DB_PATH=/home/site/kgts-runtime/knowledge_graph.db
KGTS_VECTOR_INDEX_DIR=/home/site/kgts-runtime/vector_index
```

然后通过 Kudu/SSH/SCM 把文件上传到 `/home/site/kgts-runtime/knowledge_graph.db` 和 `/home/site/kgts-runtime/vector_index/`。`/home` 是 App Service 的持久化存储；不要上传到部署 zip 解包目录里，否则下一次部署可能覆盖。Azure for Students VM 使用 `scp` 同步到 `~/kgts/.runtime/`，详见 VM 文档。

如果手上有 Azure Portal 下载的 App Service publish profile XML，可以不安装 Azure CLI，直接用 Kudu/SCM 上传本地最新版运行时数据：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\upload_app_service_runtime_data.ps1 -PublishProfilePath .tmp\kgts.PublishSettings
```

该脚本只上传 `.runtime\knowledge_graph.db`、`.runtime\vector_index\metadata.json` 和 `.runtime\vector_index\vector_index.faiss` 到 `/home/site/kgts-runtime`，不会改 Git 历史，也不会把大文件放进 GitHub Actions 的部署包。

Azure App Service 的 Startup Command 建议设置为：

```bash
bash startup.sh
```

如果 Portal 不接受脚本，也可以设置为：

```bash
python -m uvicorn render_app:app --host 0.0.0.0 --port $PORT
```

Azure 冷启动健康探测时间有限，默认会跳过结构化图谱重建和图谱清理这类启动维护任务，避免出现 `ContainerTimeout`。相关环境变量：

- `APP_RUN_STARTUP_MAINTENANCE=0`
- `RENDER_AUTO_SYNC_STRUCTURED=0`
- `APP_BOOTSTRAP_SEED_DATA=1`
- `DEEPSEEK_GENERATION_READ_TIMEOUT_SECONDS=0`

### 本地 GraphRAG 检索配置

本地默认使用真正的 `hybrid` GraphRAG：图谱节点 embedding + FAISS 向量索引召回，再用图关系补充公式、例题、表格和前后置节点。先安装可选向量依赖：

```bash
python -m pip install -r requirements/vector.txt
```

在低内存 Linux VM 上不要直接让 PyPI 解析 torch，否则可能拉取 CUDA 版依赖。改用 CPU-only 文件：

```bash
python -m pip install -r requirements/vector-cpu.txt
```

默认环境变量：

```text
KGTS_RETRIEVAL_MODE=hybrid
KGTS_VECTOR_INDEX_DIR=.runtime/vector_index
KGTS_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
KGTS_EMBEDDING_LOCAL_FILES_ONLY=1
KGTS_VECTOR_STARTUP_ENSURE=0
```

如果本机没有缓存该 embedding 模型，先下载/放置模型，或把 `KGTS_EMBEDDING_MODEL` 改为本地模型目录。缺失时接口会在 `vector_stats.last_error` 中给出明确错误，不会静默降级为 `graph_db`。`KGTS_VECTOR_STARTUP_ENSURE=0` 会避免 Web 服务启动时预热 embedding；第一次混合检索或手动重建索引时再加载模型。

### Azure F1 检索配置

Azure F1 部署保持 `hybrid` 神经向量检索能力，但按低内存方式运行：安装 CPU-only 向量依赖，不在启动时预热 embedding，查询或重建后释放模型引用。生产默认：

```text
KGTS_RETRIEVAL_MODE=hybrid
KGTS_VECTOR_STARTUP_ENSURE=0
KGTS_VECTOR_UNLOAD_AFTER_QUERY=1
KGTS_VECTOR_UNLOAD_AFTER_REBUILD=1
KGTS_VECTOR_HASH_FALLBACK=1
```

GitHub Actions 会安装 `requirements/vector-cpu.txt`，并继续排除 `backend/vector_index_system/models/`、`backend/vector_index_system/vector_index/`、`data/seed/vector_index/` 和大模型权重，避免部署包膨胀。若线上没有可用 embedding 缓存，系统会保留 hybrid 接口并使用 hashing fallback，不会把功能关掉。

### 可选本地语音推理

项目预留了 `/api/tts/*` 接口，本地可用 `genie` provider 加载项目内模型。Azure App Service F1 主站不直接加载本地 TTS 权重，`startup.sh` 默认不会假定本机存在 `127.0.0.1:9880` TTS 代理。需要线上语音时，在 App Settings 中显式启用 server provider，并把 URL 指向真实可达的独立推理服务：

```text
KGTS_TTS_ENABLED=1
KGTS_TTS_PROVIDER=genie_server
KGTS_TTS_SERVER_URL=http://127.0.0.1:9880
```

部署包会排除 TTS 模型、缓存和生成音频。线上语音需要把 Genie/GPT-SoVITS 放在独立进程、独立 VM 或外部推理服务里，再把 `KGTS_TTS_SERVER_URL` 指向该服务。本地启用方式见 [本地语音推理](docs/tts-genie.md)。

## Azure for Students VM 部署

如果要部署到 Azure for Students 免费 VM，优先选择 `Standard_B2ats_v2`；它是 AMD x86-64、2 vCPU / 1 GB RAM，在当前免费 VM 候选里比 `Standard_B1s` 更适合 KGTS，同时比 Arm 规格更少依赖兼容风险。若目标地区没有该 SKU 或订阅无配额，再退回 `Standard_B1s`。

免费 VM 线上配置必须保持功能开启但低内存：

```text
KGTS_RETRIEVAL_MODE=hybrid
KGTS_VECTOR_STARTUP_ENSURE=0
KGTS_VECTOR_UNLOAD_AFTER_QUERY=1
KGTS_VECTOR_UNLOAD_AFTER_REBUILD=1
KGTS_VECTOR_HASH_FALLBACK=1
KGTS_TTS_ENABLED=1
KGTS_TTS_PROVIDER=genie_server
KGTS_TTS_SERVER_URL=http://127.0.0.1:9880
APP_RUN_STARTUP_MAINTENANCE=0
RENDER_AUTO_SYNC_STRUCTURED=0
APP_BOOTSTRAP_SEED_DATA=1
```

如果需要在同一台 1 GB VM 上实验本地 Genie-TTS 和神经向量检索，按错峰运行处理：TTS 只用于朗读课件，向量检索只用于备课/问答；不要让两者同时常驻。向量依赖使用 `requirements/vector-cpu.txt`，并开启：

```text
KGTS_RETRIEVAL_MODE=hybrid
KGTS_VECTOR_STARTUP_ENSURE=0
KGTS_VECTOR_UNLOAD_AFTER_QUERY=1
KGTS_VECTOR_UNLOAD_AFTER_REBUILD=1
```

完整 VM 创建、systemd 和 Nginx 部署步骤见 [Azure for Students VM 部署](docs/azure-student-vm.md)。VM 文档中的 Azure CLI 命令用于创建资源、开放端口和检查状态；应用代码部署是在 VM 内通过 `git clone` / `git pull` 更新仓库，不是从本机 Azure CLI 直接上传工作区。

## 文档索引

- [前端说明](frontend/README.md)
- [后端兼容目录说明](backend/README.md)
- [检索模式与 Azure F1 说明](docs/retrieval-modes.md)
- [本地语音推理](docs/tts-genie.md)
- [Azure for Students VM 部署](docs/azure-student-vm.md)
- [数据目录说明](data/README.md)
- [结构化数据说明](structured/README.md)
- [第三方依赖迁移说明](docs/THIRD_PARTY_MIGRATION.md)

## 运行截图演示

<img width="2560" height="1380" alt="index" src="https://github.com/user-attachments/assets/8510a239-e652-4d5c-82f5-c6747c794582" />
主界面

<img width="2560" height="1380" alt="exam" src="https://github.com/user-attachments/assets/a20caa87-a2a2-4443-ba2c-0b708a32e5c7" />
测试题

<img width="2560" height="1380" alt="teacher" src="https://github.com/user-attachments/assets/46caec53-9947-43d5-8d83-4628b77623f4" />
教师端

<img width="2560" height="1380" alt="sheet" src="https://github.com/user-attachments/assets/03b812c7-3454-4b84-bc3d-f729ab52c2ca" />
授课文案

<img width="2560" height="1380" alt="graph-1" src="https://github.com/user-attachments/assets/ed9d5a25-d48c-4ad2-a101-2372676fa7e0" />
图谱示例1

<img width="2560" height="1380" alt="graph" src="https://github.com/user-attachments/assets/1f0b0f8f-b4da-47e4-9426-5fcec46a3006" />
图谱示例2

<img width="2560" height="1380" alt="graph-admin" src="https://github.com/user-attachments/assets/90f39d5d-7309-4c9d-8da1-c15791810680" />
图谱管理，仅教师端，教师端也可直接编辑图谱








