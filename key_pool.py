import json
import os
import threading
from typing import Dict, List, Optional


def parse_api_keys(raw_value: Optional[str]) -> List[str]:
    """解析 .env 中的 API key 配置，支持 JSON 数组、逗号/换行分隔、单值。"""
    if not raw_value:
        return []

    raw = raw_value.strip()
    if not raw:
        return []

    keys: List[str] = []

    # 1) JSON 数组：例如 ["k1", "k2"]
    if raw.startswith("[") and raw.endswith("]"):
        try:
            arr = json.loads(raw)
            if isinstance(arr, list):
                for item in arr:
                    if item is None:
                        continue
                    s = str(item).strip().strip('"').strip("'")
                    if s:
                        keys.append(s)
        except Exception:
            pass

    # 2) 逗号/换行分隔
    if not keys:
        normalized = raw.replace("\n", ",").replace("\r", ",")
        for part in normalized.split(","):
            s = part.strip().strip('"').strip("'")
            if s:
                keys.append(s)

    return keys


class _ApiKeyPool:
    def __init__(self, keys: List[str]):
        self.keys = list(keys)
        # 与 keys 一一对应的槽位占用状态，支持重复 key 作为独立并发槽位
        self._in_use: List[bool] = [False for _ in self.keys]
        self._next_idx = 0
        self._lock = threading.Lock()

    def acquire(self) -> Optional[str]:
        with self._lock:
            if not self.keys:
                return None
            total = len(self.keys)
            for offset in range(total):
                idx = (self._next_idx + offset) % total
                if not self._in_use[idx]:
                    self._in_use[idx] = True
                    self._next_idx = (idx + 1) % total
                    return self.keys[idx]
            return None

    def release(self, key: Optional[str]):
        if not key:
            return
        with self._lock:
            # 释放第一个匹配且正在占用的槽位（支持重复 key）
            for idx, k in enumerate(self.keys):
                if k == key and self._in_use[idx]:
                    self._in_use[idx] = False
                    return

    def available_count(self) -> int:
        with self._lock:
            return sum(1 for used in self._in_use if not used)


_POOLS: Dict[str, _ApiKeyPool] = {}
_POOLS_LOCK = threading.Lock()


def get_api_keys(env_name: str) -> List[str]:
    return parse_api_keys(os.getenv(env_name, ""))


def _get_pool(env_name: str) -> _ApiKeyPool:
    keys = get_api_keys(env_name)
    with _POOLS_LOCK:
        pool = _POOLS.get(env_name)
        # 配置变化时重建池
        if pool is None or pool.keys != keys:
            pool = _ApiKeyPool(keys)
            _POOLS[env_name] = pool
        return pool


def acquire_api_key(env_name: str) -> Optional[str]:
    return _get_pool(env_name).acquire()


def release_api_key(env_name: str, key: Optional[str]):
    _get_pool(env_name).release(key)


def available_key_count(env_name: str) -> int:
    return _get_pool(env_name).available_count()
