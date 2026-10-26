import os
import re
import random
import socket
import asyncio
from urllib.parse import unquote
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, HTMLResponse
from pydantic import BaseModel
from mangum import Mangum

import aiohttp
import edge_tts

# ============ 🔧 edge-tts 403 修复补丁（2026-10-13）============
# 症状：微软正在把 Edge 朗读接口从旧版 v1 (speech.platform.bing.com)
#       迁移到新版 v2 (api.msedgeservices.com)，已升级的机房节点会
#       直接拒绝旧版 WebSocket 握手，返回 403（日志里每个词语都报
#       403 就是这个原因，不是代码写错了，也不是被限流）。
# 修法：把 edge-tts 建连时用的 WebSocket 地址切到微软新版 v2 接口
#       （和 Edge 155 浏览器实际用的同一套），token 生成逻辑不动。
# 参考：https://github.com/rany2/edge-tts/issues/490
try:
    import edge_tts.communicate as _edge_communicate
    from edge_tts import constants as _edge_constants
    _NEW_WSS_URL = (
        "wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"
        f"?Ocp-Apim-Subscription-Key={_edge_constants.TRUSTED_CLIENT_TOKEN}"
    )
    _edge_communicate.WSS_URL = _NEW_WSS_URL  # communicate.py 每次建连时读取该变量
    _edge_constants.WSS_URL = _NEW_WSS_URL
    print("[INFO] edge-tts 已切换到微软 v2 语音接口")
except Exception as _e:
    print(f"[WARN] edge-tts v2 接口补丁未生效: {_e}")
# ============================================================

# ============ Worker 中转（2026-10-26）============
# TTS_WS_BASE 设为 Cloudflare Worker 的 wss 地址（如 wss://xxx.workers.dev）时，
# websocket 改走 Worker 中转，绕开 Vercel 出口被微软边缘拦的问题；
# 不设则直连微软，行为零变化。Worker 只转发到微软 TTS 固定地址，无法被当成通用代理。
# 注意：补丁放在 import 时，因为 edge-tts 每次建连都读取模块级 WSS_URL，
# 这样连接池、回退模式、探针三条路径都会一起走 Worker。
try:
    _TTS_WS_BASE = os.environ.get("TTS_WS_BASE", "").strip().rstrip("/")
    if _TTS_WS_BASE:
        _edge_communicate.WSS_URL = (
            f"{_TTS_WS_BASE}?Ocp-Apim-Subscription-Key={_edge_constants.TRUSTED_CLIENT_TOKEN}"
        )
        _edge_constants.WSS_URL = _edge_communicate.WSS_URL
        print("[INFO] TTS websocket 已切换到 Worker 中转")
except Exception as _e:
    print(f"[WARN] Worker 中转未生效: {_e}")

# ============ 连接复用 + 音频缓存（2026-10-15）============
# v2 接口按 IP 限握手频率：原来 60 个词 = 60 次握手，必撞 429。
# 改法：每条 websocket 建连一次、顺序合成多个词语（2 条连接并行），
# 握手从 60 次降到 2 次；词语去重 + 实例内音频缓存进一步减少请求。
_WORD_AUDIO_CACHE: dict = {}
_WORD_AUDIO_CACHE_MAX = 2000
_POOL_SIZE = 2
# ============================================================

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

class StreamRequest(BaseModel):
    text: str
    voice: str = "zh-CN-XiaoxiaoNeural"
    speed: float = 0.85
    pause_seconds: float = 1.5
    pitch: int = -15
    shuffle_bool: bool = False
    client: str = "web"  # 调用方标识：web / miniprogram，用于日志区分

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


# ================= 代理出口：Vercel 直连被微软封后的备选 =================
# 启用条件：Vercel 环境变量 TTS_PROXY 配了 HTTP 代理地址，格式：
#   http://host:port 或带鉴权 http://user:pass@host:port
# 未配置时 proxy=None，行为与原来完全一致（直连）。
def _tts_proxy() -> Optional[str]:
    return os.environ.get("TTS_PROXY") or None


def _proxy_label(url: str) -> str:
    """日志用：只留 host:port，绝不打用户名密码。"""
    try:
        from urllib.parse import urlparse
        u = urlparse(url)
        return f"{u.hostname}:{u.port}" if u.port else (u.hostname or "proxy")
    except Exception:
        return "proxy"


