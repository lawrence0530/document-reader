# Document Reader Agent Skill 实现计划

## Repository Research

### 当前状态
- 项目目录为空（全新项目），路径：`c:\data\code\document-reader`
- 目标：创建一个 TRAE Agent Skill，支持读取 Office（Word/Excel/PPT）、PDF、图片、纯文本等多种文件并提取文本和结构化数据

### 技术约束与选择（最终修订）
- **运行时**：Python 3.10+（用户指定）
- **Skill 规范**：遵循 TRAE Skill 格式（SKILL.md frontmatter + 使用说明 + Python 代码）

---

#### PDF 解析策略（三级降级，用户指定链）
| 层级 | 方案 | 关键能力 |
|------|------|----------|
| **Level 1（首选）** | **mineru-open-sdk**（用户指定） | 官方免费在线 SDK；Apache-2.0；两种模式：① Flash Extract（**免 Token**，≤10MB/≤20页）② Precision Extract（需免费 Token，≤200MB/≤200页）；支持 PDF/Word/PPT/Excel/图片 → Markdown；内置 OCR / 表格 / 公式 开关 |
| **Level 2（回退）** | **markitdown[all]**（用户指定） | 微软开源 MIT；快速 Markdown 输出；可选 `markitdown-ocr` 插件（LLM Vision，纯 Python，无需系统级二进制）|
| **Level 3（兜底）** | **pypdf** | 最轻量本地文本提取；支持加密 PDF 解密（password 参数）|

#### OCR 方案（两条路径，均无需 pytesseract/系统级二进制）

> **直接回应："有 markitdown[all] 了还要 markitdown-ocr 吗？"**
> **一句话结论**：独立图片 OCR → **不需要** markitdown-ocr，只要 markitdown[all] + 传 `llm_client` 即可；**扫描件 PDF / Office（DOCX/PPTX/XLSX）内嵌截图 OCR → 必须** markitdown-ocr + llm_client。

**📊 markitdown[all] vs markitdown-ocr 能力边界（官方 README 验证）**

| 场景 | 仅 markitdown[all]<br>（不传 llm_client） | markitdown[all] + 传 llm_client<br>（**不需要** markitdown-ocr） | 再加 markitdown-ocr 插件<br>+ llm_client |
|------|-------------------------------------------|------------------------------------------------------------------|--------------------------------------------|
| 独立图片文件 (.jpg/.png) | ❌ 只提取 EXIF 元数据（相机/时间/尺寸），不识字 | ✅ LLM Vision 直接识别图片文字 + 语义描述 | ➖ 同左，无额外提升 |
| 扫描件 PDF（全是图/无原生文本层） | ❌ text 几乎为空（只有页码） | ❌ 原生 PDF 转换器不会主动把每页图发 LLM | ✅ **这是 markitdown-ocr 核心用途**：逐页扫描嵌入图 → LLM Vision OCR |
| DOCX/PPTX/XLSX 内嵌截图 | ❌ 忽略截图，只抽段落/表格文本 | ❌ 仍忽略内嵌图 | ✅ 遍历文档内嵌图片 → LLM Vision 识字 |
| 原生文本 PDF / Office / 纯文本 | ✅ 完美，本地离线，零费用 | ➖ 同左 | ➖ 同左 |

**整体 OCR 决策链（本 Skill 实现逻辑）**
- **在线首选（mineru-open-sdk）**：一参开关搞定所有场景。独立图 / 扫描件 PDF / Office 内嵌图 → 传参 `ocr=True` 或 `is_ocr=True`（MinerU 云端完成，免费，无需 llm_client）
- **离线降级（markitdown 分支）**：
  - ① 先判断是否传了 `llm_client` → 没传则跳过所有 LLM OCR，只抽原生文本层
  - ② 独立图片 → markitdown[all] 原生能力直接 OCR（**不用** markitdown-ocr）
  - ③ 扫描件 PDF / DOCX / PPTX / XLSX → 检测到**已安装 markitdown-ocr 且已 enable_plugins** 时才开启内嵌图 OCR；否则退化为原生文本层（扫描件会返回空 text 并在 WARNING 提示）

