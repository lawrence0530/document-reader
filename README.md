# Document Reader（Agent Skill）

## 介绍
一个通用 Agent Skill，用来读取本地文档文件并输出结构化内容（纯文本 / 分页 / 表格 / Markdown / 元信息）。
解析完之后可以直接喂给大模型做总结、翻译、问答、RAG 分块、表格提取等下游任务。

### 支持的文件格式
| 分类 | 支持格式（扩展名）|
|------|---------------------|
| **PDF** | .pdf（含扫描件 OCR 识字）|
| **Office** | Word：.doc / .docx ； Excel：.xls / .xlsx ； PPT：.ppt / .pptx |
| **图片** | .jpg / .jpeg / .png / .bmp / .gif / .webp / .tiff / .tif（含文字识别 OCR）|
| **文本 / 结构化** | .txt / .csv / .json / .xml / .md / .html |
| **其它** | .epub / .zip（自动展开）|

---

## 安装
一条命令安装，零配置，缺啥自动补，装好后永久不用再装：

```bash
npx add document-reader
```


---

## 配置
可选，不配也能用（大文件和扫描件建议配）。

| 配置项 | 说明 |
|--------|------|
| `MINERU_TOKEN` | 免费 Token，在 https://mineru.net/apiManage/token 领取，粘贴给 Agent：「我的 MINERU_TOKEN 是 xxx」 |
| `LLM_API_KEY` | 仅纯内网离线扫描件识字需要，粘贴给 Agent：「我的 LLM_API_KEY 是 xxx」 |

---

## 使用
直接用自然语言对 Agent 说你要做什么，5 个常用示例：
1. 读一下 `合同.pdf`，总结 3 条重点
2. 这是扫描件，帮我开 OCR 识别 `scan.pdf` 里的字
3. 只读 `报告.pdf` 的第 1 到第 20 页
4. `机密.pdf` 加密了，密码是 my-secret，读取内容
5. 不要走云端，纯离线帮我读 `客户资料.xlsx` 并抽表格

---


