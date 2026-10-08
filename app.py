import os
import shutil
import uuid
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://cakedreamr.com",
        "https://www.cakedreamr.com"
    ],
    allow_credentials=False,
    allow_methods=["POST", "GET"],
    allow_headers=["*"]
)


DOWNLOAD_DIR = Path("/tmp/downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_DOWNLOAD_SIZE = 500 * 1024 * 1024


class DownloadRequest(BaseModel):
    url: str
    format: str = "mp4"


def cleanup_job(job_dir):
    shutil.rmtree(
        job_dir,
        ignore_errors=True
    )


def find_output_file(job_dir):
    files = [
        file
        for file in job_dir.iterdir()
        if file.is_file()
    ]

    if not files:
        return None

    return files[0]


@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "CakeDreamr Video Downloader"
    }


@app.post("/download")
async def download_video(request: DownloadRequest):

    if request.format not in {"mp4", "gif"}:
        raise HTTPException(
            status_code=400,
            detail="Format must be mp4 or gif."
        )

    url = request.url.strip()

    if not url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="Invalid URL."
        )

    job_id = uuid.uuid4().hex

    job_dir = DOWNLOAD_DIR / job_id
    job_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    try:

        if request.format == "mp4":

            output_template = str(
                job_dir /
                "%(title).150B.%(ext)s"
            )

            command = [
                "yt-dlp",
                "--js-runtimes",
                "deno",
                "--extractor-args",
                "youtube:player_client=web,android_vr,tv_downgraded",
                "--no-playlist",
                "--max-filesize",
                "500M",
                "--merge-output-format",
                "mp4",
                "-f",
                "bv*+ba/b",
                "-o",
                output_template,
                url
            ]

        else:

            video_template = str(
                job_dir /
                "source.%(ext)s"
            )

            command = [
                "yt-dlp",
                "--js-runtimes",
                "deno",
                "--extractor-args",
                "youtube:player_client=web,android_vr,tv_downgraded",
                "--no-playlist",
                "--max-filesize",
                "500M",
                "-f",
                "bv*+ba/b",
                "-o",
                video_template,
                url
            ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300
        )

        if result.returncode != 0:

            error = result.stderr[-3000:]

            cleanup_job(job_dir)

            raise HTTPException(
                status_code=500,
                detail=error
            )

        source_file = find_output_file(
            job_dir
        )

        if source_file is None:

            cleanup_job(job_dir)

            raise HTTPException(
                status_code=500,
                detail="yt-dlp did not produce a file."
            )

        if source_file.stat().st_size > MAX_DOWNLOAD_SIZE:

            cleanup_job(job_dir)

            raise HTTPException(
                status_code=413,
                detail="The downloaded file is too large."
            )

        if request.format == "gif":

            gif_file = (
                job_dir /
                "download.gif"
            )

            ffmpeg = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(source_file),
                    "-vf",
                    "fps=15,scale=720:-1:flags=lanczos",
                    "-loop",
                    "0",
                    str(gif_file)
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=300
            )

            if ffmpeg.returncode != 0:

                cleanup_job(job_dir)

                raise HTTPException(
                    status_code=500,
                    detail=ffmpeg.stderr[-3000:]
                )

            source_file.unlink(
                missing_ok=True
            )

            source_file = gif_file

        if source_file.stat().st_size > MAX_DOWNLOAD_SIZE:

            cleanup_job(job_dir)

            raise HTTPException(
                status_code=413,
                detail="The converted file is too large."
            )

        return FileResponse(
            path=str(source_file),
            filename=source_file.name,
            media_type=(
                "image/gif"
                if request.format == "gif"
                else "video/mp4"
            ),
            background=BackgroundTask(
                cleanup_job,
                job_dir
            )
        )

    except subprocess.TimeoutExpired:

        cleanup_job(job_dir)

        raise HTTPException(
            status_code=504,
            detail="Download timed out."
        )

    except HTTPException:

        raise

    except Exception as error:

        cleanup_job(job_dir)

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )
