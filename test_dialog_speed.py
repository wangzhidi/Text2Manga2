"""
dialog.py CPU vs GPU 速度测试
测试数据: image/6晓风瑞鹤/当你得知芙宁  +  script/6晓风瑞鹤/当你得知芙宁.json
"""

import json
import os
import shutil
import time
import torch

BOOK_ID  = "6晓风瑞鹤"
CHAPTER  = "当你得知芙宁"
JSON_PATH = f"script/{BOOK_ID}/{CHAPTER}.json"
OUT_BASE  = f"test_dialog_out"


def _load_items():
    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return [x for x in data if x.get("id") != -1]


def _clear_model_caches():
    """清空 dialog.py 的所有模型缓存，强制下次调用重新加载到指定设备。"""
    import dialog
    dialog._clip_model_cache.clear()
    dialog._clip_processor_cache.clear()
    dialog._yolo_model_cache.clear()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def run_benchmark(device_label: str, force_cpu: bool, items: list) -> list[float]:
    """
    在指定设备上顺序处理所有 item，返回每个 item 的耗时列表（秒）。
    force_cpu=True 时临时 monkeypatch torch.cuda.is_available 为 False。
    """
    out_dir = os.path.join(OUT_BASE, device_label)
    os.makedirs(out_dir, exist_ok=True)

    # Monkeypatch
    original_cuda_avail = torch.cuda.is_available
    if force_cpu:
        torch.cuda.is_available = lambda: False

    _clear_model_caches()

    from dialog import process_item

    print(f"\n{'='*60}")
    print(f"  设备: {device_label}  (共 {len(items)} 个 item)")
    print(f"{'='*60}")

    timings = []
    total_start = time.perf_counter()

    for item in items:
        t0 = time.perf_counter()
        result = process_item(item, BOOK_ID, CHAPTER, out_dir)
        elapsed = time.perf_counter() - t0
        timings.append(elapsed)
        print(f"  id={item['id']:>3}  {elapsed:6.2f}s  {result}")

    total = time.perf_counter() - total_start
    avg = sum(timings) / len(timings)
    print(f"\n  总计: {total:.2f}s  |  均值: {avg:.2f}s/item  |  最快: {min(timings):.2f}s  |  最慢: {max(timings):.2f}s")

    # 恢复
    torch.cuda.is_available = original_cuda_avail
    _clear_model_caches()

    return timings


def main():
    items = _load_items()
    print(f"加载 {len(items)} 个 item，图片目录: image/{BOOK_ID}/{CHAPTER}")

    # 清空上次输出
    if os.path.exists(OUT_BASE):
        shutil.rmtree(OUT_BASE)

    cpu_times = run_benchmark("CPU", force_cpu=True, items=items)

    if torch.cuda.is_available():
        gpu_times = run_benchmark("GPU", force_cpu=False, items=items)

        print(f"\n{'='*60}")
        print("  CPU vs GPU 汇总")
        print(f"{'='*60}")
        cpu_total = sum(cpu_times)
        gpu_total = sum(gpu_times)
        print(f"  CPU 总计: {cpu_total:.2f}s  GPU 总计: {gpu_total:.2f}s")
        print(f"  加速比:   {cpu_total / gpu_total:.2f}x")
    else:
        print("\nGPU 不可用，仅测试 CPU。")


if __name__ == "__main__":
    main()
