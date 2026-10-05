# 英语写作语料库（english-writing-corpus）

面向高中英语**应用文**与**读后续写**素材积累的本地语料管理工具。

- 两个完全独立的分区：`practical`（应用文，绿色主题）、`continuous`（读后续写，蓝色主题）
- 数据以 JSONL 存在本地，每条记录只有 `id` / `content` / `translation` / `tags`
- 本地跑 FastAPI 后端，可用前端页面增删改查、分页、搜索、标签筛选
- 用构建脚本导出静态 JSON，前端可部署到 Vercel 等静态托管平台（只读浏览）

技术栈：Python + FastAPI + JSONL 存储；前端纯 HTML/CSS/原生 JS，无框架、无构建工具。

---

## 一、目录结构

```text
english-writing-corpus/
├── data/                          # 本地数据源，不部署
│   ├── practical/
│   │   ├── sentences.jsonl
│   │   ├── next_id.txt
│   │   └── backup/
│   └── continuous/
│       ├── sentences.jsonl
│       ├── next_id.txt
│       └── backup/
├── raw/                           # 原始语料，供导入脚本使用
│   ├── practical/
│   └── continuous/
├── public/                        # 部署目录
│   ├── index.html
│   ├── style.css
│   ├── app.js
│   ├── config.js                  # 运行时配置（本地/静态模式）
│   └── api/
│       ├── practical/{sentences.json,tags.json}
│       └── continuous/{sentences.json,tags.json}
├── main.py                        # 本地 FastAPI 后端
├── import_raw.py                  # 原始语料导入脚本
├── build_static.py                # 构建静态 JSON
├── vercel.json
└── README.md
```

---

## 二、快速开始

### 1. 安装依赖

```bash
pip install fastapi "uvicorn[standard]"
```

> 前端为纯静态文件，无需 `npm install`。

### 2. 启动本地后端 + 页面

```bash
python main.py
# 或：uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

- 接口文档：<http://127.0.0.1:8000/docs>
- 前端页面：直接双击打开 `public/index.html`（`config.js` 默认 `mode: "local"`）

### 3. 导入原始语料

把 `.txt` / `.md` / `.csv` 放进 `raw/practical/` 或 `raw/continuous/`，然后：

```bash
python import_raw.py                 # 两个分区都导入
python import_raw.py practical       # 只导入应用文分区
python import_raw.py --dry-run       # 只统计，不写入
```

### 4. 构建静态数据并部署

```bash
python build_static.py --write-config   # 生成 public/api/** 并把 config.js 切到静态模式
```

把 `public/` 目录部署到 Vercel（`vercel.json` 已设置 `outputDirectory: "public"`）。
本地开发时用 `python build_static.py --local-config` 切回 `mode: "local"`。

---

## 三、数据格式

### `data/{module}/sentences.jsonl`

每行一个 JSON 对象，UTF-8，不换行、不缩进。

```json
{"id":1,"content":"My heart hammered against my ribs.","translation":"我的心怦怦直跳。","tags":["紧张","动作描写"]}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | int | 是 | 唯一、自增、从 1 开始，分区内独立 |
| `content` | string | 是 | 英文原文 |
| `translation` | string \| null | 否 | 中文译文 |
| `tags` | string[] | 是 | 标签，可为空数组 |

### `data/{module}/next_id.txt`

单行整数，表示下一个可用 ID，仅在本地新增时使用。

### 写入规则

| 操作 | 方式 |
|---|---|
| 新增 | 以 `"a"` 模式追加一行，不重写整个文件 |
| 修改 / 删除 | 读全部 → 改内存 → 写临时文件 → `os.replace` 原子替换 |
| 并发 | 每个分区一把 `threading.Lock` |
| 备份 | 修改/删除前复制到 `backup/sentences_YYYYMMDD_HHMMSS.jsonl`，最多保留 20 份 |

---

## 四、后端 API

