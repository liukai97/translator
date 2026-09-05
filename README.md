# translator

个人日语小说翻译流水线。项目把“原文分段、批次翻译、进度保存、结构校验、左右对照阅读、译文 EPUB 回制”做成可恢复、可检查、可继续的工作流。

## 当前定位

- 输入：日语小说 `txt` / `md`，当前默认使用 `input/book.md`。
- 中间层：`work/segments.jsonl` 保存稳定段落 ID，`work/batches.jsonl` 保存翻译批次，`work/translations.jsonl` 保存已完成译文。
- 翻译方式：Codex 按 `skills/japanese-novel-translation/SKILL.md` 的规则逐批翻译，脚本负责准备请求、合并输出和校验结构。
- 输出：`output/parallel.html` 用于原文 / 译文左右对照阅读；翻译完成后也可生成保留原书资源和阅读顺序的中文 EPUB。

## 目录说明

```text
input/                         原始输入文本
work/book.no_pages.md           去掉 [Page N] 标记后的正文
work/segments.jsonl             原文分段结果，稳定对齐层
work/batches.jsonl              LLM 翻译批次
work/translations.jsonl         已确认写入的译文进度
work/checkpoints/               单批请求和模型输出草稿
output/parallel.html            左右对照阅读文件
output/*.zh-CN.epub             从译文回制的中文 EPUB
scripts/                        分段、批次、合并、校验、组装脚本
skills/japanese-novel-translation/SKILL.md
                               翻译风格、格式和 QA 规则
```

## 数据流

```text
input/book.md
  -> scripts/strip_page_markers.py
  -> work/book.no_pages.md
  -> scripts/segment.py
  -> work/segments.jsonl
  -> scripts/make_batches.py
  -> work/batches.jsonl
  -> scripts/translation_task_loop.py
  -> work/checkpoints/<batch>_request.md
  -> work/checkpoints/<batch>_output.jsonl
  -> work/translations.jsonl
  -> output/parallel.html
  -> scripts/build_translated_epub.py
  -> output/<原书名>.zh-CN.epub
```

`segments.jsonl` 是最重要的对齐层。每个原文段落都有稳定 `id`，后续译文必须用同一个 `segment_id` 对应回来。不要在翻译时合并、拆分、跳过或重排段落。

## 首次准备一本书

把原文放到 `input/book.md` 后，按顺序运行：

```powershell
.\.python312\python.exe scripts\strip_page_markers.py
.\.python312\python.exe scripts\segment.py
.\.python312\python.exe scripts\make_batches.py
.\.python312\python.exe scripts\validate_translation_progress.py
```

常用可调参数：

```powershell
.\.python312\python.exe scripts\segment.py --max-segment-bytes 500
.\.python312\python.exe scripts\make_batches.py --max-bytes 10000 --max-segments 24
```

如果已经有新的 `work/segments.jsonl` 和 `work/batches.jsonl`，通常不需要重复准备。重建这些文件前要确认不会破坏已有 `work/translations.jsonl` 的段落 ID 对齐关系。

### 从 EPUB 生成 Markdown

EPUB 输入可以先转换成适合分段和翻译的纯文本 Markdown：

```powershell
.\.python312\python.exe scripts\epub_to_markdown.py "input\书名.epub"
```

默认输出为 EPUB 同目录、同名的 `.md` 文件，也可以显式指定路径：

```powershell
.\.python312\python.exe scripts\epub_to_markdown.py "input\书名.epub" -o "input\转换结果.md"
```

转换器按 EPUB `spine` 中声明的阅读顺序处理内容，不依赖 XHTML 文件名。独立图片不会写入 Markdown，其非空 `alt` 可作为章节标题；文字块中的行内图片会变成可逆的 `⟦EPUB_IMG:...⟧` 占位符。日文振假名会保留正文汉字并删除 `rt` / `rp` 读音，以免 HTML 标签进入后续翻译流程。脚本默认拒绝覆盖已有输出；确认需要覆盖时添加 `--force`。

## 从译文回制 EPUB

完成全部翻译并通过进度校验后，先执行只读映射检查：

```powershell
.\.python312\python.exe scripts\build_translated_epub.py `
  "input\放課後の魔術師(1).epub" `
  --dry-run
```

检查通过后生成中文 EPUB：

```powershell
.\.python312\python.exe scripts\build_translated_epub.py `
  "input\放課後の魔術師(1).epub"
```

默认读取 `work/book.no_pages.md`、`work/segments.jsonl` 和 `work/translations.jsonl`，输出到 `output/<原书名>.zh-CN.epub`。也可以显式指定所有路径：

```powershell
.\.python312\python.exe scripts\build_translated_epub.py `
  "input\原书.epub" `
  --source "work\book.no_pages.md" `
  --segments "work\segments.jsonl" `
  --translations "work\translations.jsonl" `
  --output "output\原书.zh-CN.epub"
```

