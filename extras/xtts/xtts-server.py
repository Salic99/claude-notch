#!/usr/bin/env python3
"""Claude Notch — a small local XTTS v2 server for the speech feature.

Keeps Coqui's XTTS v2 loaded on the GPU and answers on 127.0.0.1:5117:
    GET /say?text=…[&lang=cs|en|…][&speaker=Name|&speaker_wav=/path.wav][&speed=1.1]
           [&temperature=0.65&repetition_penalty=2.5&top_p=0.85&top_k=50]           → audio/wav
    (speaker_wav may be several paths joined with ":" — more reference audio, steadier clone)
    GET /health                                                                     → "ok"
Point the notch at it:  [speech] synth = "curl -sG http://127.0.0.1:5117/say --data-urlencode text={text} -o {file}"

Defaults come from the environment: XTTS_SPEAKER (a built-in speaker name),
XTTS_SPEAKER_WAV (6+ s of someone's voice to clone), XTTS_SPEED, XTTS_PORT.
Run it as a user service (extras/xtts/claude-notch-xtts.service) — loading the
model takes a while, so it must stay up between sentences.
"""
import io
import json
import os
import re
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("COQUI_TOS_AGREED", "1")

PORT = int(os.environ.get("XTTS_PORT", "5117"))
SPEAKER = os.environ.get("XTTS_SPEAKER", "Ana Florence")
SPEAKER_WAV = os.environ.get("XTTS_SPEAKER_WAV", "")
SPEED = float(os.environ.get("XTTS_SPEED", "1.0"))
CZECH = re.compile(r"[ěščřžýáíéůúďťňĚŠČŘŽÝÁÍÉŮÚĎŤŇ]")


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S ") + msg, flush=True)


log("loading XTTS v2 …")
import torch  # noqa: E402
from TTS.api import TTS  # noqa: E402

device = "cuda" if torch.cuda.is_available() else "cpu"
tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
log(f"ready on {device}; speakers: {', '.join(list(tts.synthesizer.tts_model.speaker_manager.speakers)[:8])} …")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):      # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str = "text/plain") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/health":
            return self._send(200, b"ok")
        if u.path != "/say":
            return self._send(404, b"no")
        text = (q.get("text") or "").strip()
        if not text:
            return self._send(400, b"text?")
        lang = q.get("lang") or ("cs" if CZECH.search(text) else "en")
        speaker_wav = q.get("speaker_wav") or SPEAKER_WAV
        if ":" in speaker_wav:
            speaker_wav = [w for w in speaker_wav.split(":") if w]
        speaker = q.get("speaker") or SPEAKER
        speed = float(q.get("speed") or SPEED)
        # sampling: lower temperature = steadier, less expressive; repetition_penalty > 1 curbs stutters
        tune = {k: float(q[k]) for k in ("temperature", "repetition_penalty", "top_p", "length_penalty") if q.get(k)}
        if q.get("top_k"):
            tune["top_k"] = int(q["top_k"])
        t0 = time.time()
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                out = f.name
            kw = {"speaker_wav": speaker_wav} if speaker_wav else {"speaker": speaker}
            tts.tts_to_file(text=text, language=lang, file_path=out, speed=speed, split_sentences=True, **kw, **tune)
            with open(out, "rb") as f:
                data = f.read()
            os.unlink(out)
        except Exception as e:                       # noqa: BLE001
            log(f"error: {e}")
            return self._send(500, str(e).encode())
        log(f"{lang} {len(text)} chars → {len(data) // 48000:.0f} s in {time.time() - t0:.2f} s")
        self._send(200, data, "audio/wav")


HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
