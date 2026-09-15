import os
import edge_tts
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List
import re
import random
from urllib.parse import unquote
from mangum import Mangum

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


# ================= 2. 纯净流媒体合成：绝对安全，永不自爆 =================
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

        async def generate():
            # 每 10 个词一批，稳扎稳打
            batch_size = 10
            for i in range(0, len(words), batch_size):
                batch_words = words[i:i+batch_size]
                
                # 手工拼接最标准的微软 SSML 格式
                ssml = f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='zh-CN'>"
                ssml += f"<voice name='{voice}'>"
                ssml += f"<prosody rate='{speed_rate}' pitch='{pitch_rate}'>"
                
                for j, word in enumerate(batch_words):
                    ssml += f"{word}<break time='{pause_ms}ms'/>"
                    ssml += f"{word}<break time='{pause_ms}ms'/>"
                    ssml += f"{word}"
                    
                    # 如果不是全部听写的最后一个词，就加词间大停顿
                    is_last_word_overall = (i + j) == (len(words) - 1)
                    if not is_last_word_overall:
                        ssml += f"<break time='{word_gap_ms}ms'/>"
                        
                ssml += "</prosody></voice></speak>"
                
                # 🎯 核心魔法：只替换当前这个对象的内部方法，不碰系统的任何全局设置！
                communicate = edge_tts.Communicate(text="dummy")
                communicate._generate_ssml = lambda s=ssml: s  
                
                try:
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio":
                            yield chunk["data"]
                except Exception as e:
                    print(f"流合成错误: {e}")
                    break

        return StreamingResponse(generate(), media_type="audio/mpeg")
        
    except Exception as e:
        print(f"音频合成故障: {e}")
        raise HTTPException(status_code=500, detail=str(e))

handler = Mangum(app)