**关于是否保留 markitdown-ocr 的结论**
- 保留为 **「可选，独立第三方插件，默认 opt-in 关闭」**，因为：
  1. mineru-open-sdk 在线 OCR 已经覆盖绝大多数正常场景（免费、不占本地算力、无 llm_client 费用）
  2. 仅当用户需要「纯离线 + 能识别扫描件 PDF / Office 内嵌图」时，才需要额外 `pip install markitdown-ocr` + 手动传 llm_client + 显式 `enable_markitdown_ocr=True`
  3. 不装也不影响其它功能，零感知

#### Office（Word/Excel/PPT）解析策略
**两级简化（在线优先 → 本地通用兜底）**：`markitdown[all]` 已内置并自动安装 Docx/PPTx/XLSX/XLS 的底层解析依赖，无需重复声明专用库。
- **Level 1（在线，文件大小/页数适配时优先）**：mineru-open-sdk 直接处理（Precision 支持 Doc/x、Ppt/x、Excel；Flash 支持 Docx、PPTx、Excel）→ Markdown + 结构化 JSON。
- **Level 2（本地兜底）**：markitdown[all] → Markdown；Markdown 表格反向解析写入 `tables` 字段。

#### 纯文本 / 结构化（CSV/JSON/XML/MD）
- 本地 `chardet` + `pandas` + 内置库；CSV → `tables`，JSON/XML 美化预览，MD 写入 `markdown` 字段。

#### 图片解析
- **Level 1（在线）**：mineru-open-sdk（Flash/Precision 均支持图片输入；传参 `is_ocr=True` 云端 OCR，免费，无需 llm_client）
- **Level 2（离线降级）**：markitdown[all]
  - 用户**传了 llm_client** 时：原生能力直接对图片做 LLM Vision 识别（不需要 markitdown-ocr），写入 text/markdown
  - **未传 llm_client** 时：提取 EXIF 元数据 + Pillow 尺寸/格式信息写入 metadata，text 返回占位提示（说明需要 llm_client 或走在线 mineru 才能识字）

### Skill 输出数据结构
统一的解析结果 `ParsedDocument`（dataclass）包含：
- `file_path`, `file_type`: 文件路径与类型标签
- `text`: 全文本（从 markdown 或专用提取结果拼接）
- `pages`: 按页/按分片的文本列表（PDF/PPT/有页概念的格式）
- `tables`: 表格列表（list of 二维数组，DataFrame 可直接消费）
- `markdown`: Markdown 文本（mineru-open-sdk / markitdown 产出时填充，便于 LLM 直接消费）
- `metadata`: 元数据字典（作者、标题、页数、创建日期、文件大小、sdk_mode 等）
- `parser_used`: 实际使用的解析器链（调试追踪，如 `"mineru-open-sdk[flash]"` / `"mineru-open-sdk(failed NetworkError) -> markitdown"` / `"markitdown"`）

---

## Files and Modules

```
document-reader/
├── SKILL.md                              # Skill 定义与使用说明
├── requirements.txt                      # Python 依赖声明（分层：核心 / 可选增强）
├── src/
│   ├── __init__.py                       # 导出公共 API: DocumentReader, ParsedDocument, DocumentParseError
│   ├── document_reader.py                # 主入口 DocumentReader 门面类
│   ├── parsers/
│   │   ├── __init__.py
│   │   ├── base_parser.py                # 解析器基类 Protocol + DocumentParseError 异常
│   │   ├── mineru_sdk_parser.py          # ★ mineru-open-sdk 封装（PDF/Office/图片通用入口）
│   │   ├── pdf_parser.py                 # PDF 总调度（mineru-sdk → markitdown → pypdf 三级降级 + 空白 OCR 重试）
│   │   ├── office_parser.py              # Word/Excel/PPT（优先 mineru-sdk → markitdown 两级降级）
│   │   ├── image_parser.py               # 图片（mineru-sdk → markitdown + markitdown-ocr 降级）
│   │   ├── text_parser.py                # 纯文本 / CSV / JSON / XML / MD
│   │   └── markitdown_wrapper.py         # markitdown 统一封装（被 PDF/Office/Image 降级复用）
│   ├── models/
│   │   ├── __init__.py
│   │   └── document.py                   # ParsedDocument 数据类 + 便捷方法
│   └── utils/
│       ├── __init__.py
│       ├── file_type_detector.py         # 扩展名映射 + 可选 python-magic 的文件类型检测
│       └── dependency_checker.py         # mineru-open-sdk / markitdown / markitdown-ocr 可用性检测
└── tests/
    ├── __init__.py
    └── test_document_reader.py           # 单元测试 + 集成测试（在线/重型依赖可 skip）
```

