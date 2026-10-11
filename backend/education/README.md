# 教学 API 兼容入口

`backend/education/` 是教学 API 的兼容入口目录。它主要保留旧的启动路径，真实业务逻辑已经迁移到仓库根目录的 `education/` 包。

## 运行模式

### 单端口模式

根目录 `render_app.py` 会把教学相关路由挂载到同一个 Web 服务中。

常见路径：

- `/api/education/*`
- `/api/student/*`
- `/api/teacher/*`

### 拆分服务模式

如果需要单独启动教学 API：

```bash
python backend/education/api_server.py
```

默认端口是 `8001`。

## 关键文件

| 文件 | 作用 |
| --- | --- |
| `backend/education/api_server.py` | FastAPI 兼容启动入口 |
| `education/router.py` | 教学主路由 |
| `education/router_student.py` | 学生端路由 |
| `education/router_teacher.py` | 教师端路由 |
| `core/bridge.py` | 图谱、章节和导入导出的桥接层 |

## 能力范围

- 讲稿生成
- 问答
- 学习计划
- 练习题生成与反馈
- 教师端题库追加生成：每次请求 5 道新题，总题库不设上限
- 学生端练习抽题：排除教师点踩题目，每次随机返回最多 10 题
- PPT 解析与逐页讲稿生成
- 逐字稿辅助讲稿生成：教师可在备课页载入带时间戳的 `.txt/.md` 逐字稿。系统按页面主题抽取短片段作为噪声参考，并以 PDF/课件和图谱证据校正；连续动画页只讲新增内容。
- 章节读写与图谱联动

## 依赖的数据与配置

- `DEEPSEEK_API_KEY`
- `DEEPSEEK_FLASH_MODEL`
- `DEEPSEEK_PRO_MODEL`
- `.runtime/chapters.json`
- `structured/`
- 当前知识图谱数据库

线上低资源部署还建议设置：

- `DEEPSEEK_GENERATION_READ_TIMEOUT_SECONDS=0`
- `KGTS_RETRIEVAL_MODE=hybrid`
- `KGTS_VECTOR_STARTUP_ENSURE=0`
- `KGTS_VECTOR_UNLOAD_AFTER_QUERY=1`
- `KGTS_VECTOR_UNLOAD_AFTER_REBUILD=1`
- `KGTS_TTS_ENABLED=1`
- `KGTS_TTS_PROVIDER=genie_server`

这些配置能减少 Azure F1 或 1 GB 免费 VM 的启动压力，同时保留讲稿、问答、练习生成、向量检索和 TTS 入口。

## 维护约定

- 改业务逻辑时优先改根目录 `education/`
- 只有在旧启动方式、兼容入口或路径约定变化时才改这里
- 如果改了请求/响应结构，记得同步前端和 `models/`
- 生成题目、讲稿和问答依赖外部模型，排查问题时应以前台运行服务查看日志为准，不建议静默后台启动
