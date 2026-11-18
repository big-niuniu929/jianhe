# -*- coding: utf-8 -*-
"""本地语音转写（faster-whisper）

设计：
- 模型懒加载、进程内复用（默认 base；可用 ZHILIAN_WHISPER_MODEL 换 tiny/small）。
- 只接受 WAV（浏览器端已经重采样成 16k 单声道），避免依赖 ffmpeg/av 解码链。
- 转写失败返回结构化错误，前端降级文字输入，不丢已答内容。
"""
import io
import os
import threading
import wave

import numpy as np

MODEL_SIZE = os.environ.get("ZHILIAN_WHISPER_MODEL", "base")
INITIAL_PROMPT = (
    "以下是求职面试的普通话回答，请用简体中文转写。"
    "涉及电商运营、电商平台店铺、SKU、价格巡检、客服质检、提示词工程等词。"
)

_model = None
_lock = threading.Lock()


def get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from faster_whisper import WhisperModel

                _model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
    return _model


def _read_wav(data: bytes):
    with wave.open(io.BytesIO(data), "rb") as w:
        channels = w.getnchannels()
        rate = w.getframerate()
        width = w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"只支持 16-bit PCM WAV（当前 {width*8} bit）")
    arr = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        arr = arr.reshape(-1, channels).mean(axis=1)
    if rate != 16000:
        # 简单线性重采样到 16k（浏览器端正常已是 16k，这里兜底）
        n = int(round(len(arr) * 16000 / rate))
        arr = np.interp(np.linspace(0, len(arr) - 1, n), np.arange(len(arr)), arr).astype(np.float32)
    return arr


def transcribe_wav(data: bytes, hotwords: str = "") -> dict:
    audio = _read_wav(data)
    if len(audio) < 1600:  # <0.1s
        return {"ok": False, "error": "录音太短（<0.1 秒）"}
    model = get_model()
    segments, info = model.transcribe(
        audio,
        language="zh",
        beam_size=5,
        vad_filter=True,
        initial_prompt=INITIAL_PROMPT,
        hotwords=hotwords or None,
        condition_on_previous_text=False,
    )
    segs = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segments]
    text = "".join(s["text"] for s in segs).strip()
    return {
        "ok": True,
        "text": text,
        "language": info.language,
        "language_probability": round(info.language_probability, 3),
        "duration": round(len(audio) / 16000, 2),
        "model": MODEL_SIZE, "stt_rev": "hot-v1",
        "segments": segs,
    }