---

## Implementation Steps

### Step 1: 初始化项目结构与依赖
1. 创建目录树（`src/`、`src/parsers/`、`src/models/`、`src/utils/`、`tests/`）
2. 编写 `requirements.txt`，分三层，按需安装：
   - **核心（必装）**：`mineru-open-sdk`、`markitdown[all]`、`pypdf`、`pandas`、`Pillow`、`chardet`
   - **可选本地增强**：`python-magic`（真实 MIME 检测，不依赖扩展名）
   - **OCR 兜底插件（可选，独立第三方 PyPI 包，不包含在 markitdown[all] 内）**：`markitdown-ocr`（扫描件/图片 LLM Vision OCR，需 LLM API Key；默认 opt-in 关闭）
3. 创建所有 `__init__.py` 空文件

### Step 2: 定义数据模型 `ParsedDocument`
1. 在 `src/models/document.py` 定义 `@dataclass ParsedDocument`（字段见 Repository Research）
2. 便捷方法：`to_dict()`, `to_json(indent=2)`, `preview_chars(n=500)`, `__repr__`（仅预览前 N 字，避免长文本刷屏）
3. 在 `src/models/__init__.py` 导出

### Step 3: 实现工具模块
1. **文件类型检测** `src/utils/file_type_detector.py`：
   - 常量 `EXT_TO_TYPE`：`pdf/docx/doc/xlsx/xls/pptx/image/text/csv/json/xml/md/unknown`
   - `detect_file_type(path, use_magic=True) -> str`：优先扩展名；可选 `python-magic`（未安装自动 fallback 到纯扩展名）
2. **依赖/能力检测** `src/utils/dependency_checker.py`：
   - `is_mineru_sdk_available() -> bool`（import mineru）
   - `is_markitdown_available() -> bool`
   - `is_markitdown_ocr_plugin_available() -> bool`
   - `is_network_available(timeout=2) -> bool`（小工具：HEAD mineru.net 判断是否能走在线 SDK）
   - `get_available_capability() -> dict`（返回当前环境能力矩阵，供上层决策）
3. **异常基类**：`src/parsers/base_parser.py` 定义 `DocumentParseError(Exception)`（带 `original_error` 属性与 `retryable: bool` 标记）

### Step 4: 解析器基类 + mineru-open-sdk 封装 + markitdown 封装
1. **基类** `src/parsers/base_parser.py`：
   - `BaseParser(Protocol)`：抽象 `parse(file_path, **options) -> ParsedDocument`
   - 辅助方法：`_safe_file_size_check(path, max_mb)`、`_file_meta(path)`（大小/mtime）
2. **★ mineru-open-sdk 封装** `src/parsers/mineru_sdk_parser.py`：
   - 职责：统一封装 MinerU 两种模式，把 `ExtractResult`（markdown / content_list / images / state / progress）映射到 `ParsedDocument`。
   - 配置：
     - `client = MinerU(token=...)`，token 来源优先级：用户传入 → `MINERU_TOKEN` 环境变量 → None（flash-only 模式）。
     - Flash vs Precision 决策：若有 token 且文件 > 10MB 或页数 > 20（通过 pypdf/stat 预估）→ 走 `extract()`；否则走 `flash_extract()`（无需 token 也能用）。
   - 参数开关（与官方 SDK 对齐）：`ocr`、`formula`、`table`、`language`、`pages`、`timeout`。
   - `content_list` 中有表格块时抽出到 `tables`；`markdown` 填入 `result.markdown`。
   - 网络异常 / HTTP 错误 → 抛 `DocumentParseError(..., retryable=True)` 以便上层快速降级。
