"""
手动测试：Danbooru 查找 + LLM 推断接口

示例：
python test_tag_inference.py --name nagisa
python test_tag_inference.py --name nagisa --session-id <sid> --token <jwt>
"""

import argparse
import base64
import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
from urllib import request, error

from danbooru_search import search_similar_characters


BASE_PATH = Path(__file__).parent
SESSIONS_PATH = BASE_PATH / "sessions"
JWT_SECRET = os.environ.get("JWT_SECRET", "text2manga-default-secret-change-in-production")
JWT_ALGORITHM = "HS256"


def _create_local_token(username: str) -> str:
    if JWT_ALGORITHM != "HS256":
        raise RuntimeError(f"当前测试脚本仅支持 HS256，实际为: {JWT_ALGORITHM}")

    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    header = {"alg": "HS256", "typ": "JWT"}
    expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
    payload = {"sub": username, "exp": int(expire.timestamp())}

    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    sig_b64 = _b64url(sig)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def _pick_local_session_and_token():
    """从本地 sessions 里复用一个 session_id，并为其 owner 生成可用 token。"""
    if not SESSIONS_PATH.exists():
        return "", ""

    candidates = []
    for sdir in SESSIONS_PATH.iterdir():
        if not sdir.is_dir():
            continue
        meta_file = sdir / "meta.json"
        if not meta_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        username = (meta.get("owner_username") or meta.get("username") or "").strip()
        session_id = (meta.get("session_id") or sdir.name or "").strip()
        created_at = str(meta.get("created_at", ""))
        if username and session_id:
            candidates.append((created_at, session_id, username))

    if not candidates:
        return "", ""

    # 取最新会话
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, session_id, username = candidates[0]
    token = _create_local_token(username)
    return session_id, token


def test_danbooru_search(name: str, limit: int = 10):
    print(f"\n[1] Danbooru 模糊查找: {name}")
    candidates = search_similar_characters(name, limit=limit)
    if not candidates:
        print("  无候选")
        return []

    for i, c in enumerate(candidates, 1):
        print(
            f"  {i:02d}. {c.get('display_name', c['name'])} "
            f"(score={c.get('score')}, text={c.get('text_score')}, post={int(c.get('post_count', 0))})"
        )
    return candidates


def test_llm_infer_api(base_url: str, session_id: str, token: str, name: str):
    print(f"\n[2] 调用 LLM 推断接口: {base_url}/api/sessions/{session_id}/missing_characters/infer")

    url = f"{base_url.rstrip('/')}/api/sessions/{session_id}/missing_characters/infer"
    body = json.dumps({"name": name}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    req = request.Request(url=url, data=body, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=120) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(text)
            print("  推断结果:")
            print("   - tag:", data.get("tag"))
            print("   - appearance:", data.get("appearance"))
            print("   - is_known_character:", data.get("is_known_character"))
            sims = data.get("similar_characters") or []
            if sims:
                print("   - similar_characters:")
                for i, s in enumerate(sims[:10], 1):
                    print(f"      {i:02d}. {s.get('display_name', s.get('name'))}")
    except error.HTTPError as e:
        err_text = e.read().decode("utf-8", errors="ignore")
        print(f"  接口失败: HTTP {e.code} {err_text}")
    except Exception as e:
        print(f"  接口失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="测试 Danbooru 查找与 tag 推断")
    parser.add_argument("--name", required=True, help="角色名（可简称）")
    parser.add_argument("--limit", type=int, default=10, help="Danbooru 候选数量")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Web 服务地址")
    parser.add_argument("--session-id", default="", help="会话 ID（不传则自动复用本地最新会话）")
    parser.add_argument("--token", default="", help="JWT Token（不传则本地自动生成）")
    args = parser.parse_args()

    test_danbooru_search(args.name, args.limit)

    session_id = args.session_id
    token = args.token
    if not session_id or not token:
        auto_sid, auto_token = _pick_local_session_and_token()
        if not session_id:
            session_id = auto_sid
        if not token:
            token = auto_token

    if session_id and token:
        print(f"\n[2] 使用会话: {session_id}")
        test_llm_infer_api(args.base_url, session_id, token, args.name)
    else:
        print("\n[2] 跳过 LLM 接口测试（未找到可复用的本地 session/token）")


if __name__ == "__main__":
    main()
