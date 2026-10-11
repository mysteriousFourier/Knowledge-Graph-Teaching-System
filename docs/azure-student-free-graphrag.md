# Azure for Students VM：低内存 GraphRAG

继续使用现有 `kgts-free-vm`（`Standard_B2ats_v2`，约 1 GB RAM）、Nginx 和 `kgts.service`，无需新建 Azure Search、图数据库或 embedding 服务。

## 检索能力

项目原本已有节点召回、图关系扩展、课程子树范围和证据生成，属于局部 GraphRAG。本次将 `sparse_hybrid` 升级为 SQLite FTS5 BM25：英文词项与中文双字片段召回、标签加权，结合文本命中和图连接度重排。FTS 索引在原 SQLite 数据库中持久化；触发器记录待更新节点，维护 API、批量导入及直接 SQL 修改都会在下次检索前同步。

查询召回最多 128 个候选，再按关系扩展两跳；默认最多 24 个上下文节点，单节点最多 900 字符，总内容默认 12,000 字符。公式、例题、推导、前置关系、节点 ID 和来源信息保留，关系三元组直接进入模型上下文。选择课程后，查询限制在子树及直接引用中；显式空范围不会意外搜索全库。查询上下文不再加载全图，旧检索的前 5,000 节点限制也已移除。

## 上下文缓存

GraphRAG 上下文组装现在使用独立的 SQLite 持久缓存，默认位于 `.runtime/context-cache/context-cache.sqlite3`。缓存键包含规范化查询、课程/节点范围、检索参数、上下文长度和图数据库版本；图数据库、WAL 或共享内存文件发生变化时，旧键不会再次命中。缓存只保存可重建的检索证据和上下文，不保存用户问题的最终模型回答，因此不会把用户私有答案跨请求复用。

默认上限为 128 条、32 MiB、1 小时 TTL，并按最近访问时间清理。可通过 `KGTS_CONTEXT_CACHE_ENABLED=0` 关闭，或调整 `KGTS_CONTEXT_CACHE_MAX_ENTRIES`、`KGTS_CONTEXT_CACHE_TTL_SECONDS`、`KGTS_CONTEXT_CACHE_MAX_BYTES`。健康接口会返回 `context_cache` 的命中、未命中、写入和清理计数。

这个实现采用了开源 RAG 项目中常见的三种做法：LangChain 的可替换 cache backend 思路、LlamaIndex 对 embedding/response cache 的内容键设计，以及 Microsoft GraphRAG 对昂贵中间结果按输入版本复用的思路。这里选择 SQLite/file backend，是因为 Azure 单 worker 的内存约束不适合为缓存额外常驻 Redis；最终 LLM 输出仍不缓存，避免模型、权限和提示词变化造成陈旧结果。

这是无神经模型的局部 GraphRAG，不等同于 Microsoft GraphRAG 的全库社区发现、LLM 社区报告和全局 map-reduce。BM25 不具备神经 embedding 的跨语言或同义改写能力。需要这类语义召回时可切回现有 `hybrid` 和 CPU 模型，但不能把两种检索质量视为相同。

## 现有 VM 启用

通过现有代码发布流程将修改更新到 `/home/azureuser/kgts`，保留线上 `.env` 和 `.runtime`。不要用本机运行时数据库覆盖线上数据。只安装基础依赖；已有模型依赖可以留在磁盘上，`sparse_hybrid` 不会加载它们。

```bash
cd /home/azureuser/kgts
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c "import sqlite3; c=sqlite3.connect(':memory:'); c.execute('CREATE VIRTUAL TABLE t USING fts5(x)'); print('FTS5 available')"
sudo systemctl edit kgts
```

在编辑器中为现有服务添加以下覆盖配置，不要新建第二个占用 8000 端口的服务：

