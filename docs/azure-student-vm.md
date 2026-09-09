# Azure for Students VM 部署

> 最后核对：2026-05-26。Azure 免费服务和地区 SKU 可用性会变，创建资源前以 Azure Portal 的 Free services、Cost Management 和 Pricing Calculator 为准。

KGTS 已支持单端口部署：前端构建产物、教学 API、维护 API、图谱浏览页和后台页都由根目录 `render_app.py` 托管。VM 上只需要运行一个 Python Web 服务，再由防火墙或 Nginx 暴露到公网。

当前实测部署：

| 项目 | 值 |
| --- | --- |
| 资源组 | `kgts-student-sea-rg` |
| VM | `kgts-free-vm` |
| 区域 | `southeastasia` |
| 规格 | `Standard_B2ats_v2` |
| 公网访问 | `http://20.212.50.255/` |

## 免费规格选择

> 低内存配置更新（2026-09-05）：现有 1 GB VM 推荐按 [免费 VM GraphRAG 优化](azure-student-free-graphrag.md) 启用 `sparse_hybrid` 和单 worker 配置。下文 `hybrid` + CPU 模型的步骤保留为可选神经检索方案，不再是免费 VM 默认建议；轻量检索无需安装 `requirements/vector-cpu.txt`。TTS 仍使用现有独立服务。

Azure for Students 当前包含 12 个月内的免费 VM 小规格和 100 美元额度。免费 VM 候选优先级：

| 规格 | 架构 | 资源 | 判断 |
| --- | --- | --- | --- |
| `Standard_B2ats_v2` | AMD x86-64 | 2 vCPU / 1 GB RAM | 首选。CPU 比 `B1s` 更宽裕，保持 x86 兼容。 |
| `Standard_B2pts_v2` | Arm64 | 2 vCPU / 1 GB RAM | 备选。部分 Python/Node 依赖在 Arm 上更容易遇到 wheel 或构建问题。 |
| `Standard_B1s` | x86-64 | 1 vCPU / 1 GB RAM | 最稳妥兼容兜底，但构建和冷启动更慢。 |

KGTS 的完整本地能力，包括神经向量检索和 Genie-TTS，不适合在 1 GB 免费 VM 上同时常驻运行。免费 VM 线上部署保持功能开启，但必须使用低内存配置：

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
DEEPSEEK_GENERATION_READ_TIMEOUT_SECONDS=0
KGTS_SLIDE_LECTURE_MAX_OUTPUT_TOKENS=12000
KGTS_SLIDE_LECTURE_READ_TIMEOUT_SECONDS=0
KGTS_SLIDE_LECTURE_FLASH_READ_TIMEOUT_SECONDS=0
KGTS_SLIDE_LECTURE_COMPLETION_TIMEOUT_SECONDS=0
```

TTS 必须作为独立服务运行，并让主站使用 `genie_server` 代理。不要在主 `kgts.service` 里直接使用 `KGTS_TTS_PROVIDER=genie`。

如果同时要实验本地 TTS 和神经向量检索，把它们按错峰任务处理：TTS 只用于朗读课件，向量检索只用于备课/问答。不要在 TTS 合成时跑 embedding，也不要让 Web 启动时预热向量模型。

## 成本边界

避免意外扣费时重点看这些资源：

- 只创建一个免费 VM 规格，不要同时运行多个免费 VM，否则 750 小时/月会被多台机器累计消耗。
- 使用 64 GB P6 托管磁盘以内；不要额外挂载大磁盘。
- 公网 IPv4 地址可能有单独计费，Basic SKU 已在 2025-09-30 退役。创建前在 Portal 费用预估里确认 Public IP、带宽和磁盘是否仍在免费额度或学生额度内。
- 不要创建 NAT Gateway、Load Balancer、Application Gateway、Azure Firewall 等额外网络资源。
- 部署后在 Cost Management 设置预算告警，建议阈值 1 美元和 5 美元。

如果需要“完全避免 VM 公网 IP 成本”，继续使用 Azure App Service F1 是更合适的公开 Web 部署方式；VM 更适合需要 SSH、后台任务和更可控运行环境的场景。

## Azure CLI 创建流程

下面的命令假设已经安装并登录 Azure CLI，且当前订阅是 Azure for Students。地区优先用 `eastasia` 或 `southeastasia`，如果目标地区没有 `Standard_B2ats_v2` 配额，就切换地区或退回 `Standard_B1s`。

这里的 Azure CLI 只负责创建和管理 VM、网络端口等 Azure 资源，不负责把当前本机工作区推送到服务器。KGTS 应用代码在 VM 内通过 `git clone` 首次拉取，后续更新用 `git pull --ff-only`，再重建前端并重启 systemd 服务。不要把本机 `.runtime/`、模型目录、临时导出或个人配置用 `az`/`scp` 直接覆盖到 VM 的项目目录。

```bash
az login
az account list --output table
az account set --subscription "<Azure for Students subscription id or name>"
```

检查 SKU：

```bash
az vm list-skus \
  --location eastasia \
  --size Standard_B2ats_v2 \
  --all \
  --output table