基础路径 `http://127.0.0.1:8000`，`{module}` ∈ `practical` / `continuous`，已开启 CORS。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/{module}/sentences` | 分页列表，参数 `page`、`page_size`(≤100)、`q`、`scope`(`all`/`content`/`translation`)、`tag`、`sort`(`id_desc`/`id_asc`) |
| GET | `/api/{module}/sentences/{id}` | 获取单条 |
| POST | `/api/{module}/sentences` | 新增，返回新建对象 |
| PUT | `/api/{module}/sentences/{id}` | 修改，保留原 `id` |
| DELETE | `/api/{module}/sentences/{id}` | 删除，返回 `{"ok": true}` |
| GET | `/api/{module}/tags` | 标签列表（按出现次数降序） |
| POST | `/api/{module}/sentences/batch` | 批量导入，返回 `{"inserted": n, "ids": [...]}` |

列表响应：

```json
{
  "items": [{ "id": 1, "content": "...", "translation": "...", "tags": ["..."] }],
  "total": 137, "page": 1, "page_size": 20, "total_pages": 7
}
```

请求体（POST / PUT / batch 内的元素）：

```json
{ "content": "...", "translation": null, "tags": [] }
```

设置与筛选规则：

- 搜索默认不区分大小写；`scope` 控制只搜原文、只搜译文或两者都搜
- `tag` 为精确匹配（完整标签名）
- `page_size` 超过 100 会被接口拒绝（422）；页码超出范围会返回空 `items`，`total` 仍为真实总数

---

## 五、导入脚本支持格式

| 格式 | 解析规则 |
|---|---|
| `.txt` | 一行一句，忽略空行 |
| `.md` | 按空行切分段落，非空段落作为一条（段内换行合并为一行） |
| `.csv` | 列名映射 `content` / `translation` / `tags`；`tags` 用 `\|` 分隔 |

- 处理顺序：先 `.txt`，再 `.md`，最后 `.csv`
- 去重：与现有 JSONL 的 `content` 完全相同则跳过（文件之间也去重）
- 新句子 `id` 从 `next_id.txt` 继续，导入后更新该文件
- 输出统计：每个文件导入条数、跳过重复条数

---

## 六、前端说明

### 运行模式（`public/config.js`）

```js
// 本地编辑模式
window.APP_CONFIG = { mode: "local",  apiBase: "http://127.0.0.1:8000/api" };

// 静态只读模式
window.APP_CONFIG = { mode: "static", apiBase: "/api" };
```

- `local`：调用本地后端，显示新增 / 编辑 / 删除按钮
- `static`：读取 `public/api/**` 的静态 JSON，隐藏编辑类按钮，搜索、分页、标签筛选全部在浏览器内完成

### 功能点

1. **分区切换**：两个 Tab，切换时重载数据、切换主题色（绿 / 蓝）、重置到第 1 页并清空标签筛选
2. **列表展示**：卡片显示 `content`、`translation`、全部标签与 `#id`
3. **搜索**：输入框 300ms 防抖，范围下拉对应 `scope`
4. **标签筛选**：下拉选项来自当前分区；点击卡片上的标签也能筛选
5. **分页**：上一页 / 下一页 / 页码信息，切换条件时回到第 1 页
6. **新增 / 编辑 / 删除**：仅 `mode === "local"` 时显示，删除前有确认提示
7. **XSS 防护**：所有渲染文本都经过 HTML 转义
8. **错误处理**：请求失败显示红色提示条，3 秒后自动消失；提交时禁用按钮
9. **标签输入**：逗号分隔（中英文逗号、顿号均可），提交前拆分、去空、去重

---

## 七、部署到 Vercel

1. 本地编辑数据（通过页面或直接改 `data/{module}/sentences.jsonl`）
2. `python build_static.py --write-config`
3. 把 `public/` 目录部署到 Vercel（或任何静态托管平台）

站点为只读浏览模式；每次更新语料后需重复以上步骤，本地修改不会自动同步到线上。

---

## 八、本阶段不做

用户登录与权限、SQLite 迁移、AI 自动抽取与打标签、移动端适配、跨分区搜索。

---

## 九、自检脚本

仓库自带两套验证脚本，用于改动后快速回归：

```bash
python tests/api_e2e_test.py     # 后端 78 项：全部接口、分页、scope、标签、增删改、备份、校验、CORS
node   tests/frontend_check.js   # 前端 63 项：静态 JSON 结构、标签统计、转义、分页与筛选逻辑、主题变量
```

- 后端测试在系统临时目录里建数据，**不会改动 `data/` 下的真实语料**（运行前后可对比文件哈希）。
- 前端测试直接读取 `public/api/**` 的静态产物，因此请先执行 `python build_static.py`。
- 两个脚本都会打印通过/失败明细，全部通过时退出码为 0。

