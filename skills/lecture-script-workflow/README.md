# 逐页课堂讲稿整理 · 可复用工作流（v1.0）

适用于：**课堂录制/逐字稿 + PDF课件 + 上一讲参考讲稿 + 可选TeX/PPT源码**，生成能直接念的中文逐页讲稿。允许录音只覆盖部分PDF，其余页面根据实际课件、参照教师已有讲法做**教师风格模拟补讲**，并在独立的对齐台账里标注来源。

## 以后每次怎么用

**最省事的方式：** 建一个固定的 ChatGPT 项目，例如“课堂逐页讲稿”，将 `PROJECT_INSTRUCTIONS.md` 的内容放进项目指令，参考讲稿作为项目参考文件保留。今后在这个项目中上传新的录音和PDF，发送一句：

> 按固定讲稿整理工作流处理这组文件：先核对录音与PDF是否匹配，逐帧对齐，保留原讲法，仅清理口误和重复。录音缺页按教师原有讲课方式补讲，所有公式用能直接朗读的中文，最后交付一个完整紧凑的Markdown文件及对齐质量报告。无需分段征求确认。

**可移植方式：** `SKILL.md` 是标准Agent Skill主文件，打包了详细处理规范。把整个文件夹添加到支持Agent Skills的智能体/执行环境时，可以自动加载该工作流。`scripts/` 里是工具脚本，智能体需具备文件读写及Python运行权限。`PROJECT_INSTRUCTIONS.md` 则可在不安装Skill的环境中使用。

## 输入

**必需**：课件PDF，课堂录制（音频/视频）或会议转写文本。**推荐**：上一讲的已完成讲稿（稳定模仿格式和口吻）。**可选**：TeX/PPT源码、包含多份候选逐字稿的ZIP、课程术语表。只有PDF也可以做**模拟授课讲稿**，但不能称作录音整理。

## 硬性标准

- 按PDF实际页数（包括每一帧overlay），而不是TeX frame数。每页恰好一节 `## 第X页`。
- 录音决定讲解内容和顺序；保留老师的提问、类比、展开逻辑，**不重新写成教材**。
- 没有录音的页由当前PPT内容自然补讲；模拟补写和录音原话在**台账**里严格区分，主稿不塞入标记。
- 公式全部改成可朗读的中文，不能保留TeX代码或不能直接念的复杂符号。
- 一页标题加紧凑正文，减少空行；一次交付完整Markdown，不在中途要求用户输入“继续”。
- 全文默认目标不少于**32000个汉字**（可按新任务调整），但不能靠复制内容或创造不存在的PPT事实充数。

## 一次完整运行

1. `python scripts/slide_index.py AT_06.pdf --out work/page_index.csv --contact-dir work/contact_sheets` —— 创建PDF实际页面目录和缩略图，供逐页视觉核对。
2. 按 `SKILL.md` 的流程识别录音、对齐各页，逐步写入一个主文件；同时按 `templates/coverage.csv` 的字段记录来源。
3. `python scripts/audit_script.py --pdf AT_06.pdf --script 完整讲稿.md --coverage 完整讲稿_对齐记录.csv --min-hanzi 32000 --report 验收报告.md` —— 机械验收。
4. 看验收报告，修复漏页、重复、残留数学代码等问题；再人工复核至少首、中、尾和所有模拟补讲的高风险页，交付。

如果没有运行脚本的环境，也可以依据同一规范人工逐页制作与验收，但不可谎称完成自动检查。

## 文件结构

```text
lecture-script-workflow/
  SKILL.md                     # 最完整的AI执行规范
  PROJECT_INSTRUCTIONS.md      # 可复制到ChatGPT项目的简短指令
  README.md                    # 使用说明
  scripts/
    slide_index.py             # PDF逐帧索引与联览图
    audit_script.py            # 页码/字数/口播/重复质检
  templates/
    coverage.csv               # 每页录音与模拟来源台账
  references/
    oral_math_guide.md         # 常见公式的正确中文朗读方式
    style_fidelity.md          # 轻校订与教师风格补讲示例
```

备注：`slide_index.py` 需要 PyMuPDF 和 Pillow；`audit_script.py` 仅PDF页数读取需要 PyMuPDF（不提供PDF时可只检查讲稿本身）。脚本不能代替对录音与PDF语义匹配的人工/模型检查。
