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
import edge_tts.communicate
import xml.sax.saxutils

# ================= 🚀 终极防死循环 & 纯净 SSML 补丁 =================
# 核心绝招：直接引用 Python 官方最底层的 escape，绝不引用 edge_tts 自身的 escape！
# 这样就算 Vercel 唤醒一万次，也绝对不可能发生“左脚踩右脚”的死循环！
_base_escape = xml.sax.saxutils.escape

def _safe_escape(data, entities=None):
    # 如果是我们自己拼装的 SSML 停顿标签，直接放行，骗过微软
    if isinstance(data, str) and ("<speak" in data or "<break" in data or "<prosody" in data):
        return data
    # 否则，调用系统【最原始】的转义函数
    if entities is not None:
        return _base_escape(data, entities)
    return _base_escape(data)

# 强行替换 edge_tts 内部的 escape 为我们的安全版本
edge_tts.communicate.escape = _safe_escape
# =======================================================================

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


# ================= 带 Log 的流媒体引擎 =================
@app.get("/api/stream")
async def stream_audio(
    text: str,
    voice: str = "zh-CN-XiaoxiaoNeural",
    speed: float = 0.85,
    pause_seconds: float = 1.5,
    pitch: int = -15,
    shuffle_bool: bool = False
):
    print(f"========== [DEBUG LOG] 收到流媒体请求 ==========")
    print(f"请求参数: text={text}, voice={voice}, speed={speed}, pause={pause_seconds}, pitch={pitch}")
    
    try:
        raw_words = re.split(r'[,，\s、\n\r]+', text)
        words = [w.strip() for w in raw_words if w.strip()]
        if not words:
            print("[ERROR] 词语列表为空")
            raise HTTPException(status_code=400, detail="词语列表为空")
        if shuffle_bool:
            random.shuffle(words)

        speed_rate = f"{int((speed - 1.0) * 100):+d}%"
        pitch_rate = f"{int(pitch):+d}Hz"
        pause_ms = int(pause_seconds * 1000)
        word_gap_ms = pause_ms + 500

        print(f"[INFO] 解析成功: 共有 {len(words)} 个词，准备分批生成。")

        async def generate():
            batch_size = 5
            for i in range(0, len(words), batch_size):
                batch_words = words[i:i+batch_size]
                print(f"\n[DEBUG] 正在处理批次: {batch_words}")
                
                # 直接将标签与真实单词拼接
                injected_parts = []
                for j, word in enumerate(batch_words):
                    safe_word = _base_escape(word) # 安全转义真实单词
                    injected_parts.append(safe_word)
                    injected_parts.append(f"<break time='{pause_ms}ms'/>")
                    injected_parts.append(safe_word)
                    injected_parts.append(f"<break time='{pause_ms}ms'/>")
                    injected_parts.append(safe_word)
                    
                    is_last_word_overall = (i + j) == (len(words) - 1)
                    if not is_last_word_overall:
                        injected_parts.append(f"<break time='{word_gap_ms}ms'/>")
                        
                injected_text = "".join(injected_parts)
                print(f"[DEBUG] 即将发送给 edge-tts 的文本:\n{injected_text}")

                try:
                    # 堂堂正正地使用自带类，不再用任何 dummy
                    communicate = edge_tts.Communicate(text=injected_text, voice=voice, rate=speed_rate, pitch=pitch_rate)
                    print(f"[DEBUG] Communicate 实例化成功，准备建立流...")
                    
                    chunk_count = 0
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio":
                            chunk_count += 1
                            yield chunk["data"]
                            
                    print(f"[DEBUG] 批次处理完毕，成功下发了 {chunk_count} 个音频数据块！")
                    
                except Exception as e:
                    print(f"[ERROR] 批次合成严重报错: {e}")
                    break

        return StreamingResponse(generate(), media_type="audio/mpeg")
        
    except Exception as e:
        print(f"[FATAL ERROR] 音频合成整体故障: {e}")
        raise HTTPException(status_code=500, detail=str(e))

handler = Mangum(app)
