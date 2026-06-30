"""
Qwen3TTS test:
  Test 1: 第二章：奇迹.json  - voice design (no reference audio)
  Test 2: 可莉，该长大.json - voice clone (klee/albedo) + voice design (others)

Voice consistency: "Voice Design then Clone" pattern for all speakers.
  - Step 1: VoiceDesign generates one reference audio per speaker.
  - Step 2: Base model builds a reusable voice-clone prompt (ICL mode).
  - Step 3: All lines synthesized via Base model clone → consistent voice.

For klee/albedo: whisper transcribes the existing .ogg/.mp3 for ICL mode.
Fallback to VoiceDesign-generated reference if transcription fails.
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf
import torch
from dotenv import load_dotenv
from modelscope import snapshot_download
from openai import OpenAI

from key_pool import acquire_api_key, release_api_key
from qwen_tts import Qwen3TTSModel, VoiceClonePromptItem

load_dotenv()

SILENCE_WITHIN_FRAME = 0.3
SILENCE_BETWEEN_FRAMES = 0.7

VOICE_DESIGN_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
BASE_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"

# Speakers that have a reference audio file. Value: (path, ref_text or None).
# ref_text=None triggers whisper transcription.
REF_AUDIO_SPEAKERS = {
    "girl, blonde hair": ("reference_audio/genshin/klee.ogg", "可莉今天又勇敢地抓到了花纹奇怪的蜥蜴，从没见过这种图案，你要看看吗？"),
    "boy, blonde hair": ("reference_audio/genshin/albedo.mp3", "能麻烦你当我的助手吗？鉴于你旁观了这么多次，想必早就熟练了吧，放心，我在你旁边，就算是第一次实际操作，也不会有问题的，我相信我的指导能力，更相信你的天赋。"),
}

SYSTEM_PROMPT = """\
你是一个专业的声音设计师和情感标注员。
输入是一段漫画/小说的脚本，包含多个角色的对白。
你的任务：
1. 为每个独特的说话人生成音色描述（中文），包括性别、年龄、音色特征。格式：简洁中文，15-30字。
2. 为每一句对白标注情绪（中文，1-4字），例如：平静、兴奋、悲伤、愤怒、恐惧、疑惑、温柔。

说话人说明：<driver>/<narrator> 为旁白，其他为角色外貌描述。