3. **markitdown 封装** `src/parsers/markitdown_wrapper.py`：
   - 职责：`MarkItDown(enable_plugins=..., llm_client=..., llm_model=...).convert_local(path)` → `ParsedDocument`。
   - `markdown = result.text_content`；`text` 同值；Markdown 表格语法（`|...|`）反向解析到 `tables`（基础版正则）。

### Step 5: 实现各格式解析器（含降级链）

#### 5.1 纯文本解析器 `text_parser.py`
- 支持 `.txt`, `.md`, `.csv`, `.json`, `.xml`
- 编码尝试顺序：`chardet.detect` 推荐 → `utf-8-sig` → `utf-8` → `gbk` → `latin-1`
- CSV：`pandas.read_csv()` → `tables`；`text` 放 head(100) 预览；`.md` 额外写入 `markdown`
- JSON/XML：美化缩进后写入 `text`

#### 5.2 图片解析器 `image_parser.py`（mineru-sdk → markitdown-ocr）
- **L1 — mineru-open-sdk**：在线可用且体积适合时调用，`is_ocr`/`ocr` 按需开启。
- **L2 — markitdown + markitdown-ocr**：离线/失败时降级；若用户提供 llm_client 则 OCR 质量更好。
- 支持多页 TIFF：每一页一项 `pages`。
- 两者皆空文本时抛 `DocumentParseError("图片未识别到文字，可尝试 enable_markitdown_ocr 并提供 LLM client")`。

#### 5.3 Office 解析器 `office_parser.py`（mineru-sdk → markitdown 两级降级）
简化为两级（markitdown[all] 已自动拉取 Docx/PPTx/XLSX/XLS 底层解析依赖，无需重复包装）：
- **Level 1 — mineru-open-sdk**：在线可用 + 大小/页数适配时优先调用；Precision 可处理旧 .doc/.xls。
- **Level 2 — markitdown[all]**：SDK 失败/离线时直接调用 markitdown 统一兜底；Markdown 表格（`|col1|col2|`）用正则反向解析写入 `tables`；大 Excel 同步用 pandas 取 head() 写入 text 预览。
- 失败提示：旧版 .doc / .xls 仍不支持时，给出 LibreOffice `soffice --convert-to docx/xlsx` 转换命令。

#### 5.4 PDF 解析器 `pdf_parser.py`（核心，三级降级 + 空白文本 OCR 重试策略）
- **Level 1 — mineru-open-sdk**（首选，在线）：
  - 若 `is_network_available() & is_mineru_sdk_available()`：按页数/大小选 flash 或 precision。
  - 有 MINERU_TOKEN → precision 默认开 `ocr=True` 以识别扫描件；无 token → flash 可传 `is_ocr=True` 尝试。
  - Markdown 填 `markdown`；content_list 的 table blocks → `tables`。
- **Level 2 — markitdown[all]**（本地回退，用户指定）
  - 若 L1 抛 retryable 网络异常或解析过空（len(text) < 50）立即进入。
  - 文本过空且 markitdown-ocr 可用时自动开插件重试一次。
- **Level 3 — pypdf**（最轻量本地兜底）
  - 加密 PDF：尝试 `PdfReader.decrypt(password)`；解密失败报错提示用户传 password。
  - 逐页 `extract_text()` 填 `pages` + `text`。
- **全局兜底策略**：若三级跑完 `len(text) < 50`，则走一次"强制 OCR 分支"：若 markitdown-ocr 已可用 → 插件重跑；否则在 `DocumentParseError` 中明确提示「该 PDF 疑似扫描件，建议配置 MINERU_TOKEN 走 mineru-sdk OCR 或启用 markitdown-ocr + LLM client」。
- 每级失败写 WARNING；`parser_used` 串记录链路。

