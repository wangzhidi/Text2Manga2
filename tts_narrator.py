"""
Narrator TTS: LLM generates a storyteller narration from the script,
then the entire text is passed to Qwen3TTS VoiceDesign in a single call.
"""

import json
import os
import time
from pathlib import Path

import soundfile as sf
import torch
from dotenv import load_dotenv
from modelscope import snapshot_download
from openai import OpenAI

from key_pool import acquire_api_key, release_api_key
from qwen_tts import Qwen3TTSModel

load_dotenv()

VOICE_DESIGN_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"

NARRATOR_VOICE = (
    "成熟男性，30岁左右，声音低沉磁性，富有情感，叙事感强，"
    "语速适中偏缓，语调有起伏，适合讲述故事"
)

SYSTEM_PROMPT = """\
你是一位专业的说书人。根据漫画/小说脚本，以第三人称解说者的视角，将整个故事重新写成一段流畅的旁白，用于朗读配音。

要求：
- 包含场景环境、人物动作、表情、心理的描写
- 将对话融入叙述中（可用直接引语，格式如：她说道，"……"）
- 语言优美流畅，情感充沛，有画面感
- 只输出旁白正文，不含任何说明、标题或格式标记
- 控制在600字以内
"""


def load_script_text(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    frames = [d for d in data if d.get("id", -1) >= 0]
    parts = []
    for frame in frames:
        desc = frame.get("description", "").strip()
        if desc:
            parts.append(f"[场景：{desc}]")
        for text, ttype, speaker in zip(
            frame.get("text", []),
            frame.get("text_type", []),
            frame.get("speaker", []),
        ):
            if ttype != "sound effect":
                parts.append(f"{speaker}：{text}")
    return "\n".join(parts)


def generate_narration(script_text: str) -> str:
    api_key = acquire_api_key("LLM_API_KEY")
    try:
        client = OpenAI(api_key=api_key, base_url=os.getenv("GEMINI_API_URL"))
        resp = client.chat.completions.create(
            model="claude-opus-4-6",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": script_text},
            ],
            temperature=0.7,
        )
    finally:
        release_api_key("LLM_API_KEY", api_key)
    return resp.choices[0].message.content.strip()


def run(script_path: str, output_path: str, tts: Qwen3TTSModel):
    name = Path(script_path).stem
    print(f"\n--- {name} ---")

    script_text = load_script_text(script_path)
    print("Generating narration with LLM...")
    narration = generate_narration(script_text)
    print(f"Narration ({len(narration)} chars):\n{narration[:120]}...\n")

    t0 = time.time()
    wavs, sr = tts.generate_voice_design(
        text=narration,
        language="Chinese",
        instruct=NARRATOR_VOICE,
        max_new_tokens=4096,
    )
    elapsed = time.time() - t0

    wav = wavs[0]
    duration = len(wav) / sr
    print(f"Synthesized {duration:.1f}s audio in {elapsed:.1f}s")

    sf.write(output_path, wav, sr)
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    base_dir = Path(__file__).parent

    print(f"Loading VoiceDesign model...")
    model_dir = snapshot_download(VOICE_DESIGN_MODEL_ID)
    tts = Qwen3TTSModel.from_pretrained(
        model_dir,
        device_map="cuda:0",
        torch_dtype=torch.bfloat16,
    )

    scripts = [
        (
            "script/z归档/魔法少女独断万古/第二章：奇迹.json",
            "test_tts_out/第二章：奇迹_narrator.wav",
        ),
        (
            "script/z归档/4利蒲是coconut/可莉，该长大.json",
            "test_tts_out/可莉，该长大_narrator.wav",
        ),
    ]

    for script_rel, output_rel in scripts:
        run(
            script_path=str(base_dir / script_rel),
            output_path=str(base_dir / output_rel),
            tts=tts,
        )

    del tts
    torch.cuda.empty_cache()
