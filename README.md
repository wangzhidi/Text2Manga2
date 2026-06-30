# Text2Manga

将小说文本自动转化为漫画页面的 AI 生成系统。

## 功能特点

- **故事分镜**：调用 Claude 等 LLM 将故事文本拆解为可视化场景
- **图像生成**：通过 NovelAI API 生成动漫风格插图
- **角色一致性**：基于 Danbooru 标签 + 参考图像保持角色外观统一
- **对话气泡**：YOLOv8 人脸检测 + CLIP 角色识别，自动添加对话框
- **多格式导出**：单张 PNG / 长条拼图 / ZIP 打包下载
- **Web 界面**：带用户系统、会话管理、实时进度推送（SSE）的 FastAPI 应用
- **API 密钥池**：多密钥负载均衡，并发生成

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

> 如需使用图像选择 GUI（`choose.py`），还需安装 PyQt5。  
> `torch` 请根据自己的 CUDA 版本从 [pytorch.org](https://pytorch.org) 单独安装。

### 2. 配置环境变量

在项目根目录创建 `.env` 文件：

```env
# LLM（兼容 OpenAI API 的端点，如 Claude）
GEMINI_API_URL=https://your-api-endpoint/v1
LLM_API_KEY=["key1", "key2"]
LLM_MODEL=claude-opus-4-6

# NovelAI 图像生成
NA_API_KEY=["novelai-key1"]
NOVELAI_MODEL=nai-diffusion-4-curated-preview
NOVELAI_STEPS=28
NOVELAI_SCALE=5.0
NOVELAI_SAMPLER=k_euler_ancestral

# 风格提示词（追加到每帧正/负向提示）
STYLE_POS=masterpiece, best quality
STYLE_NEG=lowres, bad anatomy

# Web 应用密钥（请替换为随机强密钥）
JWT_SECRET=change-me-in-production
```

### 3. 启动 Web 应用

```bash
python web_app.py
```

浏览器访问 `http://localhost:8000`，注册账号后即可使用。

## 生产部署（HTTPS 推荐方案）

采用 **Caddy + Uvicorn**：

- Uvicorn 仅监听内网地址 `127.0.0.1:8000`
- Caddy 对外监听 `80/443`，自动申请/续期 Let's Encrypt 证书
- 浏览器统一通过 `https://你的域名` 访问，可消除“不安全连接”提示

### 1. 准备域名与端口

- 域名 `A/AAAA` 记录指向服务器公网 IP
- 安全组/防火墙放行 `80`、`443`

### 2. 启动后端（Uvicorn）

Windows PowerShell：

```powershell
./deploy/start_uvicorn_prod.ps1
```

等价命令：

```bash
uvicorn web_app:app --host 127.0.0.1 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips="*"
```

### 3. 配置并启动 Caddy

1. 编辑 [deploy/Caddyfile](deploy/Caddyfile)，把 `example.com` 改成你的真实域名。
2. 启动 Caddy（使用该配置文件）。

> Caddy 会自动把 HTTP 跳转到 HTTPS，并自动管理证书续期。

### 4. 验证

- 在浏览器打开 `https://你的域名`
- 下载 ZIP 时不再出现“连接不安全”类提示（若仍有文件安全扫描提示，属于浏览器下载策略）

### 4. 命令行批量模式

```bash
# 完整流程：分镜 → 生图 → 加对话
python main.py <book_id>

# 指定章节
python main.py <book_id> --chapter <chapter_id>

# 跳过已完成步骤
python main.py <book_id> --skip-storyboard
python main.py <book_id> --skip-images
python main.py <book_id> --skip-dialog

# 强制重新生成（删除旧数据）
python main.py <book_id> --force
```

## 目录结构

```
Text2Manga2/
├── web_app.py            # FastAPI Web 服务（主入口）
├── main.py               # CLI 批量处理入口
├── board.py              # 分镜生成（LLM）
├── image.py              # 图像生成（NovelAI）
├── dialog.py             # 对话气泡处理
├── data.py               # 角色数据库
├── key_pool.py           # API 密钥池
├── danbooru_search.py    # Danbooru 角色搜索
├── speech_bubble.py      # 气泡绘制工具
├── concate.py            # 图像拼接
├── make_cover.py         # 封面生成
├── books/                # 输入小说文本 books/{id}/{chapter}.txt
├── script/               # 生成的分镜 JSON
├── image/                # 原始生成图像
├── with_text/            # 添加对话后的图像
├── users/                # 用户数据与参考图
├── sessions/             # 会话元数据
└── static/               # Web 前端静态资源
```

## 技术栈

| 层次 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| LLM | Claude / OpenAI 兼容 API |
| 图像生成 | NovelAI API |
| 图像处理 | Pillow, OpenCV, PyCairo |
| 人脸检测 | YOLOv8 (Ultralytics) |
| 角色识别 | CLIP (HuggingFace Transformers) |
| 认证 | JWT (python-jose) + bcrypt |

## 注意事项

- NovelAI API 需要有效的付费账号
- 生图并发数默认为 4 线程，可在 `image.py` 中调整
- 生产部署时务必替换 `JWT_SECRET` 为强随机密钥