```

创建资源组和 VM：

```bash
az group create \
  --name kgts-student-rg \
  --location eastasia

az vm create \
  --resource-group kgts-student-rg \
  --name kgts-free-vm \
  --image Ubuntu2204 \
  --size Standard_B2ats_v2 \
  --admin-username azureuser \
  --os-disk-size-gb 64 \
  --storage-sku Premium_LRS \
  --generate-ssh-keys
```

只开放 SSH 和 Web：

```bash
az vm open-port --resource-group kgts-student-rg --name kgts-free-vm --port 22
az vm open-port --resource-group kgts-student-rg --name kgts-free-vm --port 80
```

## VM 内部署 KGTS

SSH 进入 VM 后执行：

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip nodejs npm nginx
```

1 GB 内存构建前端时建议加 swap：

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

拉代码并构建：

```bash
git clone https://github.com/mysteriousFourier/Knowledge-Graph-Teaching-System.git kgts
cd kgts
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cd frontend
npm ci
NODE_OPTIONS=--max-old-space-size=1536 npm run build
cd ..
```

前端生产构建默认不生成 sourcemap，以降低 1 GB 免费 VM 的构建内存和磁盘压力。如需调试线上 bundle，可临时执行 `VITE_BUILD_SOURCEMAP=1 npm run build`。`npm ci` 也会安装公式朗读用的 MathJax/Speech Rule Engine；主站会在 TTS 合成前调用它们把 LaTeX 转成朗读文本，失败时自动回退到 Python 内置转换。

后续更新代码使用本文后面的“更新部署”流程。如果更新包含种子图谱变更，`APP_BOOTSTRAP_SEED_DATA=1` 会在运行时数据库缺失或明显落后时把种子复制/合并到 `.runtime/`。生产运行数据仍应留在 `.runtime/`，不要把 `.runtime/knowledge_graph.db` 或 `.runtime/vector_index/` 反向提交回 `data/seed/`。

如果本地已经生成了更好的图谱数据库或向量索引，但文件太大不适合提交到 Git，把它们作为运行时数据同步到 VM 的 `.runtime/`。从本机 PowerShell 执行：

```powershell
ssh azureuser@<vm-ip> "mkdir -p ~/kgts/.runtime/vector_index && sudo systemctl stop kgts"
scp .runtime\knowledge_graph.db azureuser@<vm-ip>:~/kgts/.runtime/knowledge_graph.db
scp .runtime\vector_index\metadata.json azureuser@<vm-ip>:~/kgts/.runtime/vector_index/metadata.json
scp .runtime\vector_index\vector_index.faiss azureuser@<vm-ip>:~/kgts/.runtime/vector_index/vector_index.faiss
ssh azureuser@<vm-ip> "sudo systemctl start kgts && sudo systemctl status kgts --no-pager"
```

