import os
import re
import random
import asyncio
from urllib.parse import unquote
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, HTMLResponse
from pydantic import BaseModel
from mangum import Mangum

import edge_tts

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
ROOT_DIR = os.path.dirname(os.path.dirname(__file__))

@app.get("/")
def serve_homepage():
    html_path = os.path.join(ROOT_DIR, "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return {"detail": "网页版未找到"}


# ================= 🚀 新增：年级专属中文智能排序 =================
def grade_sort_key(s):
    # 1. 把中文数字翻译成真正的数字权重
    zh_num_map = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
    grade_num = 0
    for char in s:
        if char in zh_num_map:
            grade_num = zh_num_map[char]
            break
            
    # 兼容阿拉伯数字命名 (如: 5年级)
    if grade_num == 0:
        match = re.search(r'\d+', s)
        if match:
            grade_num = int(match.group())

    # 2. 赋予上下册权重（下册比上册大，排在前面）
    semester_weight = 0
    if '下' in s:
        semester_weight = 2
    elif '上' in s:
        semester_weight = 1
        
    return (grade_num, semester_weight)

# 普通文件的数字排序（保留用于第1课、第2课的排序）
def natural_sort_key(s):
    return [(0, int(text)) if text.isdigit() else (1, text.lower()) for text in re.split(r'(\d+)', s)]

@app.get("/api/grades")
def get_grades():
    if not os.path.exists(BASE_DIR):
        return {"grades": ["请先创建年级"]}
    grades = [d for d in os.listdir(BASE_DIR) if os.path.isdir(os.path.join(BASE_DIR, d))]
    
    # 🚨 核心改动：使用智能年级排序，并且 reverse=True 强行倒序（高年级在上）
    grades.sort(key=grade_sort_key, reverse=True)
    
    return {"grades": grades if grades else ["请先创建年级"]}
# =================================================================

@app.get("/api/files/{grade}")
def get_files(grade: str):
    grade = unquote(grade)
    grade_path = os.path.join(BASE_DIR, grade)
    if not os.path.exists(grade_path) or not os.path.isdir(grade_path):
        return {"files": []}
    files = []
    for f in os.listdir(grade_path):
        if not f.startswith('.'):
            name, _ = os.path.splitext(f)
            files.append(name)
    # 课时依然保持从小到大正向排序（第1课在最前）
    files.sort(key=natural_sort_key)
    return {"files": files}

class ContentRequest(BaseModel):
    grade: str
    files: List[str]

@app.post("/api/content")
def get_content(req: ContentRequest):
    grade = unquote(req.grade)
    if not req.files or "请先" in grade:
        return {"text": ""}
    combined_words = []
    for fn in req.files:
        if not fn: continue
        for ext in [".txt", ""]:
            file_path = os.path.join(BASE_DIR, grade, f"{fn}{ext}")
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        combined_words.append(content)
                break
    return {"text": "、".join(combined_words)}


# ================= 音质拯救计划：码率对齐与清洗 =================
def clean_mp3_data(data: bytes) -> bytes:
    res = bytearray(data)
    while res.startswith(b"ID3") and len(res) >= 10:
        size = (res[6] << 21) | (res[7] << 14) | (res[8] << 7) | res[9]
        res = res[10 + size:]
    if len(res) >= 128 and res[-128:-125] == b"TAG":
        res = res[:-128]
    return bytes(res)

def get_silence_mp3(duration_ms: int) -> bytes:
    frame_hex = "fff364c40000000348" + "00" * 135
    silent_frame = bytes.fromhex(frame_hex)
    num_frames = duration_ms // 24
    return silent_frame * num_frames

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
        raw_words = re.split(r'[,，\s、\n\r]+', text)
        words = [w.strip() for w in raw_words if w.strip()]
        if not words:
            raise HTTPException(status_code=400, detail="词语列表为空")
        if shuffle_bool:
            random.shuffle(words)

        speed_rate = f"{int((speed - 1.0) * 100):+d}%"
        pitch_rate = f"{int(pitch):+d}Hz"
        
        pause_ms = int(pause_seconds * 1000)
        word_gap_ms = pause_ms + 500
        silence_gap = get_silence_mp3(pause_ms)
        word_gap = get_silence_mp3(word_gap_ms)

        async def fetch_word(word: str) -> bytes:
            communicate = edge_tts.Communicate(text=word, voice=voice, rate=speed_rate, pitch=pitch_rate)
            audio_data = bytearray()
            try:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_data.extend(chunk["data"])
            except Exception as e:
                print(f"[ERROR] 获取词语 '{word}' 失败: {e}")
            
            return clean_mp3_data(bytes(audio_data))

        tasks = [fetch_word(word) for word in words]
        word_audios = await asyncio.gather(*tasks)

        final_audio = bytearray()
        for i, word_audio in enumerate(word_audios):
            if not word_audio:
                continue
            final_audio.extend(word_audio)
            final_audio.extend(silence_gap)
            final_audio.extend(word_audio)
            final_audio.extend(silence_gap)
            final_audio.extend(word_audio)
            
            if i < len(word_audios) - 1:
                final_audio.extend(word_gap)
                
        return Response(content=bytes(final_audio), media_type="audio/mpeg")
        
    except Exception as e:
        print(f"[FATAL ERROR] 音频合成整体故障: {e}")
        raise HTTPException(status_code=500, detail=str(e))

handler = Mangum(app)