### Step 6: 实现门面 `DocumentReader`
`src/document_reader.py`：
```python
class DocumentReader:
    def __init__(
        self,
        # --- mineru-open-sdk 相关 ---
        prefer_mineru_sdk: bool = True,          # 是否在线优先
        mineru_token: str | None = None,         # 不传则读 MINERU_TOKEN 环境变量；None→flash-only
        mineru_ocr: bool | None = None,          # None = 跟随 SDK 默认；True/False 强制开关
        mineru_language: str = "ch",             # SDK language 参数
        mineru_model: str | None = None,         # vlm | pipeline | html
        # --- markitdown-ocr 相关 ---
        enable_markitdown_ocr: bool = False,     # 是否启用 markitdown-ocr 插件
        llm_client: object | None = None,        # OpenAI-compatible client（markitdown-ocr 用）
        llm_model: str | None = None,
        # --- 通用 ---
        max_file_size_mb: int = 500,
    ): ...

    def read(self, file_path, file_type_hint=None, password=None, *,
             pages: str | None = None,            # 透传给 mineru-sdk（如 "1-20"）
             **extra) -> ParsedDocument: ...

    def read_batch(self, file_paths, fail_fast=False
                   ) -> list[ParsedDocument | DocumentParseError]: ...
```
- 在 `src/__init__.py` 中导出 `DocumentReader`、`ParsedDocument`、`DocumentParseError`

### Step 7: 编写 SKILL.md
按 TRAE Skill 规范：
- **frontmatter**：
  - `name: document-reader`
  - `description: >-` 读取多格式文件（PDF/Office/图片/纯文本/HTML）→ Markdown + 文本 + 表格；PDF 优先 mineru-open-sdk（在线、免费、OCR 开关），降级 markitdown 与 pypdf；适配 RAG 与 LLM 流水线。
- **正文**：
  - 何时触发本 Skill（用户上传文档、要求读取/摘要/问答前置、RAG 构建、批量表格提取）。
  - 典型工作流：
    - 最小示例（Flash，无 Token）：
      ```python
      from document_reader import DocumentReader
      r = DocumentReader()  # 自动进入 flash 模式（≤10MB/≤20页免登录可用）
      doc = r.read("report.pdf")
      print(doc.markdown, doc.tables)
      ```
    - 精准/大文件（配免费 Token）：
      ```bash
      export MINERU_TOKEN="xxxxxxxxxxx"  # 从 https://mineru.net/apiManage/token 免费获取
      ```
      或构造参数 `DocumentReader(mineru_token="...", mineru_ocr=True)`。
  - 返回字段速查表（ParsedDocument 各字段含义 + 小示例）。
  - 环境分层安装：核心最小 → 本地增强 → OCR 兜底插件。
  - 故障排查：
    - 无网络 / SDK 服务不可达 → 自动降级 markitdown（本地）；完全离线时仅能处理本地文本/Office 基础解析。
    - 扫描件 PDF 空白文本 → 开 `mineru_ocr=True`（需 Token 或 flash 允许 OCR）或启用 `markitdown-ocr`。
    - 旧版 .doc / .xls 失败 → markitdown 仍不支持时提示 LibreOffice `soffice --convert-to docx a.doc`。
    - 加密 PDF → 传 `password=...`。
    - Windows 中文路径 → 全程 `pathlib.Path`。
  - 许可：核心 mineru-open-sdk（Apache-2.0）、markitdown（MIT）、pypdf（BSD-3）均宽松可商用；mineru-open-sdk 调用的是 MinerU 官方**免费在线服务**，使用需遵循其服务条款。

### Step 8: 编写测试
`tests/test_document_reader.py`：
- **单元测试（离线、无重型依赖）**：
  - 文件类型检测（已知扩展名 → 正确 label）
  - 依赖检测（mock import 场景验证 True/False）
  - 数据模型 `to_dict()` / `to_json()` 往返
  - 纯文本解析（现场写 .txt/.csv/.json/.md 小文件，验证 text/tables/markdown）
  - 门面分发：构造一个 mock 解析器链，验证 text/pdf/image 分派到正确解析器。