这条路径只更新 VM 运行时状态，不改变 Git 历史，也不会进入 Azure App Service 的 GitHub Actions 部署包。复制 SQLite 数据库前先停服务，避免写入过程中拿到半截文件。

Azure App Service 不使用这组 `scp` 命令。若 App Service 也需要这些大运行时文件，把 `APP_RUNTIME_DIR`、`GRAPH_DB_PATH` 和 `KGTS_VECTOR_INDEX_DIR` 指到 `/home/site/kgts-runtime`，再通过 Kudu/SSH/SCM 上传到该目录。不要把它们加入仓库，也不要放回 `data/seed/`。

有 Azure Portal 下载的 App Service publish profile XML 时，可以从本机 PowerShell 上传到 Kudu/SCM，不依赖本地 Azure CLI：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\upload_app_service_runtime_data.ps1 -PublishProfilePath .tmp\kgts.PublishSettings
```

上传目标是 `/home/site/kgts-runtime`；App Settings 仍需指向：

```text
APP_RUNTIME_DIR=/home/site/kgts-runtime
GRAPH_DB_PATH=/home/site/kgts-runtime/knowledge_graph.db
KGTS_VECTOR_INDEX_DIR=/home/site/kgts-runtime/vector_index
```

启用本地神经向量检索时使用 CPU-only 依赖文件，避免 PyPI 自动安装 CUDA 版 torch：

```bash
. .venv/bin/activate
python -m pip install -r requirements/vector-cpu.txt
```

创建生产环境配置：

```bash
cat > .env <<'EOF'
APP_BIND_HOST=127.0.0.1
APP_RUNTIME_DIR=.runtime
GRAPH_DB_PATH=.runtime/knowledge_graph.db
APP_BOOTSTRAP_SEED_DATA=1
APP_RUN_STARTUP_MAINTENANCE=0
RENDER_AUTO_SYNC_STRUCTURED=0
KGTS_RETRIEVAL_MODE=hybrid
KGTS_VECTOR_INDEX_DIR=.runtime/vector_index
KGTS_VECTOR_STARTUP_ENSURE=0
KGTS_VECTOR_UNLOAD_AFTER_QUERY=1
KGTS_VECTOR_UNLOAD_AFTER_REBUILD=1
KGTS_VECTOR_HASH_FALLBACK=1
KGTS_TTS_ENABLED=1
KGTS_TTS_PROVIDER=genie_server
KGTS_TTS_SERVER_URL=http://127.0.0.1:9880
KGTS_TTS_FORMULA_ENGINE=sre
KGTS_TTS_FORMULA_TIMEOUT_SECONDS=3
DEEPSEEK_GENERATION_READ_TIMEOUT_SECONDS=0
KGTS_SLIDE_LECTURE_MAX_OUTPUT_TOKENS=12000
KGTS_SLIDE_LECTURE_READ_TIMEOUT_SECONDS=0
KGTS_SLIDE_LECTURE_FLASH_READ_TIMEOUT_SECONDS=0
KGTS_SLIDE_LECTURE_COMPLETION_TIMEOUT_SECONDS=0
DEEPSEEK_API_KEY=
EOF
```

把 `DEEPSEEK_API_KEY` 改成实际值；不要提交 `.env`。

同一台 1 GB VM 需要同时保留 TTS 和神经向量检索能力时，检索配置保持低内存 hybrid：

```text
KGTS_RETRIEVAL_MODE=hybrid
KGTS_VECTOR_STARTUP_ENSURE=0
KGTS_VECTOR_UNLOAD_AFTER_QUERY=1
KGTS_VECTOR_UNLOAD_AFTER_REBUILD=1
KGTS_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
KGTS_EMBEDDING_CACHE_DIR=.runtime/huggingface
```

第一次下载模型时临时设置 `KGTS_EMBEDDING_LOCAL_FILES_ONLY=0`；模型缓存完成后再改回 `1`，保证运行只读项目目录内缓存。

## systemd 服务

```bash
sudo tee /etc/systemd/system/kgts.service >/dev/null <<'EOF'
[Unit]
Description=KGTS single-port web app
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=azureuser
WorkingDirectory=/home/azureuser/kgts
EnvironmentFile=/home/azureuser/kgts/.env
Environment=APP_RUNTIME_DIR=/home/azureuser/kgts/.runtime
Environment=GRAPH_DB_PATH=/home/azureuser/kgts/.runtime/knowledge_graph.db
Environment=KGTS_VECTOR_INDEX_DIR=/home/azureuser/kgts/.runtime/vector_index
ExecStart=/home/azureuser/kgts/.venv/bin/python -m uvicorn render_app:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now kgts
sudo systemctl status kgts --no-pager
```

## Nginx 反向代理

```bash
sudo tee /etc/nginx/sites-available/kgts >/dev/null <<'EOF'
server {
    listen 80;
    server_name _;

    location ~ /\.(?!well-known) {
        return 404;
    }

    # Rendered pages are immutable cache artifacts; serve them without using
    # the single FastAPI worker that handles course metadata and jobs.
    location ^~ /api/education/rendered-pages/ {
        alias /home/azureuser/kgts/.runtime/courseware/rendered-pages/;
        access_log off;
        expires 1y;
        add_header Cache-Control public;
    }

    location /assets/ {
        root /var/www/kgts;
        try_files $uri =404;
        access_log off;
        expires 1h;
    }

    location ~ ^/api/education/(generate-slide-lectures|upload-ppt|upload-ppt-preview|generate-ppt-tex|generate-lecture)$ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        # Nginx 1.18 treats a zero timeout as an immediate timeout. Keep a
        # practically unlimited duration for long-running education jobs.
        proxy_read_timeout 365d;
        proxy_send_timeout 365d;
        proxy_connect_timeout 365d;
        send_timeout 365d;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location ~ ^/api/tts/(synthesize|segments)$ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_read_timeout 365d;
        proxy_send_timeout 365d;
        proxy_connect_timeout 365d;
        send_timeout 365d;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Course reads can include persisted courseware metadata and rendered-page
    # references. Do not fail them because of a fixed proxy read timeout.
    location ~ ^/api/education/(courses(?:/.*)?|list-chapters|get-chapter|courseware/projects(?:/.*)?)$ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_read_timeout 365d;
        proxy_send_timeout 365d;
        proxy_connect_timeout 365d;
        send_timeout 365d;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/kgts /etc/nginx/sites-enabled/kgts
sudo nginx -t
sudo systemctl reload nginx
```

验证：

```bash
curl -I http://127.0.0.1:8000/
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1:8000/api/maintenance/graph/scope-tree | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]; print(len(d["nodes"]), len(d["relationships"]))'
curl -s http://127.0.0.1/api/health
```

逐页讲解、PPT/TeX 生成和整章讲稿生成会等待外部模型或 LaTeX 返回。所有教育接口代理的读写、连接和发送超时均设为 `0`，不设置固定秒数上限。课程读取不会因固定时限中断。ZIP 课件预览会先完成轻量解析并立即返回，PDF/LaTeX 预渲染进入单 worker 队列，前端通过渲染任务状态接口轮询结果；渲染完成的页面会持久化到 `APP_RUNTIME_DIR/courseware/rendered-pages`，后续相同 ZIP 直接复用缓存，删除课件时同步清理不再被引用的缓存。上传阶段不会加载未引用的大图片，真正渲染时才从队列 ZIP 读取资源，也不会新增或收紧上传大小限制。dotfile 规则用于让 `/.env` 这类扫描请求直接返回 404，避免被 SPA fallback 误判为有效路径。

如果由 Nginx 直接服务前端静态资源，把构建产物复制到 Web 可读目录，避免 Nginx 无法遍历 `/home/azureuser`：

```bash
sudo rm -rf /var/www/kgts
sudo mkdir -p /var/www/kgts
sudo cp -a /home/azureuser/kgts/frontend/dist/. /var/www/kgts/
sudo chown -R www-data:www-data /var/www/kgts
```

`/` 和 `/assets/` 必须来自同一次 `frontend/dist` 构建。若首页 HTML 引用的 `/assets/index-*.js` 返回 404，说明 Nginx 的 `/var/www/kgts` 静态目录和 FastAPI 读取的 `frontend/dist/index.html` 不同步；重新执行上面的复制命令并重启 `kgts`。

## 更新部署

以后更新 main 分支：

> 当前公开演示站点使用本节 VM 流程更新。不要把“推送到 GitHub”或 App Service GitHub Actions 当成这个 VM 的部署完成信号；VM 的 `/home/azureuser/kgts` 必须实际更新并重启服务。

```bash
cd ~/kgts
git fetch origin
git pull --ff-only origin main
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -r requirements/vector-cpu.txt
cd frontend
npm ci
NODE_OPTIONS=--max-old-space-size=1536 npm run build
cd ..
sudo rm -rf /var/www/kgts
sudo mkdir -p /var/www/kgts
sudo cp -a frontend/dist/. /var/www/kgts/
sudo chown -R www-data:www-data /var/www/kgts
sudo systemctl restart kgts
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1:8000/api/maintenance/graph/scope-tree | python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]; print(len(d["nodes"]), len(d["relationships"]))'
sudo journalctl -u kgts -n 80 --no-pager
```

如果改动涉及 TTS 代理或语音路由，例如 `core/tts_text.py`、`core/tts_service.py`、`education/tts_router.py`、`scripts/genie_tts_proxy_server.py` 或 `.env` 中的 `KGTS_TTS_*`，还需要重启 TTS 代理：

```bash
sudo systemctl restart kgts-tts
curl -s http://127.0.0.1:9880/status
curl -s http://127.0.0.1:8000/api/tts/status
sudo journalctl -u kgts-tts -n 80 --no-pager
```

TTS 相关更新后建议再合成一小段文本，确认代理返回的是可播放 WAV，而不是空文件、HTML 错误页或 OOM 后的半截文件。主站和代理都会校验 WAV 头和音频帧数；如果日志出现 `not a valid WAV file`、`empty or incomplete` 或 `no playable audio frames`，先查看 `kgts-tts` 日志和 VM 内存，再重新合成。

如果 VM 工作区不是干净的，先查清本地修改来源，不要直接 `git reset --hard` 或覆盖项目目录。生产运行数据应留在 `.runtime/`，不要混进 Git 工作区；确实需要同步大文件时只按前文运行时数据同步方式处理。

## 可选：本地图结构向量检索

1 GB VM 上向量模型不应常驻，只用于备课和问答。运行前建议让 TTS 代理空闲，必要时先停 TTS 代理：

```bash
sudo systemctl stop kgts-tts
```

重建索引：

```bash
cd ~/kgts
. .venv/bin/activate
python -m KGTS.core.cli_dispatch rebuild_vector_index
```

也可以通过应用里的图谱/问答流程触发查询。低内存配置下每次查询会加载 embedding 模型，查询后释放模型引用；首次查询会比较慢。完成备课/问答后如需朗读课件，再启动 TTS：

```bash
sudo systemctl start kgts-tts
```

检查状态：

```bash
curl -s http://127.0.0.1/api/local-assets/status
free -h
du -sh .venv .runtime models third_party 2>/dev/null
```

## 可选：本机 Genie-TTS 代理实验

这不是推荐生产配置，只用于验证 1 GB 免费 VM 是否能承载本地 `shu` TTS。先安装最小中文 Genie 依赖并确认 `models/tts/`、`third_party/Genie-TTS/` 已在 VM 本地存在；这些资产已被 `.gitignore` 排除，不要提交。

主站 `.env` 使用代理模式：

```text
KGTS_TTS_ENABLED=1
KGTS_TTS_PROVIDER=genie_server
KGTS_TTS_SERVER_URL=http://127.0.0.1:9880
KGTS_TTS_GENIE_DATA_DIR=models/tts/GenieData
KGTS_TTS_MODEL_DIR=models/tts/shu
KGTS_TTS_CHARACTER_NAME=shu
KGTS_TTS_LANGUAGE=zh
KGTS_TTS_REFERENCE_AUDIO=models/tts/shu/reference/shu.wav
KGTS_TTS_REFERENCE_LANGUAGE=zh
KGTS_TTS_REFERENCE_TEXT=我是谁？答案只在于我所见所遇的一切。
KGTS_TTS_PROXY_UNLOAD_AFTER_SYNTH=1
KGTS_TTS_PROXY_EXIT_AFTER_SYNTH=1
```

创建独立 TTS 服务：

```bash
sudo tee /etc/systemd/system/kgts-tts.service >/dev/null <<'EOF'
[Unit]
Description=KGTS Genie-TTS proxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=azureuser
WorkingDirectory=/home/azureuser/kgts
EnvironmentFile=/home/azureuser/kgts/.env
Environment=PYTHONPATH=/home/azureuser/kgts:/home/azureuser/kgts/third_party/Genie-TTS/src
Environment=OMP_NUM_THREADS=1
Environment=OPENBLAS_NUM_THREADS=1
Environment=MKL_NUM_THREADS=1
Environment=NUMEXPR_NUM_THREADS=1
Environment=TOKENIZERS_PARALLELISM=false
Environment=KGTS_TTS_GENIE_LOW_MEMORY=1
Environment=KGTS_TTS_ONNX_CACHE_DIR=/home/azureuser/kgts/.runtime/tts/onnx-fp32-cache
Environment=KGTS_TTS_PROXY_UNLOAD_AFTER_SYNTH=1
Environment=KGTS_TTS_PROXY_EXIT_AFTER_SYNTH=1
ExecStart=/home/azureuser/kgts/.venv/bin/python scripts/genie_tts_proxy_server.py
Restart=always
RestartSec=2
OOMPolicy=stop

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now kgts-tts
sudo systemctl restart kgts
```

验证：

```bash
curl -s http://127.0.0.1:9880/status
curl -s http://127.0.0.1:8000/api/tts/status
free -h
journalctl -u kgts-tts -n 100 --no-pager
```

`KGTS_TTS_PROXY_UNLOAD_AFTER_SYNTH=1` 会在每次合成后卸载 Genie 角色模型、HuBERT 和引用音频缓存。ONNX Runtime 在 1 GB VM 上不一定把全部内存还给系统，因此更稳的配置是同时开启 `KGTS_TTS_PROXY_EXIT_AFTER_SYNTH=1`，让代理在响应发出后退出并由 systemd 拉起一个空闲新进程。这个模式需要 `Restart=always`。代价是每次合成都接近冷启动。

如果合成时出现 OOM，`kgts-tts.service` 会失败或重启，但 `kgts.service` 应继续可用。这说明当前免费 VM 不能稳定承载本地 TTS；保留 `KGTS_TTS_ENABLED=1` 和 `KGTS_TTS_PROVIDER=genie_server`，把 `KGTS_TTS_SERVER_URL` 指向更高内存 VM 或外部推理服务。

## 官方参考

- Azure for Students: https://azure.microsoft.com/free/students/
- Azure free services: https://azure.microsoft.com/pricing/free-services/
- Create free services: https://learn.microsoft.com/azure/cost-management-billing/manage/create-free-services
- Bv1 sizes: https://learn.microsoft.com/azure/virtual-machines/sizes/general-purpose/bv1-series
- Basv2 sizes: https://learn.microsoft.com/azure/virtual-machines/sizes/general-purpose/basv2-series
- Public IP pricing: https://azure.microsoft.com/pricing/details/ip-addresses/