class _EdgeWS:
    """一条可复用的 edge-tts websocket 连接：握手一次，顺序合成多个词语。

    协议细节直接对齐 edge-tts 7.2.8 的 Communicate.__stream：
    建连 → 发 speech.config → 逐词发 ssml（X-RequestId 区分）→ 收 audio 直到 turn.end。
    """

    def __init__(self, voice: str, rate: str, pitch: str):
        self.voice = voice
        self.rate = rate
        self.pitch = pitch
        self._session = None
        self._ws = None

    def _ws_url(self) -> str:
        from edge_tts.drm import DRM
        # 读取补丁后的 WSS_URL（已切到 v2 接口）
        return (
            f"{_edge_communicate.WSS_URL}"
            f"&ConnectionId={_edge_communicate.connect_id()}"
            f"&Sec-MS-GEC={DRM.generate_sec_ms_gec()}"
            f"&Sec-MS-GEC-Version={_edge_communicate.SEC_MS_GEC_VERSION}"
        )

    async def connect(self) -> None:
        """建连（含 429 退避重试），成功后发送 speech.config。"""
        from edge_tts.drm import DRM
        await self._safe_close()  # 重连时先关掉旧连接，避免泄漏
        for attempt in range(8):
            try:
                self._session = aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(family=socket.AF_INET),
                    trust_env=True,
                    timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=60),
                )
                self._ws = await self._session.ws_connect(
                    self._ws_url(),
                    compress=15,
                    headers=DRM.headers_with_muid(_edge_communicate.WSS_HEADERS),
                    ssl=_edge_communicate._SSL_CTX,
                    proxy=_tts_proxy(),
                )
                await self._ws.send_str(
                    f"X-Timestamp:{_edge_communicate.date_to_string()}\r\n"
                    "Content-Type:application/json; charset=utf-8\r\n"
                    "Path:speech.config\r\n\r\n"
                    '{"context":{"synthesis":{"audio":{"metadataoptions":{'
                    '"sentenceBoundaryEnabled":"false","wordBoundaryEnabled":"true"},'
                    '"outputFormat":"audio-24khz-48kbitrate-mono-mp3"}}}}\r\n'
                )
                return
            except aiohttp.WSServerHandshakeError as e:
                await self._safe_close()
                if e.status != 429 or attempt == 7:
                    print(f"[DEBUG] handshake status={e.status} headers={dict(e.headers)}")
                    raise
                wait = min(2 ** attempt, 8) + random.uniform(0, 0.5)
                print(f"[WARN] 语音连接触发限流，{wait:.1f}s 后重连 (第 {attempt + 1} 次)")
                await asyncio.sleep(wait)

    async def _safe_close(self) -> None:
        try:
            if self._ws is not None:
                await self._ws.close()
        except Exception:
            pass
        try:
            if self._session is not None:
                await self._session.close()
        except Exception:
            pass
        self._ws = None
        self._session = None

    async def close(self) -> None:
        await self._safe_close()

    async def synth(self, word: str) -> bytes:
        """在复用连接上合成一个词语，返回清洗后的 mp3。"""
        from xml.sax.saxutils import escape
        esc = escape(_edge_communicate.remove_incompatible_characters(word))
        ssml = (
            "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'>"
            f"<voice name='{self.voice}'>"
            f"<prosody pitch='{self.pitch}' rate='{self.rate}' volume='+0%'>"
            f"{esc}</prosody></voice></speak>"
        )
        await self._ws.send_str(
            f"X-RequestId:{_edge_communicate.connect_id()}\r\n"
            "Content-Type:application/ssml+xml\r\n"
            f"X-Timestamp:{_edge_communicate.date_to_string()}Z\r\n"
            "Path:ssml\r\n\r\n"
            f"{ssml}"
        )
        audio = bytearray()
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                raw = msg.data.encode("utf-8")
                sep = raw.find(b"\r\n\r\n")
                if sep < 0:
                    continue
                headers, _ = _edge_communicate.get_headers_and_data(raw, sep)
                if headers.get(b"Path") == b"turn.end":
                    break
                # response / turn.start / audio.metadata：忽略
            elif msg.type == aiohttp.WSMsgType.BINARY:
                if len(msg.data) < 2:
                    continue
                hlen = int.from_bytes(msg.data[:2], "big")
                if hlen > len(msg.data):
                    continue
                headers, payload = _edge_communicate.get_headers_and_data(msg.data, hlen)
                if headers.get(b"Path") == b"audio" and payload:
                    audio.extend(payload)
            elif msg.type == aiohttp.WSMsgType.ERROR:
                raise ConnectionError(f"websocket error: {msg.data}")
        return clean_mp3_data(bytes(audio))


