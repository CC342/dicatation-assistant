import os
import re
import random
from urllib.parse import unquote
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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
    grade = unquote(grade)
    grade_path = os.path.join(BASE_DIR, grade)
    if not os.path.exists(grade_path) or not os.path.isdir(grade_path):
        return {"files": []}
    files = []
    for f in os.listdir(grade_path):
        if not f.startswith('.'):
            name, _ = os.path.splitext(f)
            files.append(name)
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


# ================= 🚀 终极杀招：纯 Python MP3 帧拼接 =================
def get_silence_mp3(duration_ms: int) -> bytes:
    """
    生成纯物理静音的 MP3 数据流。
    标准 MP3 帧 (MPEG-2 Layer III, 24kHz, 32kbps, Mono) 单帧大小 72 bytes，播放 24 ms。
    """
    frame_hex = "fff344c400000003480000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
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
    print(f"========== [DEBUG LOG] 收到物理流媒体请求 ==========")
    print(f"参数: text={text[:10]}..., voice={voice}, speed={speed}, pause={pause_seconds}, pitch={pitch}")
    
    try:
        raw_words = re.split(r'[,，\s、\n\r]+', text)
        words = [w.strip() for w in raw_words if w.strip()]
        if not words:
            raise HTTPException(status_code=400, detail="词语列表为空")
        if shuffle_bool:
            random.shuffle(words)

        speed_rate = f"{int((speed - 1.0) * 100):+d}%"
        pitch_rate = f"{int(pitch):+d}Hz"
        
        # 预先准备好 1.5 秒和 2.0 秒的“纯静音积木”
        pause_ms = int(pause_seconds * 1000)
        word_gap_ms = pause_ms + 500
        silence_gap = get_silence_mp3(pause_ms)
        word_gap = get_silence_mp3(word_gap_ms)

        print(f"[INFO] 准备处理 {len(words)} 个词，使用纯净 MP3 拼接...")

        async def generate():
            for i, word in enumerate(words):
                print(f"[DEBUG] 正在向微软请求纯净发音: {word}")
                
                # 堂堂正正传纯文本，绝不带任何标签，微软绝对不会拒收！
                communicate = edge_tts.Communicate(text=word, voice=voice, rate=speed_rate, pitch=pitch_rate)
                word_audio = b""
                
                try:
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio":
                            word_audio += chunk["data"]
                except Exception as e:
                    print(f"[ERROR] 获取词语 '{word}' 失败: {e}")
                    continue
                    
                if not word_audio:
                    print(f"[WARN] 词语 '{word}' 未获取到音频，跳过。")
                    continue
                    
                # 就像搭积木一样物理拼接音频
                yield word_audio     # 第一遍
                yield silence_gap    # 停顿
                yield word_audio     # 第二遍
                yield silence_gap    # 停顿
                yield word_audio     # 第三遍
                
                # 若不是最后一个词，加上较长的词间停顿
                if i < len(words) - 1:
                    yield word_gap
                    
            print("[DEBUG] 全部音频拼接流传输完毕！")

        return StreamingResponse(generate(), media_type="audio/mpeg")
        
    except Exception as e:
        print(f"[FATAL ERROR] 音频合成整体故障: {e}")
        raise HTTPException(status_code=500, detail=str(e))

handler = Mangum(app)
