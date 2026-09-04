from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import yt_dlp
import requests
import re
import os

app = FastAPI(title="OrangeTube Downloader 🍊", version="1.0")

# CORS abierto para orange-tube.vercel.app
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# yt-dlp con clientes Android/iOS (menos "not a bot")
YDL_BASE = {
    "quiet": True,
    "no_warnings": True,
    "noprogress": True,
    "noplaylist": True,
    "geo_bypass": True,
    "socket_timeout": 30,
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "ios", "tv_embedded"],
        }
    },
    "http_headers": {
        "User-Agent": "com.google.android.youtube/19.29.37 (Linux; U; Android 14) gzip",
        "Accept-Language": "en-US,en;q=0.9",
    },
}

# Cookies opcionales (Render Secret File)
for path in (
    os.environ.get("YTDLP_COOKIES", ""),
    "/etc/secrets/cookies.txt",
    os.path.join(os.path.dirname(__file__), "cookies.txt"),
):
    if path and os.path.isfile(path):
        YDL_BASE["cookiefile"] = path
        print("✅ cookies:", path)
        break


def clean_id(video_id: str) -> str:
    video_id = (video_id or "").strip()
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", video_id)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return video_id
    raise HTTPException(status_code=400, detail="video_id inválido")


def opts(**extra):
    return {**YDL_BASE, **extra}


def safe_name(title: str, fallback: str = "file") -> str:
    t = re.sub(r'[\\/:*?"<>|]+', "_", title or fallback)
    return (t[:80] or fallback)


@app.get("/")
def root():
    return {
        "status": "OrangeTube Downloader Online 🍊",
        "download": "/download/VIDEO_ID?type=video&quality=720",
        "audio": "/download/VIDEO_ID?type=audio&quality=best",
        "cookies": bool(YDL_BASE.get("cookiefile")),
    }


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/download/{video_id}")
def download(
    video_id: str,
    type: str = Query("video"),
    quality: str = Query("720"),
):
    """
    Devuelve el archivo real (bytes) con CORS abierto.
    type = video | audio
    quality = 360 | 720 | 1080 | best
    """
    video_id = clean_id(video_id)
    url = f"https://www.youtube.com/watch?v={video_id}"
    type = (type or "video").lower().strip()
    quality = (quality or "720").lower().strip()

    if type not in ("video", "audio"):
        type = "video"

    try:
        if type == "audio":
            fmt = "bestaudio[ext=m4a]/bestaudio/best"
            ext = "m4a"
            mime = "audio/mp4"
        else:
            h = 720
            if quality in ("360", "480", "720", "1080"):
                h = int(quality)
            # Un solo archivo con audio + video si se puede
            fmt = (
                f"best[height<={h}][ext=mp4][vcodec!=none][acodec!=none]/"
                f"best[height<={h}][vcodec!=none][acodec!=none]/"
                f"best[height<={h}]/"
                f"best"
            )
            ext = "mp4"
            mime = "video/mp4"

        with yt_dlp.YoutubeDL(opts(format=fmt, skip_download=True)) as ydl:
            info = ydl.extract_info(url, download=False)
            stream_url = info.get("url")
            title = safe_name(info.get("title"), video_id)

            if not stream_url:
                for f in reversed(info.get("formats") or []):
                    if not f.get("url"):
                        continue
                    vc = f.get("vcodec") or "none"
                    ac = f.get("acodec") or "none"
                    if type == "audio" and vc == "none" and ac != "none":
                        stream_url = f["url"]
                        break
                    if type == "video" and vc != "none" and ac != "none":
                        stream_url = f["url"]
                        break
                if not stream_url:
                    for f in reversed(info.get("formats") or []):
                        if f.get("url"):
                            stream_url = f["url"]
                            break

            if not stream_url:
                raise HTTPException(status_code=404, detail="No hay stream para este video")

        def generate():
            headers = {
                "User-Agent": YDL_BASE["http_headers"]["User-Agent"],
                "Referer": "https://www.youtube.com/",
                "Accept-Language": "en-US,en;q=0.9",
            }
            with requests.get(stream_url, stream=True, headers=headers, timeout=180) as r:
                r.raise_for_status()
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        yield chunk

        return StreamingResponse(
            generate(),
            media_type=mime,
            headers={
                "Content-Disposition": f'attachment; filename="{title}.{ext}"',
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Expose-Headers": "Content-Disposition, Content-Type",
                "Cache-Control": "no-store",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        if "not a bot" in msg.lower() or "Sign in to confirm" in msg:
            raise HTTPException(
                status_code=503,
                detail=(
                    "YouTube bot-check. Sube cookies.txt como Secret File "
                    "en /etc/secrets/cookies.txt o prueba otro video."
                ),
            )
        raise HTTPException(status_code=500, detail=f"download error: {msg[:400]}")