- **集成测试（按需 @pytest.mark.skipif 跳过）**：
  - `@pytest.mark.skipif(not is_network_available(), ...)`：mineru-open-sdk flash_extract 对示例 URL / 小型本地文件跑一次。
  - `@pytest.mark.skipif(not is_markitdown_available(), ...)`：现场生成极小 Word/Excel/PPT（利用 markitdown[all] 传递安装的 python-docx/pandas/python-pptx 生成 fixture，不单独声明依赖）→ 验证 text/tables/pages 非空。
  - `@pytest.mark.skipif(not mineru_token_present(), ...)`：precision 模式（需 MINERU_TOKEN env）跑一次稍大文件。
- **降级链验证**：monkeypatch 让 `is_network_available -> False` 或 mock mineru SDK 抛 NetworkError，验证 PDF/Office 仍能由 markitdown/pypdf 解析成功，且 `parser_used` 不包含在线分支标签。

---

## Dependencies and Considerations

| 依赖 | 用途 | 层级 | 注意事项 |
|------|------|------|----------|
| **`mineru-open-sdk`** | **Level 1 在线解析（PDF/Office/图片→Markdown）**，内建 OCR/表格/公式开关 | **核心必装** | Apache-2.0；极轻量 pip 包；需联网；两种模式（Flash 免 Token，Precision 需免费 Token 从 mineru.net 获取） |
| `markitdown[all]` | Level 2 本地通用 Markdown 提取（PDF/Office/图片/文本/HTML/音频/ZIP/ePub…15+ 种格式）；**独立图片 OCR 只需传 llm_client 即可（不需要 markitdown-ocr）** | 核心必装 | MIT；微软 AutoGen 团队维护；图片文字识别/描述需要额外传 `llm_client` + `llm_model`（用 OpenAI-compatible LLM Vision）；不传时图片仅取 EXIF/元数据 |
| `markitdown-ocr` + 可选 LLM client | **仅用于扫描件 PDF / DOCX / PPTX / XLSX 内嵌图片 OCR**（markitdown[all] 原生不会把这些文档内嵌的图片发给 LLM） | **可选，独立第三方插件**（不包含在 markitdown[all] 内） | 独立 PyPI 包名 `markitdown-ocr`；需 OpenAI-compatible API Key；需同时 `enable_plugins=True` + 传 llm_client 才生效；**默认 `enable_markitdown_ocr=False` opt-in** 避免意外费用；独立图片 OCR 请不要装它（直接用 markitdown[all] + llm_client 即可） |
| `python-magic` | 不依赖扩展名的真实文件类型校验 | 可选本地增强 | Windows 需 libmagic DLL；Linux `apt install libmagic1` |
| `pypdf` | PDF 最轻量本地文本兜底；加密 PDF 解密 | 核心必装 | 支持 decrypt(password)；不支持表格/扫描件 |
| `pandas` | CSV/Excel → 结构化表格；预览文本生成 | 核心必装 | 大 Excel 可用 `chunksize` |
| `Pillow` | 图片元信息 / 体积检查 | 核心必装 | |
| `chardet` | 文本编码自动检测 | 核心必装 | 可选替代品 `charset-normalizer` |

