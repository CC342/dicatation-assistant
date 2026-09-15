import os
import edge_tts
import edge_tts.communicate
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from typing import List
import re
import random
from urllib.parse import unquote
import xml.sax.saxutils
from mangum import Mangum

# ================= 🚀 终极安全拦截器 =================
# 只针对 edge_tts，绝对不影响 FastAPI 系统的其他部分
_orig_escape = xml.sax.saxutils.escape

def _safe_escape(data, entities=None):
    # 安全的类型检查：只有是字符串且包含我们的标签时，才放行
    if isinstance(data, str) and ("<break" in data or "</prosody>" in data):
        return data
    # 其他所有情况，乖乖走系统原生的转义逻辑
    if entities is None:
        return _orig_escape(data)
    return _orig_escape(data, entities)

# 精准狙击：只替换 edge_tts 内部的 escape，绝不触碰系统全局！
if hasattr(edge_tts.communicate, "escape"):
    edge_tts.communicate.escape = _safe_escape
# =======================================================

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 动态定位 data 目录
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


# ================= 2. 完美的流媒体合成 + 伪装注入 =================
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

        speed_rate = f"{int((speed - 1.0) * 100)}%" if speed != 1.0 else "+0%"
        pitch_rate = f"{pitch:+d}Hz"
        pause_ms = int(pause_seconds * 1000)
        word_gap_ms = pause_ms + 500

        # 在安全的拦截器护航下，注入停顿代码
        injected_parts = []
        for i, word in enumerate(words):
            injected_parts.append(word)
            injected_parts.append(f"</prosody><break time='{pause_ms}ms'/><prosody rate='{speed_rate}' pitch='{pitch_rate}'>")
            
            injected_parts.append(word)
            injected_parts.append(f"</prosody><break time='{pause_ms}ms'/><prosody rate='{speed_rate}' pitch='{pitch_rate}'>")
            
            injected_parts.append(word)
            
            if i < len(words) - 1:
                injected_parts.append(f"</prosody><break time='{word_gap_ms}ms'/><prosody rate='{speed_rate}' pitch='{pitch_rate}'>")

        injected_text = "".join(injected_parts)

        communicate = edge_tts.Communicate(text=injected_text, voice=voice, rate=speed_rate, pitch=pitch_rate)
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]

        return Response(content=audio_data, media_type="audio/mpeg")
        
    except Exception as e:
        print(f"音频合成故障: {e}")
        raise HTTPException(status_code=500, detail=str(e))

handler = Mangum(app)
