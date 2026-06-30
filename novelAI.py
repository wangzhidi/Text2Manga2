from novelai import NovelAI
from novelai.types import GenerateImageParams
from novelai.types import Character
from novelai.types import CharacterReference
from pathlib import Path

from dotenv import load_dotenv
import os
from key_pool import get_api_keys
# 加载环境变量
load_dotenv(override=True)

# 初始化novelAI客户端
api_keys = get_api_keys("NA_API_KEY")
api_key = api_keys[0] if api_keys else ""
if not api_key:
    raise RuntimeError("未配置可用 NA_API_KEY")

# 初始化客户端 (API 密钥来自 NOVELAI_API_KEY 环境变量)
client = NovelAI(api_key=api_key)
characters = [
    Character(
        prompt=r"girl, white hair, thighhighs, looking back, ",
        enabled=True
    ),
    Character(
        prompt=r"girl, pink hair, hair ornement, hug from behind",
        enabled=True
    ),
]

path1=Path("reference\\linnea.png")
path2=Path("reference\\zibai.png")

character_references = [
    CharacterReference(
        image=path1,
        type="character", 
        fidelity=1,  
        strength=1,  
    ),
    CharacterReference(
        image=path2,
        type="character",  
        fidelity=1,  
        strength=1,  
    )
]

suffix=r"[[[artist:kedama_milk]]],,{{{saintshiro}}},[kazutake_hazano],artist:ciloranko,artist:suyamori, {artist:iumu ::,,0.65:: artist:icecake ::,{reoen ::,,1.4::artsit:chen_bin::,[shuri_\(84k\)],{{rin_yuu}},artist:mignon,[[[ningen mame]]]"
main_prompt=r"2girls, looking at another, "
# 生成图像
params = GenerateImageParams(
    prompt=main_prompt+suffix,
    model="nai-diffusion-4-5-full",
    # model="nai-diffusion-3",
    size="portrait",  # 或 (832, 1216)
    steps=28,
    scale=5.0,
    seed=403834307,
    sampler="k_euler_ancestral",
    characters=characters,
    n_samples=1,
    quality=True,
    character_references=character_references

)

images = client.image.generate(params)
images[0].save("output.png")