### 兼容性与许可
- 核心依赖：`mineru-open-sdk` **Apache-2.0**、`markitdown` **MIT**、`pypdf` **BSD-3**、`pandas`/`Pillow`/`chardet` 均宽松可商用。
- 可选本地增强库：MIT/BSD。
- 注意：mineru-open-sdk 本身是客户端；解析服务由 [mineru.net](https://mineru.net) 官方免费提供，需联网。大文件或高频调用可考虑用户自建 MinerU 后端（通过 `base_url` 参数切换）。

---

## Validation

1. **依赖可安装**：
   - `pip install -r requirements.txt`（核心层）成功，版本无冲突。markitdown[all] 会传递安装 Office 解析所需的 python-docx/openpyxl/python-pptx/xlrd，无需单独声明。
2. **单测通过**：
   - `pytest tests/ -v` → 至少所有离线单元测试全绿；在线用例未配置网络/Token 时自动 skip 不红。
3. **功能冒烟（可脚本化）**：
   - **核心 markitdown（离线）**：用 pandas 出 .xlsx（2 sheet），通过 markitdown[all] 传递安装的 python-docx 写 1 段+1 表格、python-pptx 写 2 slide（作 fixture，不当作直接依赖）→ 对应 `tables/pages/text` 非空。
   - **mineru-sdk flash（在线，无 Token）**：找一个 ≤10MB 公开 PDF 或 1 张小图 → `doc.markdown` 非空；`parser_used` 含 `"mineru-open-sdk"`。
   - **PDF 降级链**：monkeypatch 禁用网络 → 解析同一 PDF 仍成功（markitdown 或 pypdf 任一分支）。
   - **CSV/JSON/MD**：现场写文件 → 对应字段正确（tables 非空、markdown 非空等）。
4. **参数透传**：传 `pages="1-2"` 给 read()，在 mineru-sdk mock 中断言参数被正确传递；传 `password` 给加密 PDF 示例在 pypdf 分支能解密。
5. **异常友好**：
   - 路径不存在 → `DocumentParseError("File not found: ...")`。
   - 非预期扩展名 + 乱码内容 → 错误信息含"无法识别/解析"与下一步建议（"可用 file_type_hint 指定类型"）。
6. **Windows 中文路径冒烟**（本地环境可做手动验证）：中文+空格路径下放 .csv/.docx → 读取成功，不报 OSError。

---

## Risks

| 风险 | 影响 | 处理 / 降级 |
|------|------|-------------|
| **mineru-open-sdk 服务不可达 / 限流 / 超时**（在线依赖） | Level 1 在线解析不可用，用户感知延迟 | 门面层 catch `retryable=True` 错误后**立即降级**本地 markitdown / pypdf；日志 WARNING 记录原因；`parser_used` 链带失败标签便于追踪 |
| **Flash 体积/页数限制**（≤10MB/≤20页） | 大文件 flash 被拒 | 自动判断：有 MINERU_TOKEN → precision（≤200MB/≤200页）；无 Token → 提前降级本地 markitdown（避免一次失败重试）|
| **扫描件 PDF 文本空白** | 未开 OCR 时 `len(text)` 极少 | PDF 解析器三级跑完后仍 < 50 字 → 自动重跑"开 OCR 分支"：优先 SDK ocr=True（需要 Token/Flash 支持），其次 markitdown-ocr（若已配置 llm_client）；最终报错明确提示解决方案 |
| **内网 / 完全离线环境** | mineru-sdk 全不可用 | 自动走纯本地链（markitdown + pypdf 兜底，纯文本/CSV/JSON 无需额外依赖）；SKILL.md 开头大字号标注「离线能力矩阵」说明哪些格式可在离线处理（文本/现代 Office/原生 PDF），哪些不行（扫描件/旧 .doc）|
| **mineru-open-sdk Token 过期 / 无效** | precision 模式报错 | 捕获对应错误 → 在 WARNING 里提示「请到 mineru.net/apiManage/token 刷新」，并自动 fallback 到 flash（如果文件大小适配）或降级本地解析 |
| **旧版 .doc 二进制格式** | 部分解析器不支持 | mineru-sdk precision 支持 .doc → 优先；markitdown 分支尝试；仍失败提示 LibreOffice 转码命令 |
| **加密 PDF / 受保护 Office** | 直接报错 | 预留 `password` 参数；pypdf 分支先尝试解密；失败时报错「请传入 password= 或使用原软件解锁后重试」|
| **大文件内存风险**（>500MB PDF / 百万行 Excel） | OOM / 卡顿 | `max_file_size_mb` 硬限制（默认 500）超阈值直接拒绝；Excel 用 pandas chunksize；PDF 分页增量解析 |
| **Windows 中文/空格路径** | C 扩展库打开失败 | 全程 `pathlib.Path(str)`；传路径前 `str(Path.resolve())`；CSV 显式 `encoding="utf-8-sig"` |
| **markitdown-ocr 成本 / 限流**（LLM Vision 调用）| 用户意外产生高费用 | **默认 `enable_markitdown_ocr=False` opt-in**；仅在用户显式开启且文本过空时才调用；文档显式提醒费用 |
| **Python 版本 < 3.10** | mineru-open-sdk / markitdown 不兼容 | 启动时 `sys.version_info` 检查，抛清晰错误「需要 Python 3.10+」|