```ini
[Service]
EnvironmentFile=/home/azureuser/kgts/deploy/azure-student-free.env
ExecStart=
ExecStart=/home/azureuser/kgts/.venv/bin/python -m uvicorn render_app:app --host 127.0.0.1 --port 8000 --workers 1 --limit-concurrency 16 --limit-max-requests 2000
Restart=always
RestartSec=3
MemoryAccounting=true
MemoryHigh=650M
MemoryMax=800M
TasksMax=64
```

原服务应有 `WorkingDirectory=/home/azureuser/kgts` 和 `.env` EnvironmentFile；免费配置文件必须排在 `.env` 后。新安装可参考 [完整 service 模板](../deploy/kgts-student-free.service)，安装为 `kgts.service`。运行时路径基于 WorkingDirectory；如果线上有自定义数据目录，先调整免费配置文件中的路径以匹配线上位置。

```bash
sudo systemctl daemon-reload
sudo systemctl restart kgts
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/local-assets/status
sudo systemctl show kgts -p MemoryCurrent -p MemoryPeak
sudo journalctl -u kgts -n 60 --no-pager
```

健康接口应返回 `retrieval_mode: sparse_hybrid`，检索状态应返回 `provider: sqlite-fts5-bm25`。首次有效查询自动建索引，也可提前在低流量维护窗口执行：

```bash
KGTS_RETRIEVAL_MODE=sparse_hybrid GRAPH_DB_PATH=.runtime/knowledge_graph.db .venv/bin/python -c "from KGTS.core.graph_service import GraphService; print(GraphService().rebuild_vector_index())"
```

初次建立索引会持有 SQLite 写锁并增加磁盘占用。备份使用 SQLite backup API 或先停服务；图数据和索引同库备份，不需要额外向量文件。缺少 FTS5 时检索显式报错，需要修复 Python/SQLite 环境。

## 资源与费用

- Web 单 worker，最多 16 个并发连接/任务；超限返回 503。每 2,000 请求后退出并由 systemd 重启，存在短暂不可用时间。
- 内存软上限 650 MB、硬上限 800 MB；超限可能被系统终止并重启。需结合线上课件导出等工作负载验证，检索基准不是全站峰值保证。
- TTS 继承 `.env` 的提供方、开关和 URL，支持保留现有 Azure Speech 或独立 `genie_server`。1 GB VM 无法保证 Web 与本地语音模型同时运行；大型语音合成仍需错峰或使用已有外部推理服务。本配置不停止或删除 TTS 服务。
- 图检索无模型调用费用。自然语言答案、讲稿和课件仍使用现有 DeepSeek API，费用独立于 Azure 学生额度；未配置 Key 时问答保留本地证据回退。
- Students VM 免费资格有期限和月用量限制。磁盘、公网 IP、带宽及超期规格可能消耗余额，不保证账单恒为零。继续在 Portal 检查现有 VM 的 Free services 与 Cost Management。

## 验证与恢复

```bash
.venv/bin/python -m pytest tests/test_sparse_graphrag.py tests/test_graph_hybrid_retrieval.py tests/test_ppt_formula_graph_context.py -q
.venv/bin/python scripts/benchmark_free_graphrag.py --db .runtime/knowledge_graph.db
```

基准脚本创建数据库临时快照，完成后清理，不修改源数据。报告建索引时间、索引体积、端到端图上下文延迟、召回结果及是否加载 torch/FAISS；Linux 额外报告检索进程最大 RSS。应在真实 VM 上复测，本机耗时不能代表云端性能。

恢复神经模式：在 `deploy/azure-student-free.env` 中将 `KGTS_RETRIEVAL_MODE` 改为 `hybrid`，安装 `requirements/vector-cpu.txt`，准备模型缓存，再重启 `kgts`。新增 FTS 表不影响原图数据或 FAISS 索引。

官方参考：[Azure for Students](https://azure.microsoft.com/free/students/)、[免费服务说明](https://learn.microsoft.com/azure/cost-management-billing/manage/create-free-services)、[B 系列 VM](https://learn.microsoft.com/azure/virtual-machines/sizes/general-purpose/basv2-series)。