构建器会先确认原 EPUB 回抽结果与 canonical source 完全一致，再按 spine 顺序做确定性 DOM 映射。它会保留链接属性和原始图片节点、把正文改为从上到下的横排并将翻页方向设为从左到右、更新书名/语言/UUID/目录，并校验 ZIP 结构、资源哈希、本地引用和最终回读内容。任何映射歧义或结构漂移都会停止构建。默认拒绝覆盖已有输出；确认替换时使用 `--force`。源 EPUB 始终只读。

## 日常续译流程

常规入口是：

```powershell
.\.python312\python.exe scripts\translation_task_loop.py
```

脚本会自动：

1. 校验当前 `segments / batches / translations` 的结构。
2. 找出第一个仍有缺失译文的批次。
3. 如果该批次已有 `work/checkpoints/<batch>_output.jsonl`，先验证并追加到 `work/translations.jsonl`。
4. 准备下一批 Markdown 翻译请求，并打印完整请求内容。

当输出报告里的 `action` 是 `translate` 时，阅读打印出的请求，只翻译 `Translation Targets` 部分，并把结果写入报告里的 `model_output_path`，形如：

```json
{"segment_id":"ch014-p0001","translation":"...","status":"translated"}
```

每个目标段落必须正好输出一行 JSONL。写好后再次运行同一个命令：

```powershell
.\.python312\python.exe scripts\translation_task_loop.py
```

这会提交刚写好的批次并准备下一批。完成本轮想翻译的最后一批后，使用：

```powershell
.\.python312\python.exe scripts\translation_task_loop.py --assemble
```

`--assemble` 会先提交当前批次输出，再做最终校验并重建 `output/parallel.html`。普通批次之间不要每次都手动重建 HTML。

## 查看进度

简洁进度：

```powershell
.\.python312\python.exe scripts\translation_progress_report.py --list-pending 20
```

完整 JSON 校验报告：

```powershell
.\.python312\python.exe scripts\validate_translation_progress.py --list-pending 20
```

校验脚本会检查：

- source segment ID 是否重复。
- batch 是否覆盖全部 segment。
- translation 是否有重复、空译文或无法匹配的 `segment_id`。
- 下一批缺失译文的位置。
- 指定 checkpoint 范围是否已经完成。

## 手动合并单批输出

日常建议使用 `translation_task_loop.py`。如果需要手动处理某个批次，可用：

```powershell
.\.python312\python.exe scripts\merge_model_output.py "work\checkpoints\ch014-b001_output.jsonl" --batch ch014-b001 --append
```

这个脚本会把模型输出规范化为源文顺序，并确认输出 ID 正好等于该批次当前仍缺失的 ID。通过后才会追加到 `work/translations.jsonl`。

只校验并追加一个已有草稿文件时也可以用：

```powershell
.\.python312\python.exe scripts\append_translations.py "work\checkpoints\draft.jsonl" --dry-run
.\.python312\python.exe scripts\append_translations.py "work\checkpoints\draft.jsonl"
```

## 生成阅读文件

常规最终命令：

```powershell
.\.python312\python.exe scripts\translation_task_loop.py --assemble
```

只想单独重建 HTML 时：

```powershell
.\.python312\python.exe scripts\assemble_parallel.py
```

调试时显示段落 ID、批次和状态：

```powershell
.\.python312\python.exe scripts\assemble_parallel.py --show-metadata
```

输出文件为：

```text
output/parallel.html
```

## 翻译输出规则

翻译批次必须遵守：

- 使用 `segment_id`，且必须与请求中的源文 ID 完全一致。
- 每个目标 segment 正好一条译文。
- 不合并、不拆分、不重排、不跳过非空正文。
- `translation` 不能为空。
- 源文内部有多少个换行符，译文内部也必须有相同数量的换行符。
- JSONL 每条记录必须在同一个物理行内，段内换行写成字符串里的 `\n`。
- 普通完成项使用 `status: "translated"`。
- 不确定但已尽力翻译的项使用 `status: "needs_review"`，并添加 `review_note`。

推荐记录格式：

```json
{"segment_id":"ch014-p0001","translation":"译文","status":"translated"}
{"segment_id":"ch014-p0002","translation":"需要复核的译文","status":"needs_review","review_note":"复核原因"}
```

## 常见注意事项

- Windows PowerShell 里中文或日文有时会显示乱码，不一定代表文件损坏。项目文件按 UTF-8 读写，优先用脚本校验 JSON 结构。
- 不要直接编辑 `work/translations.jsonl` 来覆盖已有译文，除非明确是在做修订并知道会影响哪些 `segment_id`。
- 如果校验报重复 `segment_id`，先停止续译，清理重复项后再继续。
- 如果 `translation_task_loop.py` 提示模型输出比请求文件旧，说明 checkpoint 可能是旧批次残留，应重新确认当前请求后再写输出。