async def _debug_handshake_probe(voice: str, rate: str, pitch: str) -> None:
    """调试探针：连接池失败时，用普通 HTTPS GET 请求同一地址，
    看微软对非 websocket 请求回什么（状态 + 前 500 字正文），
    帮助区分是被封、被 WAF 拦，还是其他原因。绝不打印 URL（内含 token）。"""
    try:
        probe_ws = _EdgeWS(voice, rate, pitch)
        https_url = probe_ws._ws_url().replace("wss://", "https://", 1)
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(family=socket.AF_INET),
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as session:
            async with session.get(https_url, proxy=_tts_proxy()) as resp:
                body = await resp.text(errors="replace")
                print(f"[DEBUG] https-probe status={resp.status} body={body[:500]!r}")
    except Exception as e:
        print(f"[DEBUG] https-probe failed: {e}")


async def _synth_with_pool(pending: List[str], voice: str, rate: str, pitch: str,
                           out: dict) -> bool:
    """连接池合成。返回 True=成功；False=探针失败，调用方回退逐词模式。"""
    q: asyncio.Queue = asyncio.Queue()

    async def worker(ws: _EdgeWS):
        while True:
            w = await q.get()
            if w is None:
                q.task_done()
                return
            try:
                audio = await asyncio.wait_for(ws.synth(w), timeout=30)
                if not audio:
                    raise RuntimeError("empty audio")
            except Exception as e:
                print(f"[WARN] 词语 '{w}' 合成异常，重连重试: {e}")
                try:
                    await ws.connect()
                    audio = await asyncio.wait_for(ws.synth(w), timeout=30)
                except Exception as e2:
                    print(f"[ERROR] 获取词语 '{w}' 失败: {e2}")
                    audio = b""
            key = (w, voice, rate, pitch)
            if audio and len(_WORD_AUDIO_CACHE) < _WORD_AUDIO_CACHE_MAX:
                _WORD_AUDIO_CACHE[key] = audio
            out[w] = audio
            q.task_done()

    conns: List[_EdgeWS] = []
    try:
        for _ in range(min(_POOL_SIZE, len(pending))):
            ws = _EdgeWS(voice, rate, pitch)
            await ws.connect()
            conns.append(ws)
        # 探针：先合成第一个词验证协议通路，失败则整体回退
        probe = await asyncio.wait_for(conns[0].synth(pending[0]), timeout=30)
        if not probe:
            raise RuntimeError("probe returned empty audio")
        key = (pending[0], voice, rate, pitch)
        if len(_WORD_AUDIO_CACHE) < _WORD_AUDIO_CACHE_MAX:
            _WORD_AUDIO_CACHE[key] = probe
        out[pending[0]] = probe

        for w in pending[1:]:
            q.put_nowait(w)
        workers = [asyncio.create_task(worker(ws)) for ws in conns]
        for _ in workers:
            q.put_nowait(None)
        await q.join()
        await asyncio.gather(*workers)
        return True
    except Exception as e:
        print(f"[WARN] 连接池模式不可用，回退逐词模式: {e}")
        await _debug_handshake_probe(voice, rate, pitch)
        return False
    finally:
        for ws in conns:
            await ws.close()


