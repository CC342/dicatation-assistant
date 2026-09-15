import os
import uuid
import edge_tts
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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

# ================= 1. 物理文件扫描逻辑 (完全保留你原来的逻辑) =================
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


# ================= 2. Vercel 适配的听写生成核心逻辑 =================
class DictationRequest(BaseModel):
    text: str
    voice: str = "zh-CN-XiaoxiaoNeural"
    speed: float = 0.85
    pause_seconds: float = 1.5
    pitch: int = -15
    shuffle_bool: bool = False

@app.post("/api/generate")
async def process_dictation(req: DictationRequest):
    try:
        raw_words = re.split(r'[,$,\s、\n\r]+', req.text)
        words = [w.strip() for w in raw_words if w.strip()]
        if not words:
            raise HTTPException(status_code=400, detail="词语列表为空")
        if req.shuffle_bool:
            random.shuffle(words)

        speed_rate = f"{int((req.speed - 1.0) * 100)}%" if req.speed != 1.0 else "+0%"
        pitch_rate = f"{req.pitch:+d}Hz"
        pause_ms = int(req.pause_seconds * 1000)

        # 🎯 Vercel Serverless 核心改造：
        # 使用微软原生 SSML 语法直接在云端把“朗读 -> 停顿 -> 朗读 -> 停顿”一次性组合好并由微软合成大文件。
        # 这样既不需要本地安装 ffmpeg / pydub，也不会因为在 Armbian 慢速拼接而导致超时或卡死！
        ssml_parts = ["<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='zh-CN'>"]
        ssml_parts.append(f"<voice name='{req.voice}'>")
        
        for word in words:
            # 完美还原你原本的“读三遍 + 中间停顿”的听写逻辑
            for _ in range(3):
                ssml_parts.append(f"<prosody rate='{speed_rate}' pitch='{pitch_rate}'>{word}</prosody>")
                ssml_parts.append(f"<break time='{pause_ms}ms'/>")
            # 课与课或词与词之间的额外词间大间隔
            ssml_parts.append(f"<break time='{pause_ms + 500}ms'/>")
            
        ssml_parts.append("</voice></speak>")
        ssml_text = "".join(ssml_parts)

        # Vercel 只允许写入 /tmp 临时目录
        file_name = f"dictation_{uuid.uuid4().hex[:8]}.mp3"
        file_path = os.path.join("/tmp", file_name)

        communicate = edge_tts.Communicate(text=ssml_text, voice=req.voice)
        await communicate.save(file_path)

        if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
            raise HTTPException(status_code=500, detail="音频生成为空")

        # 返回符合前端调用的下载路由路径
        return {"status": "success", "file_url": f"/api/download/{file_name}"}
        
    except Exception as e:
        print(f"音频合成故障: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/download/{file_name}")
def download_audio(file_name: str):
    file_path = os.path.join("/tmp", file_name)
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="audio/mpeg")
    raise HTTPException(status_code=404, detail="文件不存在或已过期")

# Vercel Serverless 必备适配网关
handler = Mangum(app)
