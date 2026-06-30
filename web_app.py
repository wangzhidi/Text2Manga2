"""
Text2Manga Web Application
FastAPI-based web interface for the manga generation workflow
"""
import asyncio
import base64
import datetime
import hashlib
import hmac
import json
import os
import random
import re
import secrets
import shutil
import string
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor
from PIL import Image

from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from pydantic import BaseModel

# ─────────────────────────── App init ───────────────────────────
BASE_PATH = Path(__file__).parent
SESSIONS_PATH = BASE_PATH / "sessions"
SESSIONS_PATH.mkdir(exist_ok=True)
PROFILES_PATH = BASE_PATH / "private_profiles"
PROFILES_PATH.mkdir(exist_ok=True)

app = FastAPI(title="Text2Manga")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth middleware: protect all /api/ routes except /api/auth/* ──
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

class AuthMiddleware(BaseHTTPMiddleware):
    _PUBLIC = {"/api/auth/register", "/api/auth/login", "/api/auth/guest"}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path not in self._PUBLIC:
            # Support token via Authorization header OR ?token= query param (for EventSource)
            auth_header = request.headers.get("Authorization", "")
            token = ""
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
            else:
                token = request.query_params.get("token", "")
            if not token:
                return JSONResponse({"detail": "未登录，请先登录"}, status_code=401)
            username = _decode_token(token)
            if not username:
                return JSONResponse({"detail": "Token 无效或已过期，请重新登录"}, status_code=401)
            request.state.current_user = username
        return await call_next(request)

app.add_middleware(AuthMiddleware)


class CachedStaticFiles(StaticFiles):
    """为静态资源添加浏览器缓存头，降低重复拉取流量。"""

    def __init__(self, *args, cache_control: str = "public, max-age=604800", **kwargs):
        super().__init__(*args, **kwargs)
        self._cache_control = cache_control

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        # 对命中/协商缓存响应都返回 Cache-Control，便于浏览器复用缓存
        if response.status_code in (200, 206, 304):
            response.headers["Cache-Control"] = self._cache_control
        return response

# Serve static files
static_path = BASE_PATH / "static"
static_path.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# Serve generated images
image_path = BASE_PATH / "image"
image_path.mkdir(exist_ok=True)
app.mount("/image", CachedStaticFiles(directory=str(image_path)), name="image")

# Serve with_text images
with_text_path = BASE_PATH / "with_text"
with_text_path.mkdir(exist_ok=True)
app.mount("/with_text", CachedStaticFiles(directory=str(with_text_path)), name="with_text")

# Serve users directory (reference images etc.)
USERS_PATH = BASE_PATH / "users"
USERS_PATH.mkdir(exist_ok=True)
app.mount("/users", StaticFiles(directory=str(USERS_PATH)), name="users")

_executor = ThreadPoolExecutor(max_workers=4)
IMAGE_EXT = "jpg"
_IMAGE_EXT_CANDIDATES = ("jpg", "jpeg", "png")

# session_id -> True：正在生成图片中，防止同一 session 并发重入
_active_image_sessions: Dict[str, bool] = {}

# ─────────────────────────── Auth ────────────────────────────────
JWT_SECRET = os.environ.get("JWT_SECRET", "text2manga-default-secret-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30
_bearer = HTTPBearer(auto_error=False)


def _hash_password(password: str) -> str:
    """使用 PBKDF2-SHA256 生成密码哈希（兼容任意长度/字符集）。"""
    iterations = 390000
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    dk_b64 = base64.b64encode(dk).decode("ascii")
    return f"pbkdf2_sha256${iterations}${salt_b64}${dk_b64}"


def _verify_password(plain: str, hashed: str) -> bool:
    """校验密码。
    - 新格式：pbkdf2_sha256$iterations$salt_b64$hash_b64
    - 旧格式：bcrypt($2a/$2b/$2y)（向后兼容）
    """
    try:
        if hashed.startswith("pbkdf2_sha256$"):
            _, iter_str, salt_b64, hash_b64 = hashed.split("$", 3)
            iterations = int(iter_str)
            salt = base64.b64decode(salt_b64.encode("ascii"))
            expected = base64.b64decode(hash_b64.encode("ascii"))
            actual = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, iterations)
            return hmac.compare_digest(actual, expected)

        if hashed.startswith("$2"):
            # 兼容历史 bcrypt 哈希，避免 passlib 与新 bcrypt 版本不兼容问题
            import bcrypt
            return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

    return False


def _create_token(username: str) -> str:
    expire = datetime.datetime.utcnow() + datetime.timedelta(days=JWT_EXPIRE_DAYS)
    return jwt.encode({"sub": username, "exp": expire}, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_token(token: str) -> Optional[str]:
    """Returns username if valid, else None."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


def _user_profile_file(username: str) -> Path:
    return PROFILES_PATH / f"{username}.json"


def _legacy_user_profile_file(username: str) -> Path:
    return USERS_PATH / username / "profile.json"


def _load_user_profile(username: str) -> Optional[dict]:
    """Load profile from private storage; migrate legacy profile from users/{username}/profile.json if found."""
    profile_file = _user_profile_file(username)
    if profile_file.exists():
        return json.loads(profile_file.read_text(encoding="utf-8"))

    legacy_file = _legacy_user_profile_file(username)
    if legacy_file.exists():
        profile = json.loads(legacy_file.read_text(encoding="utf-8"))
        profile_file.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            legacy_file.unlink(missing_ok=True)
        except Exception:
            pass
        return profile
    return None


def _save_user_profile(username: str, profile: dict):
    profile_file = _user_profile_file(username)
    profile_file.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")


def _session_belongs_to_user(meta: dict, current_user: str) -> bool:
    """Backward-compatible owner check for old sessions and new sessions."""
    user_key = _sanitize(current_user)
    if not user_key:
        return False

    owner_candidates = {
        _sanitize(str(meta.get("owner_username", ""))),
        _sanitize(str(meta.get("username", ""))),
        _sanitize(str(meta.get("book_id", ""))),
    }
    owner_candidates.discard("")
    if user_key in owner_candidates:
        return True

    # Compatibility: old sessions may store display_name in username/book_id.
    try:
        profile = _load_user_profile(current_user) or {}
        display_key = _sanitize(str(profile.get("display_name", "")))
        if display_key and display_key in owner_candidates:
            return True
    except Exception:
        pass

    return False


def _require_session_owner(session_id: str, current_user: str) -> dict:
    """Load session and ensure it belongs to current user, else raise 403."""
    meta = _load_meta(session_id)
    if not _session_belongs_to_user(meta, current_user):
        raise HTTPException(status_code=403, detail="无权限访问该会话")
    return meta


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> str:
    """FastAPI dependency – returns sanitized username from JWT or raises 401."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="未登录，请先登录")
    username = _decode_token(credentials.credentials)
    if not username:
        raise HTTPException(status_code=401, detail="Token 无效或已过期，请重新登录")
    return username


# ─────────────────────────── Models ─────────────────────────────

class SessionCreate(BaseModel):
    title: str
    text: str
    original_author: Optional[str] = ""
    username: str
    notes: Optional[str] = ""
    num_images: int = 2
    style_preset: Optional[str] = "default"
    streaming: bool = False


class StoryboardUpdate(BaseModel):
    data: List[Dict[str, Any]]


class SelectionUpdate(BaseModel):
    # frame_id -> selected image index (0 or 1)
    selections: Dict[str, int]
    # frame_id -> is_cover (bool)
    cover_id: Optional[int] = None
    # list of frame_ids to discard
    discarded: Optional[List[int]] = None


class UserRegister(BaseModel):
    username: str
    password: str


class UserLogin(BaseModel):
    username: str
    password: str


class UserRename(BaseModel):
    new_username: str
    new_password: Optional[str] = None
    current_password: Optional[str] = None  # 游客账户无需填写


class MissingCharacterInferRequest(BaseModel):
    name: str


class RenameCharacterRequest(BaseModel):
    old_name: str
    new_name: str


# ─────────────────────────── Helpers ─────────────────────────────

def _session_dir(session_id: str) -> Path:
    return SESSIONS_PATH / session_id


def _load_meta(session_id: str) -> dict:
    meta_file = _session_dir(session_id) / "meta.json"
    if not meta_file.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    return json.loads(meta_file.read_text(encoding="utf-8"))


def _save_meta(session_id: str, meta: dict):
    meta_file = _session_dir(session_id) / "meta.json"
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _sanitize(name: str) -> str:
    """Remove characters that are invalid in directory/file names."""
    return re.sub(r'[\\/:*?"<>|]', '_', name)[:40]


def _script_path(book_id: str, chapter: str) -> Path:
    return BASE_PATH / "script" / book_id / f"{chapter}.json"


def _source_text_path(book_id: str, chapter: str) -> Path:
    return BASE_PATH / "books" / book_id / f"{chapter}.txt"


def _load_session_source_text(meta: dict) -> Dict[str, str]:
    """Load stored source text for Step-1 form restore.

    Returns:
        {
            "full_text": 原始文件中第三行及之后的全部内容,
            "raw_text": 尝试去掉“（notes）\n”前缀后的正文
        }
    """
    book_id = str(meta.get("book_id", ""))
    chapter = str(meta.get("chapter", ""))
    if not book_id or not chapter:
        return {"full_text": "", "raw_text": ""}

    txt_path = _source_text_path(book_id, chapter)
    if not txt_path.exists():
        return {"full_text": "", "raw_text": ""}

    try:
        lines = txt_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return {"full_text": "", "raw_text": ""}

    full_text = "\n".join(lines[2:]).strip() if len(lines) >= 3 else ""
    raw_text = full_text

    notes = str(meta.get("notes") or "").strip()
    if notes and raw_text:
        prefix = f"（{notes}）"
        if raw_text.startswith(prefix):
            rest = raw_text[len(prefix):]
            if rest.startswith("\r\n"):
                rest = rest[2:]
            elif rest.startswith("\n"):
                rest = rest[1:]
            raw_text = rest

    return {"full_text": full_text, "raw_text": raw_text}


def _load_storyboard(book_id: str, chapter: str) -> list:
    p = _script_path(book_id, chapter)
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def _extract_story_title(storyboard: list, chapter: str) -> str:
    for item in storyboard:
        if isinstance(item, dict) and item.get("id") == -1:
            title = str(item.get("title", "")).strip()
            if title:
                return title
    return chapter


def _collect_related_descriptions(storyboard: list, character_name: str, limit: int = 3) -> List[str]:
    target = character_name.strip().lower().replace("_", " ")
    if not target:
        return []

    matched: List[str] = []
    for frame in storyboard:
        if not isinstance(frame, dict) or frame.get("id") == -1:
            continue
        frame_text = json.dumps(frame, ensure_ascii=False)
        placeholders = [m.strip().lower().replace("_", " ") for m in re.findall(r'<([^>]+)>', frame_text)]
        if target not in placeholders:
            continue
        desc = str(frame.get("description", "")).strip()
        if desc and desc not in matched:
            matched.append(desc)
        if len(matched) >= limit:
            break
    return matched


def _extract_json_from_text(raw_text: str) -> dict:
    text = (raw_text or "").strip()
    m = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL | re.IGNORECASE)
    if m:
        text = m.group(1)
    else:
        m = re.search(r'(\{.*\})', text, re.DOTALL)
        if m:
            text = m.group(1)
    return json.loads(text)


def _normalize_appearance(appearance: str) -> str:
    tokens = [t.strip().lower() for t in (appearance or "").replace("，", ",").split(",") if t.strip()]

    gender_alias = {
        "1girl": "girl",
        "female": "girl",
        "woman": "girl",
        "lady": "girl",
        "1boy": "boy",
        "male": "boy",
        "man": "boy",
        "guy": "boy",
        "girl": "girl",
        "boy": "boy",
    }
    gender = None
    hair = None

    for t in tokens:
        if gender is None and t in gender_alias:
            gender = gender_alias[t]
        if hair is None and "hair" in t:
            hair = t

    if not gender:
        gender = "person"
    if not hair:
        hair = "black hair"
    return f"{gender}, {hair}"


def _save_storyboard(book_id: str, chapter: str, data: list):
    p = _script_path(book_id, chapter)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_with_text_dir(book_id: str, chapter: str) -> Path:
    return BASE_PATH / "with_text" / book_id / chapter


def _get_final_dir(book_id: str, chapter: str) -> Path:
    return BASE_PATH / "with_text" / book_id / f"{chapter}_final"


def _user_dir(username: str) -> Path:
    """返回并确保用户目录存在。"""
    d = USERS_PATH / username
    d.mkdir(parents=True, exist_ok=True)
    return d


def _user_custom_file(username: str) -> Path:
    return _user_dir(username) / "custom.json"


def _user_ref_dir(username: str) -> Path:
    d = _user_dir(username) / "reference"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _find_existing_rel_path(base_dir: Path, rel_prefix: str, stem: str) -> Optional[str]:
    for ext in _IMAGE_EXT_CANDIDATES:
        p = base_dir / f"{stem}.{ext}"
        if p.exists():
            return f"{rel_prefix}/{stem}.{ext}"
    return None


def _collect_frame_rel_paths(book_id: str, chapter: str, frame_id: int, n_images: int) -> Optional[List[str]]:
    base_dir = BASE_PATH / "image" / book_id / chapter
    rel_prefix = f"image/{book_id}/{chapter}"
    out: List[str] = []
    for k in range(1, n_images + 1):
        rel = _find_existing_rel_path(base_dir, rel_prefix, f"{frame_id}_{k}")
        if not rel:
            return None
        out.append(rel)
    return out


def _collect_with_text_rel_paths(book_id: str, chapter: str, frame_id: int, n_images: int) -> Optional[List[str]]:
    base_dir = BASE_PATH / "with_text" / book_id / chapter
    rel_prefix = f"with_text/{book_id}/{chapter}"
    out: List[str] = []
    for k in range(1, n_images + 1):
        rel = _find_existing_rel_path(base_dir, rel_prefix, f"{frame_id}_{k}")
        if not rel:
            return None
        out.append(rel)
    return out


def _save_as_jpg(src: Path, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im.convert("RGB").save(dest, format="JPEG", quality=95, optimize=True)


# ─────────────────────────── Routes ─────────────────────────────

@app.get("/")
async def index():
    return FileResponse(str(static_path / "index.html"))


# ── Auth ────────────────────────────────────────────────────────

@app.post("/api/auth/register")
async def register(body: UserRegister):
    """注册新用户。用户名只允许字母、数字、下划线，长度 2-30。"""
    username = body.username.strip()
    if not re.match(r'^[a-zA-Z0-9_\u4e00-\u9fff]{2,30}$', username):
        raise HTTPException(status_code=400, detail="用户名只允许 2-30 位字母、数字、下划线或汉字")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="密码长度至少 6 位")

    sanitized = _sanitize(username)
    if _load_user_profile(sanitized) is not None:
        raise HTTPException(status_code=409, detail="用户名已被占用")

    _user_dir(sanitized)
    profile = {
        "username": sanitized,
        "display_name": username,
        "password_hash": _hash_password(body.password),
        "is_guest": False,
        "created_at": datetime.datetime.now().isoformat(),
    }
    _save_user_profile(sanitized, profile)

    token = _create_token(sanitized)
    return {"token": token, "username": sanitized, "display_name": profile["display_name"], "is_guest": False}


@app.post("/api/auth/login")
async def login(body: UserLogin):
    """用户登录，返回 JWT token。"""
    sanitized = _sanitize(body.username.strip())
    profile = _load_user_profile(sanitized)
    if profile is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not _verify_password(body.password, profile.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = _create_token(sanitized)
    return {"token": token, "username": sanitized, "display_name": profile.get("display_name", sanitized), "is_guest": profile.get("is_guest", False)}


@app.get("/api/auth/me")
async def me(request: Request):
    """返回当前登录用户信息。"""
    current_user: str = request.state.current_user
    profile = _load_user_profile(current_user)
    if profile is None:
        return {"username": current_user, "display_name": current_user, "is_guest": False}
    return {
        "username": current_user,
        "display_name": profile.get("display_name", current_user),
        "is_guest": profile.get("is_guest", False),
    }


@app.post("/api/auth/guest")
async def guest_login():
    """创建游客账号并自动登录。"""
    for _ in range(10):
        suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        username = f"guest_{suffix}"
        sanitized = _sanitize(username)
        if _load_user_profile(sanitized) is None:
            break
    else:
        raise HTTPException(status_code=500, detail="创建游客账号失败，请重试")

    password = ''.join(random.choices(string.ascii_letters + string.digits, k=20))
    _user_dir(sanitized)
    profile = {
        "username": sanitized,
        "display_name": username,
        "password_hash": _hash_password(password),
        "is_guest": True,
        "created_at": datetime.datetime.now().isoformat(),
    }
    _save_user_profile(sanitized, profile)
    token = _create_token(sanitized)
    return {"token": token, "username": sanitized, "display_name": username, "is_guest": True}


@app.put("/api/auth/rename")
async def rename_user(body: UserRename, request: Request):
    """修改用户名和/或密码。游客账户无需提供当前密码。"""
    current_user: str = request.state.current_user
    profile = _load_user_profile(current_user)
    if profile is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    is_guest = profile.get("is_guest", False)
    # 非游客需验证当前密码
    if not is_guest:
        if not body.current_password:
            raise HTTPException(status_code=400, detail="请输入当前密码")
        if not _verify_password(body.current_password, profile.get("password_hash", "")):
            raise HTTPException(status_code=401, detail="当前密码错误")

    new_username = body.new_username.strip()
    if not re.match(r'^[a-zA-Z0-9_\u4e00-\u9fff]{2,30}$', new_username):
        raise HTTPException(status_code=400, detail="用户名只允许 2-30 位字母、数字、下划线或汉字")

    new_sanitized = _sanitize(new_username)
    if new_sanitized != current_user and _load_user_profile(new_sanitized) is not None:
        raise HTTPException(status_code=409, detail="用户名已被占用")

    new_profile = dict(profile)
    new_profile["username"] = new_sanitized
    new_profile["display_name"] = new_username
    new_profile["is_guest"] = False

    if body.new_password:
        if len(body.new_password) < 6:
            raise HTTPException(status_code=400, detail="密码至少 6 位")
        new_profile["password_hash"] = _hash_password(body.new_password)

    if new_sanitized != current_user:
        # 重命名相关目录
        for subdir in ["users", "script", "image", "with_text", "books"]:
            old_dir = BASE_PATH / subdir / current_user
            new_dir = BASE_PATH / subdir / new_sanitized
            if old_dir.exists() and not new_dir.exists():
                shutil.move(str(old_dir), str(new_dir))
        # 更新所有 session
        for sdir in SESSIONS_PATH.iterdir():
            meta_file = sdir / "meta.json"
            if not meta_file.exists():
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                changed = False
                for key in ("owner_username", "username"):
                    if _sanitize(str(meta.get(key, ""))) == current_user:
                        meta[key] = new_sanitized
                        changed = True
                if _sanitize(str(meta.get("book_id", ""))) == current_user:
                    meta["book_id"] = new_sanitized
                    changed = True
                if changed:
                    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        # 删除旧 profile
        _user_profile_file(current_user).unlink(missing_ok=True)

    _save_user_profile(new_sanitized, new_profile)
    token = _create_token(new_sanitized)
    return {"token": token, "username": new_sanitized, "display_name": new_username, "is_guest": False}


# ── Step 1: Create session ──────────────────────────────────────

@app.post("/api/sessions")
async def create_session(body: SessionCreate, request: Request):
    current_user: str = request.state.current_user
    session_id = str(uuid.uuid4())
    book_id = _sanitize(current_user) or "default"
    chapter = _sanitize(body.title) or session_id[:8]

    # Ensure user directory exists
    _user_dir(book_id)

    # Save session dir
    sdir = _session_dir(session_id)
    sdir.mkdir(parents=True, exist_ok=True)

    meta = {
        "session_id": session_id,
        "title": body.title,
        "original_author": body.original_author,
        "username": current_user,
        "owner_username": current_user,
        "notes": body.notes,
        "num_images": body.num_images,
        "style_preset": body.style_preset,
        "streaming": body.streaming,
        "book_id": book_id,
        "chapter": chapter,
        "status": "created",
        "created_at": datetime.datetime.now().isoformat(),
    }
    _save_meta(session_id, meta)

    # Write the novel text to books/book_id/chapter.txt
    books_dir = BASE_PATH / "books" / book_id
    books_dir.mkdir(parents=True, exist_ok=True)
    txt_file = books_dir / f"{chapter}.txt"

    # Build the text file: first line = title, second line = author,platform,url
    # original_author is optional; keep 3-field format for downstream parser compatibility.
    safe_original_author = (body.original_author or "").strip()
    file_content = f"{body.title}\n{safe_original_author},网络,未知\n{body.text}"
    txt_file.write_text(file_content, encoding="utf-8")

    return {"session_id": session_id, "book_id": book_id, "chapter": chapter}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    meta = _require_session_owner(session_id, request.state.current_user)
    source_text = _load_session_source_text(meta)
    return {
        **meta,
        "source_text": source_text.get("full_text", ""),
        "source_text_raw": source_text.get("raw_text", ""),
    }


# ── Step 2: Generate storyboard (SSE) ──────────────────────────

@app.get("/api/sessions/{session_id}/board/stream")
async def stream_board(session_id: str, request: Request):
    meta = _require_session_owner(session_id, request.state.current_user)
    book_id = meta["book_id"]
    chapter = meta["chapter"]

    async def event_generator():
        # Check if already done
        existing = _load_storyboard(book_id, chapter)
        if existing:
            # Filter out meta block (id=-1)
            frames = [f for f in existing if f.get("id") != -1]
            yield f"data: {json.dumps({'type': 'done', 'frames': frames})}\n\n"
            return

        yield f"data: {json.dumps({'type': 'progress', 'message': '正在调用 AI 生成剧本...视输入文本长度影响，此阶段可能持续超过两分钟。'})}\n\n"

        loop = asyncio.get_event_loop()
        user_custom: dict = {}
        try:
            user_custom_file = _user_custom_file(book_id)
            if user_custom_file.exists():
                user_custom = json.loads(user_custom_file.read_text(encoding="utf-8"))
        except Exception:
            user_custom = {}
        try:
            # Run blocking board generation in thread pool
            from board import generate_storyboard
            await loop.run_in_executor(
                _executor,
                lambda: generate_storyboard(book_id=book_id, chapter=chapter, custom_data=user_custom)
            )
        except Exception as e:
            msg = str(e)
            if "排队中" in msg:
                yield f"data: {json.dumps({'type': 'queued', 'message': msg})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'error', 'message': msg})}\n\n"
            return

        storyboard = _load_storyboard(book_id, chapter)
        if not storyboard:
            yield f"data: {json.dumps({'type': 'error', 'message': '剧本生成失败：未找到输出文件'})}\n\n"
            return

        meta["status"] = "board_done"
        _save_meta(session_id, meta)

        frames = [f for f in storyboard if f.get("id") != -1]
        yield f"data: {json.dumps({'type': 'done', 'frames': frames})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/sessions/{session_id}/board")
async def get_board(session_id: str, request: Request):
    meta = _require_session_owner(session_id, request.state.current_user)
    data = _load_storyboard(meta["book_id"], meta["chapter"])
    frames = [f for f in data if f.get("id") != -1]
    return {"frames": frames}


@app.put("/api/sessions/{session_id}/board")
async def update_board(session_id: str, body: StoryboardUpdate, request: Request):
    meta = _require_session_owner(session_id, request.state.current_user)
    book_id = meta["book_id"]
    chapter = meta["chapter"]

    # Preserve meta block (id=-1) from existing data
    existing = _load_storyboard(book_id, chapter)
    meta_blocks = [f for f in existing if f.get("id") == -1]
    new_data = meta_blocks + body.data
    _save_storyboard(book_id, chapter, new_data)

    meta["status"] = "board_edited"
    _save_meta(session_id, meta)
    return {"ok": True}


# ── Step 4: Generate images (frame-by-frame SSE) ──────────────

@app.get("/api/sessions/{session_id}/images/stream")
async def stream_images(session_id: str, request: Request):
    meta = _require_session_owner(session_id, request.state.current_user)
    book_id = meta["book_id"]
    chapter = meta["chapter"]

    async def event_generator():
        import re as _re
        loop = asyncio.get_event_loop()

        storyboard = _load_storyboard(book_id, chapter)
        frames = [f for f in storyboard if f.get("id") != -1]
        total = len(frames)
        n_images = int(meta.get("num_images", 2))
        img_dir = BASE_PATH / "image" / book_id / chapter

        # ── 短路：仅当 raw + with_text 都完整时才直接返回 ────────────────
        all_raw_done = frames and all(_collect_frame_rel_paths(book_id, chapter, int(frame["id"]), n_images) for frame in frames)
        all_with_text_done = frames and all(_collect_with_text_rel_paths(book_id, chapter, int(frame["id"]), n_images) for frame in frames)
        if all_raw_done and all_with_text_done:
            for frame in frames:
                frame["image_path"] = _collect_frame_rel_paths(book_id, chapter, int(frame["id"]), n_images) or []
            yield f"data: {json.dumps({'type': 'done', 'frames': frames})}\n\n"
            return

        # ── 防重入：同一 session 不允许并发触发图片生成 ─────────────────
        if session_id in _active_image_sessions:
            yield f"data: {json.dumps({'type': 'error', 'message': '图片生成任务正在进行中，请勿重复触发'})}\n\n"
            return
        _active_image_sessions[session_id] = True

        # 追踪所有 dialog asyncio.Task，断连时统一取消
        _dialog_tasks: List[asyncio.Task] = []

        try:
            yield f"data: {json.dumps({'type': 'start', 'total': total})}\n\n"

            # Queue used only for collecting dialog results
            dialog_result_q: asyncio.Queue = asyncio.Queue()

            async def run_dialog_task(frame, n_img):
                fid = frame["id"]
                out_dir = BASE_PATH / "with_text" / book_id / chapter
                out_dir.mkdir(parents=True, exist_ok=True)
                try:
                    from dialog import process_item as _dlg_process
                    await loop.run_in_executor(
                        _executor,
                        lambda f=frame, bd=book_id, ch=chapter, od=str(out_dir): _dlg_process(f, bd, ch, od)
                    )
                except asyncio.CancelledError:
                    return
                except Exception:
                    pass
                wt_paths: List[str] = []
                for k in range(1, n_img + 1):
                    rel = _find_existing_rel_path(out_dir, f"with_text/{book_id}/{chapter}", f"{fid}_{k}")
                    if rel:
                        wt_paths.append(rel)
                await dialog_result_q.put({"type": "dialog_done", "frame_id": fid, "frame": frame, "with_text_paths": wt_paths})

            dialog_task_count = 0
            dialog_done_count = 0

            async def _drain_dialog_q():
                """非阻塞排空对话框结果队列，返回已 yield 的事件列表（调用方负责 yield）"""
                results = []
                while not dialog_result_q.empty():
                    try:
                        results.append(dialog_result_q.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                return results

            # ── 逐帧生成图片，每张图片完成后立即检查并发出已完成的对话框结果 ──
            for i, frame in enumerate(frames):
                item_id = frame["id"]
                img_dir.mkdir(parents=True, exist_ok=True)

                img_paths = [str(img_dir / f"{item_id}_{k+1}.{IMAGE_EXT}") for k in range(n_images)]
                rel_paths = [f"image/{book_id}/{chapter}/{item_id}_{k+1}.{IMAGE_EXT}" for k in range(n_images)]

                yield f"data: {json.dumps({'type': 'progress', 'message': f'生成第 {i+1}/{total} 个分镜图片…', 'frame_id': item_id})}\n\n"
                await asyncio.sleep(0)

                already_exists = all(Path(p).exists() for p in img_paths)

                if not already_exists:
                    try:
                        from image import process_item as _img_process
                        from novelai.types import CharacterReference

                        def _strip(t): return _re.sub(r'<([^>]+)>', r'\1', t)
                        _main = _strip(frame.get("main_tags", ""))
                        _chars = [_strip(t) for t in frame.get("character_tags", [])]
                        _orient = frame.get("orientation", "portrait")
                        _refs = frame.get("reference")
                        _char_refs = None
                        if isinstance(_refs, list) and _refs:
                            _char_refs = [
                                CharacterReference(image=r, type="character", fidelity=1, strength=1)
                                for r in _refs if r
                            ] or None
                        _err = str(img_dir / f"{item_id}.txt")

                        def _run(f=frame, m=_main, c=_chars, o=_orient,
                                 ip=img_paths, tp=rel_paths, e=_err,
                                 cr=_char_refs, ch=chapter, iid=str(item_id)):
                            return _img_process(f, m, c, o, ip, tp, e, cr, ch, iid)

                        # 在等待图片生成（run_in_executor）期间，之前帧的对话框任务可以并发执行
                        ok, err_msg = await loop.run_in_executor(_executor, _run)
                        if not ok:
                            if "排队中" in str(err_msg):
                                yield f"data: {json.dumps({'type': 'queued', 'message': str(err_msg)})}\n\n"
                                await asyncio.sleep(0)
                                return
                            yield f"data: {json.dumps({'type': 'frame_error', 'frame_id': item_id, 'message': err_msg})}\n\n"
                            await asyncio.sleep(0)
                            # 错误帧：直接往队列放占位结果，保持计数一致
                            await dialog_result_q.put({"type": "dialog_done", "frame_id": item_id, "frame": frame, "with_text_paths": []})
                            dialog_task_count += 1
                            # 排空已完成的对话框结果并立刻发出
                            for _r in await _drain_dialog_q():
                                yield f"data: {json.dumps(_r)}\n\n"
                                await asyncio.sleep(0)
                                dialog_done_count += 1
                            continue
                    except Exception as e:
                        if "排队中" in str(e):
                            yield f"data: {json.dumps({'type': 'queued', 'message': str(e)})}\n\n"
                            await asyncio.sleep(0)
                            return
                        yield f"data: {json.dumps({'type': 'frame_error', 'frame_id': item_id, 'message': str(e)})}\n\n"
                        await asyncio.sleep(0)
                        await dialog_result_q.put({"type": "dialog_done", "frame_id": item_id, "frame": frame, "with_text_paths": []})
                        dialog_task_count += 1
                        for _r in await _drain_dialog_q():
                            yield f"data: {json.dumps(_r)}\n\n"
                            await asyncio.sleep(0)
                            dialog_done_count += 1
                        continue

                frame["image_path"] = rel_paths

                # 启动对话框后台任务，追踪引用以便断连时取消
                t = asyncio.create_task(run_dialog_task(frame, n_images))
                _dialog_tasks.append(t)
                dialog_task_count += 1
                await asyncio.sleep(0)  # 让新建的任务有机会开始调度

                for _r in await _drain_dialog_q():
                    yield f"data: {json.dumps(_r)}\n\n"
                    await asyncio.sleep(0)
                    dialog_done_count += 1

            # ── 收尾：等待并发出剩余对话框结果 ──
            while dialog_done_count < dialog_task_count:
                result = await dialog_result_q.get()
                yield f"data: {json.dumps(result)}\n\n"
                await asyncio.sleep(0)
                dialog_done_count += 1

            # Persist updated image_path fields back to storyboard JSON
            existing = _load_storyboard(book_id, chapter)
            meta_blocks = [f for f in existing if f.get("id") == -1]
            _save_storyboard(book_id, chapter, meta_blocks + frames)

            meta["status"] = "images_done"
            _save_meta(session_id, meta)
            yield f"data: {json.dumps({'type': 'done', 'frames': frames})}\n\n"

        finally:
            # 无论正常结束还是客户端断连，都释放锁并取消未完成的 dialog Task
            _active_image_sessions.pop(session_id, None)
            for t in _dialog_tasks:
                if not t.done():
                    t.cancel()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/sessions/{session_id}/images")
async def get_images(session_id: str, request: Request):
    meta = _require_session_owner(session_id, request.state.current_user)
    data = _load_storyboard(meta["book_id"], meta["chapter"])
    frames = [f for f in data if f.get("id") != -1]
    return {"frames": frames}


@app.get("/api/sessions/{session_id}/images/poll")
async def poll_images(session_id: str, request: Request):
    """Poll for raw images that have been generated so far (for live carousel)."""
    meta = _require_session_owner(session_id, request.state.current_user)
    book_id = meta["book_id"]
    chapter = meta["chapter"]

    storyboard = _load_storyboard(book_id, chapter)
    frames = [f for f in storyboard if f.get("id") != -1]
    img_dir = BASE_PATH / "image" / book_id / chapter

    result = []
    for frame in frames:
        fid = frame["id"]
        available = []
        for idx in range(1, 5):
            rel = _find_existing_rel_path(img_dir, f"image/{book_id}/{chapter}", f"{fid}_{idx}")
            if rel:
                available.append(rel)
        if available:
            result.append({**frame, "available_images": available})

    return {
        "frames_with_images": result,
        "total_frames": len(frames),
        "done_count": len(result),
    }


# ── Step 5: Selection ───────────────────────────────────────────

@app.get("/api/sessions/{session_id}/selection")
async def get_selection(session_id: str, request: Request):
    meta = _require_session_owner(session_id, request.state.current_user)
    sdir = _session_dir(session_id)
    sel_file = sdir / "selection.json"
    if sel_file.exists():
        return json.loads(sel_file.read_text(encoding="utf-8"))
    # Default: each frame selects image 0
    data = _load_storyboard(meta["book_id"], meta["chapter"])
    frames = [f for f in data if f.get("id") != -1]
    selections = {str(f["id"]): 0 for f in frames}
    return {"selections": selections, "cover_id": frames[0]["id"] if frames else None}


@app.put("/api/sessions/{session_id}/selection")
async def update_selection(session_id: str, body: SelectionUpdate, request: Request):
    _require_session_owner(session_id, request.state.current_user)
    sdir = _session_dir(session_id)
    data = body.model_dump()
    (sdir / "selection.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


@app.post("/api/sessions/{session_id}/selection/finalize")
async def finalize_selection(session_id: str, background_tasks: BackgroundTasks, request: Request):
    """Copy selected images to final directory and generate cover."""
    meta = _require_session_owner(session_id, request.state.current_user)
    book_id = meta["book_id"]
    chapter = meta["chapter"]
    sdir = _session_dir(session_id)

    sel_file = sdir / "selection.json"
    sel_data = json.loads(sel_file.read_text(encoding="utf-8")) if sel_file.exists() else {}
    selections = sel_data.get("selections", {})
    cover_id = sel_data.get("cover_id")
    discarded_ids = set(str(x) for x in (sel_data.get("discarded") or []))

    storyboard = _load_storyboard(book_id, chapter)
    frames = [f for f in storyboard if f.get("id") != -1]

    final_dir = _get_final_dir(book_id, chapter)
    # Clear existing files so stale frames from previous runs don't linger
    if final_dir.exists():
        for pattern in ("*.jpg", "*.jpeg", "*.png"):
            for _f in final_dir.glob(pattern):
                _f.unlink(missing_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)

    cover_src = None
    dest_counter = 1

    for i, frame in enumerate(frames):
        fid = str(frame["id"])
        # Skip discarded frames entirely
        if fid in discarded_ids:
            continue
        sel_idx = int(selections.get(fid, 0))

        # Look for processed image in with_text first, else raw
        with_text_dir = _get_with_text_dir(book_id, chapter)
        candidate_names = [
            f"{frame['id']}_{sel_idx + 1}.jpg",
            f"{frame['id']}_{sel_idx + 1}.jpeg",
            f"{frame['id']}_{sel_idx + 1}.png",
            f"{frame['id']}.jpg",
            f"{frame['id']}.jpeg",
            f"{frame['id']}.png",
        ]
        src = None
        for name in candidate_names:
            p = with_text_dir / name
            if p.exists():
                src = p
                break
        if src is None:
            # Fall back to raw image
            image_paths = frame.get("image_path", [])
            if sel_idx < len(image_paths):
                src = BASE_PATH / image_paths[sel_idx]

        if src and Path(src).exists():
            dest_name = f"{dest_counter:03d}.jpg"  # sequential; 000 reserved for cover
            dest_counter += 1
            dest = final_dir / dest_name
            _save_as_jpg(Path(src), dest)
            if cover_id is not None and frame["id"] == cover_id:
                # Use raw (no-dialog) image for cover generation
                raw_paths = frame.get("image_path", [])
                raw_sel = BASE_PATH / raw_paths[sel_idx] if sel_idx < len(raw_paths) else None
                if raw_sel and Path(raw_sel).exists():
                    cover_src = str(raw_sel)
                else:
                    cover_src = str(dest)  # fallback to with_text

    # Generate cover
    if cover_src:
        try:
            from make_cover import make_manga_cover
            json_path = str(_script_path(book_id, chapter))
            cover_out = str(final_dir / "000.jpg")
            make_manga_cover(
                img_path=cover_src,
                json_path=json_path,
                output_path=cover_out,
            )
        except Exception as e:
            print(f"Cover generation failed: {e}")

    meta["status"] = "finalized"
    _save_meta(session_id, meta)
    return {"ok": True, "final_dir": str(final_dir)}


# ── Step 4 single frame regen ───────────────────────────────────

@app.post("/api/sessions/{session_id}/frames/{frame_id}/regenerate")
async def regenerate_frame(session_id: str, frame_id: int, request: Request):
    """Delete existing images for a frame and regenerate only that frame + its dialog."""
    import re as _re
    meta = _require_session_owner(session_id, request.state.current_user)
    book_id = meta["book_id"]
    chapter = meta["chapter"]

    # Delete old images
    img_dir = BASE_PATH / "image" / book_id / chapter
    for suffix in ["_1.jpg", "_1.jpeg", "_1.png", "_2.jpg", "_2.jpeg", "_2.png", ".txt"]:
        p = img_dir / f"{frame_id}{suffix}"
        if p.exists():
            p.unlink()

    # Also delete old with_text images (dynamic count)
    wt_dir = BASE_PATH / "with_text" / book_id / chapter
    for pattern in (f"{frame_id}_*.jpg", f"{frame_id}_*.jpeg", f"{frame_id}_*.png"):
        for _p in wt_dir.glob(pattern):
            _p.unlink(missing_ok=True)

    storyboard = _load_storyboard(book_id, chapter)
    frame = next((f for f in storyboard if f.get("id") == frame_id), None)
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")

    import os as _os
    loop = asyncio.get_event_loop()
    n_images = int(meta.get("num_images", 2))
    img_dir.mkdir(parents=True, exist_ok=True)

    img_paths = [str(img_dir / f"{frame_id}_{k+1}.{IMAGE_EXT}") for k in range(n_images)]
    rel_paths = [f"image/{book_id}/{chapter}/{frame_id}_{k+1}.{IMAGE_EXT}" for k in range(n_images)]

    try:
        from image import process_item as _img_process
        from novelai.types import CharacterReference

        def _strip(t): return _re.sub(r'<([^>]+)>', r'\1', t)
        _main  = _strip(frame.get("main_tags", ""))
        _chars = [_strip(t) for t in frame.get("character_tags", [])]
        _orient = frame.get("orientation", "portrait")
        _refs = frame.get("reference")
        _char_refs = None
        if isinstance(_refs, list) and _refs:
            _char_refs = [CharacterReference(image=r, type="character", fidelity=1, strength=1) for r in _refs if r] or None
        _err = str(img_dir / f"{frame_id}.txt")

        ok, err_msg = await loop.run_in_executor(
            _executor,
            lambda: _img_process(frame, _main, _chars, _orient, img_paths, rel_paths,
                                  _err, _char_refs, chapter, str(frame_id),
                                  random_seed=True)
        )
        if not ok:
            raise HTTPException(status_code=500, detail=err_msg)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Run dialog only for this frame
    try:
        from dialog import process_item as _dlg_process
        _out_dir = str(BASE_PATH / "with_text" / book_id / chapter)
        Path(_out_dir).mkdir(parents=True, exist_ok=True)
        await loop.run_in_executor(
            _executor,
            lambda: _dlg_process(frame, book_id, chapter, _out_dir)
        )
    except Exception:
        pass

    frame["image_path"] = rel_paths
    # Persist
    existing = _load_storyboard(book_id, chapter)
    for idx, f in enumerate(existing):
        if f.get("id") == frame_id:
            existing[idx] = frame
            break
    _save_storyboard(book_id, chapter, existing)

    import time as _time
    return {"frame": frame, "ts": int(_time.time())}


# ── Preview & Download ─────────────────────────────────────────

@app.get("/api/sessions/{session_id}/preview")
async def get_preview(session_id: str, request: Request):
    """Return list of final images for preview."""
    meta = _require_session_owner(session_id, request.state.current_user)
    book_id = meta["book_id"]
    chapter = meta["chapter"]

    final_dir = _get_final_dir(book_id, chapter)
    if not final_dir.exists():
        raise HTTPException(status_code=404, detail="Final images not yet generated. Please finalize selection first.")

    from concate import get_sorted_images
    files = get_sorted_images(str(final_dir))
    urls = [f"/with_text/{book_id}/{chapter}_final/{f}" for f in files]
    return {"images": urls, "total": len(urls)}


@app.get("/api/sessions/{session_id}/download/single/{frame_index}")
async def download_single(session_id: str, frame_index: int, request: Request):
    meta = _require_session_owner(session_id, request.state.current_user)
    book_id = meta["book_id"]
    chapter = meta["chapter"]
    final_dir = _get_final_dir(book_id, chapter)

    from concate import get_sorted_images
    files = get_sorted_images(str(final_dir))
    if frame_index >= len(files):
        raise HTTPException(status_code=404, detail="Frame not found")

    img_path = final_dir / files[frame_index]
    media_type = "image/jpeg" if img_path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return FileResponse(str(img_path), filename=files[frame_index], media_type=media_type)


@app.get("/api/sessions/{session_id}/download/long")
async def download_long(session_id: str, request: Request):
    """Merge all final images into a long image and return."""
    meta = _require_session_owner(session_id, request.state.current_user)
    book_id = meta["book_id"]
    chapter = meta["chapter"]
    final_dir = _get_final_dir(book_id, chapter)

    from concate import merge_images_in_folder
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_executor, lambda: merge_images_in_folder(str(final_dir)))

    merged_path = final_dir.parent / f"{chapter}_final.jpg"
    if not merged_path.exists():
        raise HTTPException(status_code=500, detail="合并图片生成失败")

    return FileResponse(str(merged_path), filename=f"{chapter}.jpg", media_type="image/jpeg")


@app.get("/api/sessions/{session_id}/download/zip")
async def download_zip(session_id: str, request: Request):
    """Zip all final images and return."""
    meta = _require_session_owner(session_id, request.state.current_user)
    book_id = meta["book_id"]
    chapter = meta["chapter"]
    final_dir = _get_final_dir(book_id, chapter)

    from concate import get_sorted_images
    files = get_sorted_images(str(final_dir))

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(str(final_dir / f), f)
    buf.seek(0)

    from urllib.parse import quote
    encoded_name = quote(f"{chapter}.zip")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"},
    )

# ── Custom characters (missing char handling) ───────────────────────

@app.get("/api/sessions/{session_id}/missing_characters")
async def get_missing_characters(session_id: str, request: Request):
    """扫描已生成的剧本 JSON，返回其中未被替换的 <角色名> 列表。"""
    meta = _require_session_owner(session_id, request.state.current_user)
    storyboard = _load_storyboard(meta["book_id"], meta["chapter"])
    storyboard_text = json.dumps(storyboard)
    missing = sorted(set(re.findall(r'<([^>]+)>', storyboard_text)))
    return {"missing": missing}


@app.post("/api/sessions/{session_id}/rename_character")
async def rename_character_in_storyboard(session_id: str, body: RenameCharacterRequest, request: Request):
    """将剧本中所有 <old_name> 替换为 <new_name>，返回更新后的 frames 和剩余未识别列表。"""
    old_name = body.old_name.strip()
    new_name = body.new_name.strip()
    if not old_name or not new_name:
        raise HTTPException(status_code=400, detail="角色名不能为空")

    meta = _require_session_owner(session_id, request.state.current_user)
    book_id = meta["book_id"]
    chapter = meta["chapter"]

    if old_name != new_name:
        storyboard = _load_storyboard(book_id, chapter)
        storyboard_text = json.dumps(storyboard, ensure_ascii=False)
        new_text = re.sub(r'<' + re.escape(old_name) + r'>', f'<{new_name}>', storyboard_text)
        _save_storyboard(book_id, chapter, json.loads(new_text))

    storyboard = _load_storyboard(book_id, chapter)
    frames = [f for f in storyboard if f.get("id") != -1]
    missing = sorted(set(re.findall(r'<([^>]+)>', json.dumps(storyboard))))
    return {"ok": True, "frames": frames, "missing": missing}


@app.post("/api/sessions/{session_id}/missing_characters/infer")
async def infer_missing_character(session_id: str, body: MissingCharacterInferRequest, request: Request):
    """使用 LLM 推断未识别角色的 tag 与简要外貌（仅性别+发色）。"""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="角色名不能为空")

    meta = _require_session_owner(session_id, request.state.current_user)
    storyboard = _load_storyboard(meta["book_id"], meta["chapter"])
    title = _extract_story_title(storyboard, meta["chapter"])
    descs = _collect_related_descriptions(storyboard, name, limit=3)
    if not descs:
        # 回退：取前 3 条分镜描述，避免无上下文
        for item in storyboard:
            if isinstance(item, dict) and item.get("id") != -1:
                d = str(item.get("description", "")).strip()
                if d:
                    descs.append(d)
                if len(descs) >= 3:
                    break

    llm_api_url = os.getenv("GEMINI_API_URL")
    llm_model = os.getenv("LLM_MODEL", "claude-opus-4-6")

    llm_key = None
    try:
        from openai import OpenAI
        from key_pool import acquire_api_key, release_api_key

        llm_key = acquire_api_key("LLM_API_KEY")
        if not llm_key:
            raise HTTPException(status_code=503, detail="暂无可用 LLM KEY，当前请求排队中，请稍后重试")

        client = OpenAI(api_key=llm_key, base_url=llm_api_url)

        system_prompt = (
            "你是二次元角色 Danbooru tag 助手。"
            "你必须只返回 JSON，不要输出任何额外文字。"
            "JSON 结构固定为："
            '{"tag":"...","appearance":"...","is_known_character":true/false}。'
            "规则："
            "1) 若能判断是已知角色，tag 返回该角色 danbooru tag，且不要使用下划线；"
            "2) 若疑似原创/未知角色，tag 返回一组外貌标签；"
            "3) appearance 仅允许包含性别与发色，例如 girl, blue hair；"
            "4) tag 与 appearance 都使用英文逗号分隔短标签。"
        )

        desc_block = "\n".join([f"- {d}" for d in descs]) if descs else "- （无可用分镜描述）"
        
        # 模糊查找 Danbooru 中相似的角色（最多 10 个）
        from danbooru_search import search_similar_characters
        similar_chars = search_similar_characters(name, limit=10)
        
        # 构建精简 Danbooru 候选：仅保留下划线转空格后的 tag 列表
        similar_block = ""
        if similar_chars:
            candidate_tags = [str(c.get("display_name") or c.get("name", "")).replace("_", " ").strip() for c in similar_chars]
            candidate_tags = [x for x in candidate_tags if x]
            if candidate_tags:
                similar_block = "\n\nDanbooru 候选tag（按相关度与热度排序）：\n" + ", ".join(candidate_tags)
        
        user_prompt = (
            f"文章标题：{title}\n"
            f"目标角色名：{name}\n"
            "对应分镜描述（最多三条）：\n"
            f"{desc_block}"
            f"{similar_block}\n"
            "请根据以上信息推断该角色的 Danbooru tag 与外貌特征，按要求输出 JSON。"
        )


        resp = client.chat.completions.create(
            model=llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )

        raw = (resp.choices[0].message.content or "").strip()
        parsed = _extract_json_from_text(raw)

        tag = str(parsed.get("tag", "")).replace("_", " ").strip()
        appearance = _normalize_appearance(str(parsed.get("appearance", "")).strip())
        is_known = bool(parsed.get("is_known_character", False))

        if not tag:
            tag = appearance

        status = "已知角色" if is_known else "未知角色"
        similar_info = ", ".join(c.get("display_name") or c.get("name", "") for c in similar_chars[:3]) if similar_chars else "无"
        print(f"  [推断] {name} → {status} | tag: {tag} | 相似角色: {similar_info}")

        return {
            "name": name,
            "tag": tag,
            "appearance": appearance,
            "is_known_character": is_known,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 推断失败: {e}")
    finally:
        if llm_key:
            try:
                from key_pool import release_api_key
                release_api_key("LLM_API_KEY", llm_key)
            except Exception:
                pass


@app.post("/api/users/{username}/upload_ref")
async def upload_user_ref_image(username: str, request: Request, file: UploadFile = File(...)):
    """上传参考图片到用户目录 users/{username}/reference/，返回相对路径。"""
    sanitized = _sanitize(request.state.current_user)
    ref_dir = _user_ref_dir(sanitized)
    filename = Path(file.filename).name
    dest = ref_dir / filename
    content = await file.read()
    dest.write_bytes(content)
    return {"filename": filename, "path": f"users/{sanitized}/reference/{filename}"}


@app.post("/api/sessions/{session_id}/custom_characters")
async def save_custom_character(session_id: str, request: Request):
    """将角色写入用户 custom.json，并重新处理剧本角色标签。"""
    body = await request.json()
    name = body.get("name", "").strip().lower()
    tag = body.get("tag", "").strip()
    appearance = body.get("appearance", "").strip()
    ref_path_raw = body.get("ref_path", "").strip()

    if not name or not tag:
        raise HTTPException(status_code=400, detail="角色名和 Tag 不能为空")

    meta = _require_session_owner(session_id, request.state.current_user)
    user_book_id = meta.get("book_id", "default")

    # Save to user-specific custom.json
    user_custom = _user_custom_file(user_book_id)
    custom_data: dict = {}
    if user_custom.exists():
        try:
            custom_data = json.loads(user_custom.read_text(encoding="utf-8"))
        except Exception:
            pass

    entry: list = [tag, appearance]
    if ref_path_raw:
        entry.append(ref_path_raw.replace("/", "\\"))
    custom_data[name] = entry
    user_custom.write_text(json.dumps(custom_data, ensure_ascii=False, indent=2), encoding="utf-8")

    meta = _require_session_owner(session_id, request.state.current_user)
    book_id = meta["book_id"]
    chapter = meta["chapter"]
    loop = asyncio.get_event_loop()
    from board import reprocess_characters
    remaining = await loop.run_in_executor(_executor, lambda: reprocess_characters(book_id, chapter, custom_data=custom_data))

    storyboard = _load_storyboard(book_id, chapter)
    frames = [f for f in storyboard if f.get("id") != -1]
    return {"ok": True, "remaining_missing": remaining, "frames": frames}

# ── User sessions & characters ─────────────────────────────────

@app.get("/api/users/{username}/sessions")
async def get_user_sessions(username: str, request: Request):
    """返回指定用户的所有会话列表，按创建时间倒序。"""
    current_user = request.state.current_user
    book_id = _sanitize(current_user)
    sessions = []
    for session_dir in SESSIONS_PATH.iterdir():
        if not session_dir.is_dir():
            continue
        meta_file = session_dir / "meta.json"
        if meta_file.exists():
            try:
                m = json.loads(meta_file.read_text(encoding="utf-8"))
                if _session_belongs_to_user(m, current_user):
                    sessions.append(m)
            except Exception:
                pass
    sessions.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"sessions": sessions}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    """删除会话目录及其关联的 books/script/image/with_text 数据。"""
    sdir = _session_dir(session_id)
    if not sdir.exists():
        raise HTTPException(status_code=404, detail="Session not found")

    # Authorization check
    _require_session_owner(session_id, request.state.current_user)

    # Read meta to find book_id / chapter before deletion
    try:
        meta = _load_meta(session_id)
        book_id = meta.get("book_id", "")
        chapter = meta.get("chapter", "")
    except Exception:
        book_id = chapter = ""

    # Delete session directory
    shutil.rmtree(str(sdir), ignore_errors=True)

    # Delete associated per-chapter data if book_id/chapter are known
    if book_id and chapter:
        targets = [
            BASE_PATH / "books" / book_id / f"{chapter}.txt",
            BASE_PATH / "script" / book_id / f"{chapter}.json",
            BASE_PATH / "image" / book_id / chapter,
            BASE_PATH / "with_text" / book_id / chapter,
            BASE_PATH / "with_text" / book_id / f"{chapter}_final",
            BASE_PATH / "with_text" / book_id / f"{chapter}_final.jpg",
            BASE_PATH / "with_text" / book_id / f"{chapter}_final.png",
        ]
        for t in targets:
            try:
                if t.is_dir():
                    shutil.rmtree(str(t), ignore_errors=True)
                elif t.is_file():
                    t.unlink(missing_ok=True)
            except Exception:
                pass

    return {"ok": True}


@app.get("/api/users/{username}/characters")
async def get_user_characters(username: str, request: Request, q: str = ""):
    """返回预设角色和用户自定义角色。"""
    import data as _data
    sanitized = _sanitize(request.state.current_user)

    # Load user-specific custom
    user_custom_file = _user_custom_file(sanitized)
    all_custom: dict = {}
    if user_custom_file.exists():
        try:
            all_custom = json.loads(user_custom_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Preset = base character_map minus user custom keys
    preset_map = _data.get_character_map()
    preset = {k: v for k, v in preset_map.items() if k not in all_custom}

    # Filter by q
    if q:
        q_lower = q.lower()
        preset = {k: v for k, v in preset.items() if q_lower in k}
        all_custom = {k: v for k, v in all_custom.items() if q_lower in k}

    return {"preset": preset, "custom": all_custom}


@app.post("/api/users/{username}/characters")
async def save_user_character(username: str, request: Request):
    """创建或更新用户自定义角色。"""
    body = await request.json()
    name = body.get("name", "").strip().lower()
    tag = body.get("tag", "").strip()
    appearance = body.get("appearance", "").strip()
    ref_path_raw = body.get("ref_path", "").strip()

    if not name or not tag:
        raise HTTPException(status_code=400, detail="角色名和 Tag 不能为空")

    sanitized = _sanitize(request.state.current_user)
    user_custom_file = _user_custom_file(sanitized)
    custom_data: dict = {}
    if user_custom_file.exists():
        try:
            custom_data = json.loads(user_custom_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    entry: list = [tag, appearance]
    if ref_path_raw:
        entry.append(ref_path_raw.replace("/", "\\"))
    custom_data[name] = entry
    user_custom_file.write_text(json.dumps(custom_data, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"ok": True}


@app.delete("/api/users/{username}/characters/{name}")
async def delete_user_character(username: str, name: str, request: Request):
    """删除用户自定义角色。"""
    sanitized = _sanitize(request.state.current_user)
    user_custom_file = _user_custom_file(sanitized)
    custom_data: dict = {}
    if user_custom_file.exists():
        try:
            custom_data = json.loads(user_custom_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    if name not in custom_data:
        raise HTTPException(status_code=404, detail="角色不存在")

    del custom_data[name]
    user_custom_file.write_text(json.dumps(custom_data, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"ok": True}


# ── Run ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web_app:app", host="0.0.0.0", port=8000, reload=True)
