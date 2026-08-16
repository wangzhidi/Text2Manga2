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
- **视频导出**：将漫画图片合成竖屏 MP4，支持多种转场动画与背景音乐

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

> 如需使用图像选择 GUI（`choose.py` / `bilibili_convert.py`），还需安装 PyQt5。
> `torch` 请根据自己的 CUDA 版本从 [pytorch.org](https://pytorch.org) 单独安装。

### 2. 配置环境变量

在项目根目录创建 `.env` 文件：

```env
# LLM（兼容 OpenAI API 的端点，如 Claude）
GEMINI_API_URL=https://your-api-endpoint/v1
LLM_API_KEY=["sk-xxxx"]

# NovelAI 图像生成
NA_API_KEY=["pst-xxxx"]

# 风格提示词（追加到每帧正/负向提示）
STYLE_POS=,[[[artist:kedama_milk]]],{{{saintshiro}}},[kazutake_hazano],artist:ciloranko,artist:suyamori, {artist:iumu ::,,0.65:: artist:icecake ::,{reoen ::,,1.4::artsit:chen_bin::,[shuri_\(84k\)],{{rin_yuu}},artist:mignon,[[[ningen mame]]],year 2024
STYLE_NEG=blurry, lowres, error, film grain, scan artifacts, worst quality, bad quality, jpeg artifacts, very displeasing, chromatic aberration, logo, dated, signature, multiple views, gigantic breasts, blurry, lowres, error, film grain, scan artifacts, worst quality, bad quality, jpeg artifacts, very displeasing, chromatic aberration, logo, dated, signature, multiple views, 2::artist_collaboration::

```

### 3. 启动 Web 应用

```bash
python web_app.py
```

浏览器访问 `http://localhost:8000`，注册账号后即可使用。

---

## 单次生成流程（命令行）

单次生成指对某本书的一个或多个章节执行完整的"分镜 → 生图 → 加对话框"流程。

### 准备输入文件

将章节原文以 `.txt` 格式放入 `books/<book_id>/` 目录，文件名即为章节 ID（可中文命名）：

```
books/
└── 7若希LR/
    ├── 星野想要和你.txt
    └── 一个个都惦记.txt
```

### 执行生成

```bash
# 完整流程：分镜 → 生图 → 加对话
python main.py <book_id>

# 指定单个章节
python main.py <book_id> --chapter <chapter_id>

# 强制重新生成（删除旧数据后重跑）
python main.py <book_id> --chapter <chapter_id> --force

# 跳过已完成的步骤（用于续跑）
python main.py <book_id> --skip-storyboard   # 跳过 LLM 分镜
python main.py <book_id> --skip-images       # 跳过 NovelAI 生图
python main.py <book_id> --skip-dialog       # 跳过对话框渲染
```

### 流程说明

```
books/<id>/<chapter>.txt
        │
        ▼ board.py（LLM 分镜）
script/<id>/<chapter>.json
        │
        ▼ image.py（NovelAI 生图，多线程并发）
image/<id>/<chapter>/*.png
        │
        ▼ dialog.py（人脸检测 + 角色识别 + 气泡渲染）
with_text/<id>/<chapter>/       ← 带对话框的漫画图
with_text/<id>/<chapter>_final/ ← 经过人工选图后的最终版本
```

生成完成后，可使用 `choose.py` 启动 PyQt5 图像选图 GUI，对每帧左右两张候选图进行人工筛选，并支持局部马赛克处理。

---

## 视频导出

### manga_to_video.py — 漫画转竖屏视频

将一个漫画图片文件夹（已排好序）合成为竖屏 MP4（832×1216），适合上传竖屏平台。

**主要特性：**
- 支持 `fade`、`slide`、`wipe`、`zoom_in` 等多种转场动画，可全局统一或每页随机
- 从 `data.py` 的 `bgm_map` 中按标签自动随机选取背景音乐
- 可在画面左侧叠加翻页倒计时圆环
- 通过 ffmpeg（NVENC / libx264）编码，画质优先

**使用方法：**

编辑 `manga_to_video.py` 顶部的配置区：

```python
FOLDER = r"final\原图一期\某章节\漫画版"  # 图片文件夹
TAGS   = ["原神"]                          # BGM 标签筛选，[] 则全库随机
DURATION = 2.0                             # 每页停留时长（秒）
TRANSITION = "fade"                        # 转场方式，"random" 随机选
```

```bash
python manga_to_video.py
```

输出文件为 `<图片文件夹>/<文件夹名>.mp4`。

---

### bilibili_convert.py — 一站式 B 站投稿工具

基于 PyQt5 的 GUI 工具，整合了横屏封面制作、BGM 选择预览与漫画视频合成，专为 B 站投稿场景设计。

**主要功能：**
- 自动从 `无字幕原图/` 目录加载图片，网格展示供点击选取横屏封面
- 读取章节 JSON 元信息（标题、原文作者、平台）并自动生成横屏封面（`make_cover.py`）
- 按情绪标签推荐 BGM 候选列表，支持试听、换一批、手动编辑标签
- 一键触发 `manga_to_video.py` 生成漫画视频，实时显示进度日志
- 完成后自动将项目复制到 `final/待上传/` 目录，便于批量上传

**使用方法：**

```bash
python bilibili_convert.py
```

启动后选择章节根目录（包含 `无字幕原图/` 子文件夹的项目文件夹），或直接修改脚本顶部的 `INPUT_FOLDER` 变量。

---

## 目录结构

```
Text2Manga2/
├── web_app.py            # FastAPI Web 服务（主入口）
├── main.py               # CLI 批量处理入口
├── board.py              # 分镜生成（LLM）
├── image.py              # 图像生成（NovelAI）
├── dialog.py             # 对话气泡处理
├── choose.py             # 人工选图 GUI（PyQt5）
├── manga_to_video.py     # 漫画转竖屏视频
├── bilibili_convert.py   # B 站投稿一站式 GUI
├── make_cover.py         # 封面生成
├── organize.py           # 成品整理到 final/ 目录
├── rename.py             # 书目文件批量重命名
├── concate.py            # 图像拼接
├── data.py               # 角色数据库 & BGM 列表
├── key_pool.py           # API 密钥池
├── danbooru_search.py    # Danbooru 角色搜索
├── speech_bubble.py      # 气泡绘制工具
├── books/                # 输入小说文本 books/{id}/{chapter}.txt
├── script/               # 生成的分镜 JSON
├── image/                # 原始生成图像
├── with_text/            # 添加对话后的图像
├── final/                # 整理后的成品（含待上传）
├── bgm/                  # 背景音乐库
├── models/               # 本地模型文件（YOLO / ESRGAN）
├── reference/            # 角色参考图
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
| GUI | PyQt5 |
| 视频合成 | ffmpeg（NVENC / libx264）|

## 注意事项

- NovelAI API 需要有效的付费账号
- 生图并发数默认为 4 线程，可在 `image.py` 中调整
- 生产部署时务必替换 `JWT_SECRET` 为强随机密钥

---

## 生产部署（HTTPS 推荐方案）

> ⚠️ **本节文档尚未完成，仅供参考。**

采用 **Caddy + Uvicorn**：

- Uvicorn 仅监听内网地址 `127.0.0.1:8000`
- Caddy 对外监听 `80/443`，自动申请/续期 Let's Encrypt 证书
- 浏览器统一通过 `https://你的域名` 访问，可消除"不安全连接"提示

### 准备域名与端口

- 域名 `A/AAAA` 记录指向服务器公网 IP
- 安全组/防火墙放行 `80`、`443`

### 启动后端（Uvicorn）

Windows PowerShell：

```powershell
./deploy/start_uvicorn_prod.ps1
```

等价命令：

```bash
uvicorn web_app:app --host 127.0.0.1 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips="*"
```

### 配置并启动 Caddy

1. 编辑 [deploy/Caddyfile](deploy/Caddyfile)，把 `example.com` 改成你的真实域名。
2. 启动 Caddy（使用该配置文件）。

> Caddy 会自动把 HTTP 跳转到 HTTPS，并自动管理证书续期。

---

## 语音生成（TTS）

> ⚠️ **本节文档尚未完成，功能仍在开发中。**

相关脚本：`tts_narrator.py`、`tts_qwen.py`，支持对白朗读与旁白配音，后续将集成到视频合成流程中。