async def _synth_legacy(pending: List[str], voice: str, rate: str, pitch: str,
                        out: dict) -> None:
    """回退模式：一词一握手（旧逻辑），带 429 退避重试。"""
    sem = asyncio.Semaphore(3)

    async def fetch_word(word: str) -> bytes:
        for attempt in range(8):
            async with sem:
                try:
                    communicate = edge_tts.Communicate(
                        text=word, voice=voice, rate=rate, pitch=pitch,
                        proxy=_tts_proxy(),
                        connector=aiohttp.TCPConnector(family=socket.AF_INET),
                    )
                    audio_data = bytearray()
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio":
                            audio_data.extend(chunk["data"])
                    return clean_mp3_data(bytes(audio_data))
                except aiohttp.WSServerHandshakeError as e:
                    if e.status != 429 or attempt == 7:
                        print(f"[ERROR] 获取词语 '{word}' 失败: {e}")
                        return b""
                except Exception as e:
                    print(f"[ERROR] 获取词语 '{word}' 失败: {e}")
                    return b""
            wait = min(2 ** attempt, 8) + random.uniform(0, 0.5)
            print(f"[WARN] 词语 '{word}' 触发限流，{wait:.1f}s 后重试 (第 {attempt + 1} 次)")
            await asyncio.sleep(wait)
        return b""

    audios = await asyncio.gather(*[fetch_word(w) for w in pending])
    for w, audio in zip(pending, audios):
        key = (w, voice, rate, pitch)
        if audio and len(_WORD_AUDIO_CACHE) < _WORD_AUDIO_CACHE_MAX:
            _WORD_AUDIO_CACHE[key] = audio
        out[w] = audio

# POST：词语走 JSON 正文，不再拼进 URL，避免词多时 URL 超长被拒（网页版用这个）
@app.post("/api/stream")
async def stream_audio_post(req: StreamRequest):
    return await _build_stream_audio(
        req.text, req.voice, req.speed, req.pause_seconds, req.pitch, req.shuffle_bool,
        req.client,
    )

# GET：保留给小程序旧版调用（参数拼 URL，词多时仍可能超长，小程序建议也改 POST）
# client 默认 miniprogram：当前只有小程序走 GET；如调用方显式传了 client 则以参数为准
@app.get("/api/stream")
async def stream_audio_get(
    text: str,
    voice: str = "zh-CN-XiaoxiaoNeural",
    speed: float = 0.85,
    pause_seconds: float = 1.5,
    pitch: int = -15,
    shuffle_bool: bool = False,
    client: str = "miniprogram",
):
    return await _build_stream_audio(text, voice, speed, pause_seconds, pitch, shuffle_bool, client)


async def _build_stream_audio(
    text: str,
    voice: str,
    speed: float,
    pause_seconds: float,
    pitch: int,
    shuffle_bool: bool,
    client: str = "unknown",
):
    try:
        raw_words = re.split(r'[,，\s、\n\r]+', text)
        words = [w.strip() for w in raw_words if w.strip()]
        if not words:
            raise HTTPException(status_code=400, detail="词语列表为空")
        # 每次请求打一行：Vercel 日志里按 requestId 关联，后面所有 WARN/ERROR 都归到这次请求
        print(f"[INFO] /api/stream client={client} words={len(words)}")
        print(f"[INFO] runtime region={os.environ.get('VERCEL_REGION', 'unknown')}")
        _p = _tts_proxy()
        if _p:
            print(f"[INFO] TTS egress=proxy {_proxy_label(_p)}")
        if shuffle_bool:
            random.shuffle(words)

        speed_rate = f"{int((speed - 1.0) * 100):+d}%"
        pitch_rate = f"{int(pitch):+d}Hz"

        pause_ms = int(pause_seconds * 1000)
        word_gap_ms = pause_ms + 500
        silence_gap = get_silence_mp3(pause_ms)
        word_gap = get_silence_mp3(word_gap_ms)

        # 同一批里重复的词语只抓一次（多选课时时很常见），拼回去时按原顺序
        unique_words = list(dict.fromkeys(words))

        # 缓存过滤：命中缓存的不再请求
        pending: List[str] = []
        audio_map: dict = {}
        for w in unique_words:
            key = (w, voice, speed_rate, pitch_rate)
            hit = _WORD_AUDIO_CACHE.get(key)
            if hit is not None:
                audio_map[w] = hit
            else:
                pending.append(w)

        if pending:
            # 连接池模式：2 条复用连接顺序合成，握手从 N 次降到 2 次；
            # 探针失败则回退到一词一握手的旧模式
            ok = await _synth_with_pool(pending, voice, speed_rate, pitch_rate, audio_map)
            if not ok:
                await _synth_legacy(pending, voice, speed_rate, pitch_rate, audio_map)

        word_audios = [audio_map[w] for w in words]

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
