# 已有文案拆分补全与更改课件

备课工作台的“已有文案拆分补全”替代“载入逐字稿”。点击后选择本次原稿，可放入多个文件或直接粘贴；参考讲稿单独上传。默认使用当前课件，也可以选择新课件。补讲长度参考备课页已确认的授课时长。

支持 TXT、MD、SRT、VTT、TEX、DOCX、文字 PDF，以及包含这些材料的 ZIP。ZIP 中的原稿保留各自文件名，模型按主题、公式和页面顺序选择匹配材料。每次上传最多 20 个文件，展开后最多 40 份材料，合计 20 万字符；单个文件及压缩包解压总量最多 32 MB。音视频须先转写；材料 PDF 的扫描页面须先 OCR，避免将无法读取的内容当成已读取。

课件使用原有上传、渲染服务，包含 PDF、PPTX、TeX/ZIP 等已有格式。等待渲染完成后才处理。解析页数与实际渲染页数不符时会提示直接上传渲染后的 PDF，防止漏掉动画帧。处理使用原有后台任务及进度轮询，无需选择图谱节点。

生成讲稿时保留原有问法、例子和推理顺序；缺少的页面依实际课件补讲。每页的来源、原稿文件、段落编号及材料中已有的时间戳存入 `slide_lectures[].alignment`，随课件/课程保存。参考讲稿只提供风格，不能被标为本次录音来源。模型输出的数学源码会触发重试，失败页面不能冒充成功。

完成后提供完整 Markdown 讲稿、CSV 对齐记录及核验报告下载。主稿页标为 `## 第X页`；来源信息留在独立对齐记录。CSV 兼容原 skill 的台账列，并增加 `source` 和 `source_segments`。机械检查不能证明语义对齐，应抽查实际课件与讲稿。编辑后的下载文件由当前文稿即时生成。

“更改课件”替换当前课件。页数相同则继承原稿；页数变化时模型只选择连续的原文分界，服务端据此拆分或合并，不让模型改写原文。每个原句只能被分配一次，所有原句必须保留，总字数须不小于原稿；没有原稿可分配的新页面另行补讲，标记为 `simulated`。原稿中的数学源码会保留并列入核验提示，避免更换课件时擅自改写。处理失败时，界面保持原课件、文稿与课程标题，可选择相同文件重试。成功后使用原有保存按钮保存结果。

工作流源文件完整保存在 `skills/lecture-script-workflow/`。产品逻辑位于 `education/lecture_alignment.py`，实际加载 skill 的教师讲法与公式口播参考；没有将 skill 中特定用户的“至少 32000 字”设为所有课程的统一目标。

验证命令：

```powershell
$env:PYTHONPATH=(Split-Path (Get-Location) -Parent)
.venv\Scripts\python.exe -m pytest tests/test_lecture_alignment.py tests/test_ppt_parser_courseware.py tests/test_courseware_transport.py -q
cd frontend
npm run build
```

浏览器回归位于 `frontend/tests/lectureAlignment.browser.mjs`。默认连接本地 Vite 3018 端口，使用既有 Playwright 安装；覆盖材料上传、下载、课件增页/减页、同页数替换、失败保留及手机布局。回归使用模型响应桩，真实模型的讲稿质量仍需结合实际材料抽查。

线上是 Azure VM，GitHub 推送不会自动更新站点。发布时按 [Azure VM 更新流程](azure-student-vm.md#更新部署) 拉取提交、重建前端、同步静态资源并重启 `kgts`，再验证 `/api/health`、新按钮以及 `/openapi.json` 中的 `/api/education/lecture-materials`。本次未改动 TTS 服务。