输出格式（严格JSON，只输出JSON）：
{
  "voice_profiles": {
    "<speaker_id>": "<音色描述>",
    ...
  },
  "line_emotions": [
    {"frame_id": <frame_id>, "line_index": <line_index>, "emotion": "<情绪>"},
    ...
  ]
}"""


def load_script(path: str) -> List[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    frames = []
    for d in data:
        if d.get("id", -1) < 0:
            continue
        lines = []
        for text, ttype, speaker in zip(
            d.get("text", []), d.get("text_type", []), d.get("speaker", [])
        ):
            if ttype != "sound effect":
                lines.append({"text": text, "speaker": speaker})
        if lines:
            frames.append({"frame_id": d["id"], "lines": lines})
    return frames


def annotate_with_llm(frames: List[dict]) -> Tuple[Dict[str, str], Dict]:
    speakers = sorted({line["speaker"] for f in frames for line in f["lines"]})
    parts = [f"说话人列表：{speakers}\n\n脚本内容："]
    for frame in frames:
        parts.append(f"\n[frame {frame['frame_id']}]")
        for i, line in enumerate(frame["lines"]):
            parts.append(f"  {i}: [{line['speaker']}] {line['text']}")
    user_prompt = "\n".join(parts)

    api_key = acquire_api_key("LLM_API_KEY")
    try:
        client = OpenAI(api_key=api_key, base_url=os.getenv("GEMINI_API_URL"))
        resp = client.chat.completions.create(
            model="claude-opus-4-6",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
        )
    finally:
        release_api_key("LLM_API_KEY", api_key)

    raw = resp.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
    annotation = json.loads(raw.strip())

    emotion_map = {
        (item["frame_id"], item["line_index"]): item["emotion"]
        for item in annotation["line_emotions"]
    }
    return annotation["voice_profiles"], emotion_map


def transcribe_audio(audio_path: str) -> Optional[str]:
    """Transcribe audio via ModelScope whisper-small. Returns None on failure."""
    try:
        from transformers import pipeline as hf_pipeline
        print(f"  Transcribing {Path(audio_path).name} with Whisper...")
        model_dir = snapshot_download("openai/whisper-small")
        asr = hf_pipeline(
            "automatic-speech-recognition",
            model=model_dir,
            device=0 if torch.cuda.is_available() else -1,
        )
        result = asr(audio_path)
        text = result["text"].strip()
        print(f"  Transcript: {text[:80]}")
        del asr
        torch.cuda.empty_cache()
        return text
    except Exception as e:
        print(f"  Transcription failed: {e}")
        return None


def load_model(model_id: str, label: str) -> Qwen3TTSModel:
    print(f"Downloading {label} ({model_id})...")
    model_dir = snapshot_download(model_id)
    print(f"Loading {label}...")
    return Qwen3TTSModel.from_pretrained(
        model_dir,
        device_map="cuda:0",
        torch_dtype=torch.bfloat16,
    )


def build_clone_prompts(
    tts_vd: Qwen3TTSModel,
    tts_base: Qwen3TTSModel,
    speakers: List[str],
    voice_profiles: Dict[str, str],
    ref_audio_map: Optional[Dict[str, Tuple[str, Optional[str]]]] = None,
) -> Dict[str, List[VoiceClonePromptItem]]:
    """
    Build a reusable ICL voice-clone prompt for every speaker.

    ref_audio_map: speaker → (abs_path, ref_text_or_None)
      - If ref_text provided: use ICL mode directly with real audio.
      - If ref_text is None: attempt whisper transcription, fall back to VoiceDesign.
    For speakers not in ref_audio_map: VoiceDesign generates ref audio → ICL clone.
    """
    REF_TEXT_SEED = "你好，这是我的声音，请仔细聆听我的音色特征。"
    prompts: Dict[str, List[VoiceClonePromptItem]] = {}

    for sp in speakers:
        print(f"  Preparing voice for: {sp}")
        entry = ref_audio_map.get(sp) if ref_audio_map else None
        ref_audio_path, ref_text = entry if entry else (None, None)

        if ref_audio_path and ref_text is None and Path(ref_audio_path).exists():
            ref_text = transcribe_audio(ref_audio_path)

        if ref_audio_path and ref_text:
            print(f"    → ICL mode (real ref audio)")
            prompt_items = tts_base.create_voice_clone_prompt(
                ref_audio=ref_audio_path,
                ref_text=ref_text,
                x_vector_only_mode=False,
            )
        else:
            voice_desc = voice_profiles.get(sp, "普通人声，语速适中")
            print(f"    → VoiceDesign then Clone ({voice_desc[:30]})")
            wavs, sr = tts_vd.generate_voice_design(
                text=REF_TEXT_SEED,
                language="Chinese",
                instruct=voice_desc,
            )
            prompt_items = tts_base.create_voice_clone_prompt(
                ref_audio=(wavs[0], sr),
                ref_text=REF_TEXT_SEED,
                x_vector_only_mode=False,
            )

        prompts[sp] = prompt_items

    return prompts


def synthesize_story(
    frames: List[dict],
    tts_base: Qwen3TTSModel,
    clone_prompts: Dict[str, List[VoiceClonePromptItem]],
) -> Tuple[np.ndarray, int]:
    """Synthesize all lines using voice clone prompts. Returns (audio, sample_rate)."""
    total_lines = sum(len(f["lines"]) for f in frames)
    frame_segments = []
    t0 = time.time()
    done = 0

    for frame in frames:
        segs = []
        for i, line in enumerate(frame["lines"]):
            sp, text = line["speaker"], line["text"]
            wavs, sr = tts_base.generate_voice_clone(
                text=text,
                language="Chinese",
                voice_clone_prompt=clone_prompts[sp],
            )
            segs.append((wavs[0], sr))
            done += 1
            print(f"  [{done}/{total_lines}] {sp[:20]}: {text[:25]}... ({time.time()-t0:.0f}s)")
        frame_segments.append(segs)

    t_total = time.time() - t0
    print(f"Synthesis: {done} lines in {t_total:.1f}s ({t_total/done:.1f}s/line)")

    sr = frame_segments[0][0][1]
    parts = []
    for segs in frame_segments:
        for j, (wav, _) in enumerate(segs):
            parts.append(wav)
            if j < len(segs) - 1:
                parts.append(np.zeros(int(sr * SILENCE_WITHIN_FRAME), dtype=np.float32))
        parts.append(np.zeros(int(sr * SILENCE_BETWEEN_FRAMES), dtype=np.float32))
    return np.concatenate(parts), sr


def test_voice_design(script_path: str, output_path: str):
    print(f"\n=== Test 1: Voice Design then Clone (voice consistency) ===")
    frames = load_script(script_path)
    total_lines = sum(len(f["lines"]) for f in frames)
    speakers = sorted({line["speaker"] for f in frames for line in f["lines"]})
    print(f"Frames: {len(frames)}, Lines: {total_lines}, Speakers: {speakers}")

    print("LLM annotation...")
    voice_profiles, _ = annotate_with_llm(frames)

    tts_vd = load_model(VOICE_DESIGN_MODEL_ID, "VoiceDesign")
    tts_base = load_model(BASE_MODEL_ID, "Base")

    print("Building voice clone prompts...")
    clone_prompts = build_clone_prompts(tts_vd, tts_base, speakers, voice_profiles)

    audio, sr = synthesize_story(frames, tts_base, clone_prompts)
    sf.write(output_path, audio, sr)
    print(f"Saved: {output_path} ({len(audio)/sr:.1f}s)")

    del tts_vd, tts_base
    torch.cuda.empty_cache()


def test_voice_clone(script_path: str, output_path: str):
    print(f"\n=== Test 2: Voice Clone (real ref audio) + Voice Design then Clone ===")
    base_dir = Path(script_path).parent.parent.parent.parent

    frames = load_script(script_path)
    total_lines = sum(len(f["lines"]) for f in frames)
    speakers = sorted({line["speaker"] for f in frames for line in f["lines"]})
    print(f"Frames: {len(frames)}, Lines: {total_lines}, Speakers: {speakers}")

    # Build ref_audio_map: speaker → (abs_path, ref_text_or_None)
    ref_audio_map = {
        sp: (str(base_dir / rel_path), ref_text)
        for sp, (rel_path, ref_text) in REF_AUDIO_SPEAKERS.items()
        if (base_dir / rel_path).exists()
    }
    print(f"Reference audio available for: {list(ref_audio_map.keys())}")

    print("LLM annotation...")
    voice_profiles, _ = annotate_with_llm(frames)

    tts_vd = load_model(VOICE_DESIGN_MODEL_ID, "VoiceDesign")
    tts_base = load_model(BASE_MODEL_ID, "Base")

    print("Building voice clone prompts...")
    clone_prompts = build_clone_prompts(
        tts_vd, tts_base, speakers, voice_profiles, ref_audio_map=ref_audio_map
    )

    audio, sr = synthesize_story(frames, tts_base, clone_prompts)
    sf.write(output_path, audio, sr)
    print(f"Saved: {output_path} ({len(audio)/sr:.1f}s)")

    del tts_vd, tts_base
    torch.cuda.empty_cache()


if __name__ == "__main__":
    base_dir = Path(__file__).parent

    test_voice_clone(
        script_path=str(base_dir / "script/z归档/4利蒲是coconut/可莉，该长大.json"),
        output_path=str(base_dir / "test_tts_out/可莉，该长大_qwen2.wav"),
    )
