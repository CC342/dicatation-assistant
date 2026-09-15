import os
import edge_tts
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from typing import List
import re
import random
from mangum import Mangum

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 动态定位 data 目录（在 Vercel 中，data 文件夹与 api 目录同级在根目录下）
BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

# ================= 1. 物理文件扫描逻辑 (完全根据你的 Armbian 原版 1:1 还原) =================
def natural_sort_key(s):
    return [(0, int(text)) if text.isdigit() else (1, text.lower()) for text in re.split(r'(\d+)', s)]

@app.get("/api/grades")
def get_grades():
    if not os.path.exists(BASE_DIR):
        return {"grades": ["请先创建年级"]}
    grades = [d for d in os.listdir(BASE_DIR) if os.path.isdir(os.path.join(BASE_DIR, d))]
    grades.sort(key=natural_sort_key)
    return {"grades": grades if grades else ["请先创建年级"]}

@app.get("/api/files/{grade}")
def get_files(grade: str):
    grade_path = os.path.join(BASE_DIR, grade)
    if not os.path.exists(grade_path) or not os.path.isdir(grade_path):
        return {"files": []}
    files = []
    for f in os.listdir(grade_path):
        name, ext = os.path.splitext(f)
        if ext.lower() == ".txt":
            files.append(name)
    files.sort(key=natural_sort_key)
    return {"files": files}

class ContentRequest(BaseModel):
    grade: str
    files: List[str]

@app.post("/api/content")
def get_content(req: ContentRequest):
    if not req.files or "请先" in req.grade:
        return {"text": ""}
    file_names = [fn for fn in req.files if fn is not None]
    if not file_names:
        return {"text": ""}
         
    combined_words = []
    for fn in file_names:
        file_path = os.path.join(BASE_DIR, req.grade, f"{fn}.txt")
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    combined_words.append(content)
    return {"text": "、".join(combined_words)}


# ================= 2. 终极绝杀：完全不碰硬盘的纯内存流媒体直出 =================
# 完全替代了原版的 pydub，但朗读节奏和停顿细节严格 100% 对齐原版！
@app.get("/api/stream")
async def stream_audio(
    text: str,
    voice: str = "zh-CN-XiaoxiaoNeural",
    speed: float = 0.85,
    pause_seconds: float = 1.5,
    pitch: int = -15,
    shuffle_bool: bool = False
):
    try:
        # 严格保留你的切词与乱序逻辑
        raw_words = re.split(r'[,，\s、\n\r]+', text)
        words = [w.strip() for w in raw_words if w.strip()]
        if not words:
            raise HTTPException(status_code=400, detail="词语列表为空")
        if shuffle_bool:
            random.shuffle(words)

        # 严格保留你的语速、语调、停顿计算逻辑
        speed_rate = f"{int((speed - 1.0) * 100)}%" if speed != 1.0 else "+0%"
        pitch_rate = f"{pitch:+d}Hz"
        pause_ms = int(pause_seconds * 1000)
        word_gap_ms = pause_ms + 500

        ssml_parts = ["<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='zh-CN'>"]
        ssml_parts.append(f"<voice name='{voice}'>")
        
        # 严格还原原版的 pydub 拼接逻辑：读一遍+停顿+读一遍+停顿+读最后一遍
        for i, word in enumerate(words):
            # 第一遍
            ssml_parts.append(f"<prosody rate='{speed_rate}' pitch='{pitch_rate}'>{word}</prosody>")
            ssml_parts.append(f"<break time='{pause_ms}ms'/>")
            # 第二遍
            ssml_parts.append(f"<prosody rate='{speed_rate}' pitch='{pitch_rate}'>{word}</prosody>")
            ssml_parts.append(f"<break time='{pause_ms}ms'/>")
            # 第三遍 (原版 pydub 中第三遍后面直接接 word_gap，所以这里不加 pause_ms)
            ssml_parts.append(f"<prosody rate='{speed_rate}' pitch='{pitch_rate}'>{word}</prosody>")
            
            # 严格按照原版：如果不是最后一个词，才加上词间大停顿 (word_gap)
            if i < len(words) - 1:
                ssml_parts.append(f"<break time='{word_gap_ms}ms'/>")
            
        ssml_parts.append("</voice></speak>")
        ssml_text = "".join(ssml_parts)

        # 纯内存直接输出数据流，彻底规避 Vercel 的 500 本地存储权限报错
        communicate = edge_tts.Communicate(text=ssml_text, voice=voice)
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]

        return Response(content=audio_data, media_type="audio/mpeg")
        
    except Exception as e:
        print(f"音频合成故障: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Vercel Serverless 适配网关
handler = Mangum(app)
