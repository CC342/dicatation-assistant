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
import xml.sax.saxutils
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


# ================= 🚀 终极杀招：自定义安全的 SSML 引擎 =================
class DirectSSMLCommunicate(edge_tts.Communicate):
    """继承官方类，无损注入自定义 SSML，绝不影响全局环境"""
    def __init__(self, custom_ssml: str, voice: str):
        # 🚨 致命 Bug 修复处：必须传入 voice，否则 WebSocket 握手头和 SSML 内容不一致会被直接拉闸！
        super().__init__(text="dummy_text", voice=voice)
        self.custom_ssml = custom_ssml
        
    def _generate_ssml(self) -> str:
        return self.custom_ssml
# =======================================================================


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

        # 严格规范参数格式，确保携带正确的符号 (+ or -) 给微软
        speed_rate = f"{int((speed - 1.0) * 100):+d}%" 
        pitch_rate = f"{int(pitch):+d}Hz"
        pause_ms = int(pause_seconds * 1000)
        word_gap_ms = pause_ms + 500

        async def generate():
            batch_size = 5
            for i in range(0, len(words), batch_size):
                batch_words = words[i:i+batch_size]
                
                # 拼装最标准、严格的微软 SSML 格式
                ssml = f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="zh-CN">'
                ssml += f'<voice name="{voice}">'
                ssml += f'<prosody rate="{speed_rate}" pitch="{pitch_rate}" volume="+0%">'
                
                for j, word in enumerate(batch_words):
                    safe_word = xml.sax.saxutils.escape(word) # 安全转义，防乱码
                    ssml += f'{safe_word}<break time="{pause_ms}ms"/>'
                    ssml += f'{safe_word}<break time="{pause_ms}ms"/>'
                    ssml += f'{safe_word}'
                    
                    is_last_word_overall = (i + j) == (len(words) - 1)
                    if not is_last_word_overall:
                        ssml += f'<break time="{word_gap_ms}ms"/>'
                        
                ssml += '</prosody></voice></speak>'
                
                try:
                    # 调用我们安全的自定义引擎
                    tts = DirectSSMLCommunicate(custom_ssml=ssml, voice=voice)
                    async for chunk in tts.stream():
                        if chunk["type"] == "audio":
                            yield chunk["data"]
                except Exception as e:
                    print(f"Batch TTS Error: {e}")
                    break

        return StreamingResponse(generate(), media_type="audio/mpeg")
        
    except Exception as e:
        print(f"音频合成故障: {e}")
        raise HTTPException(status_code=500, detail=str(e))

handler = Mangum(app)
