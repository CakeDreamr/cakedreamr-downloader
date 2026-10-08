import shutil
import uuid
import subprocess
import json
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
DOWNLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True
)

MAX_DOWNLOAD_SIZE = 500 * 1024 * 1024


class DownloadRequest(BaseModel):
    url: str
    format: str = "auto"


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


def get_base_name(filename):
    return Path(filename).stem


def get_media_type(filename):
    extension = Path(filename).suffix.lower()

    if extension == ".gif":
        return "image/gif"

    if extension == ".webm":
        return "video/webm"

    if extension == ".mov":
        return "video/quicktime"

    if extension == ".mkv":
        return "video/x-matroska"

    if extension == ".avi":
        return "video/x-msvideo"

    return "video/mp4"


def is_x_url(url):
    lowered = url.lower()

    return (
        "x.com/" in lowered
        or
        "twitter.com/" in lowered
    )


def x_post_is_gif(metadata):
    if not isinstance(metadata, dict):
        return False

    entries = [
        metadata
    ]

    while entries:

        current = entries.pop()

        if isinstance(current, dict):

            for key, value in current.items():

                key_lower = str(key).lower()

                if key_lower in {
                    "type",
                    "media_type",
                    "content_type",
                    "media_type_string"
                }:

                    if (
                        isinstance(value, str)
                        and
                        value.lower() in {
                            "animated_gif",
                            "animated gif",
                            "gif"
                        }
                    ):
                        return True

                if isinstance(value, (dict, list)):
                    entries.append(value)

        elif isinstance(current, list):

            for value in current:

                if isinstance(value, (dict, list)):
                    entries.append(value)

    return False


def get_metadata(url):
    command = [
        "yt-dlp",
        "--js-runtimes",
        "deno",
        "--no-playlist",
        "--dump-single-json",
        "--skip-download",
        url
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120
    )

    if result.returncode != 0:
        return None

    try:
        return json.loads(
            result.stdout
        )

    except json.JSONDecodeError:
        return None


def convert_to_gif(source_file, output_file):
    return subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source_file),
            "-vf",
            "fps=15,scale=720:-1:flags=lanczos",
            "-loop",
            "0",
            str(output_file)
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=300
    )


def convert_to_mp4(source_file, output_file):
    return subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source_file),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_file)
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=300
    )


@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "CakeDreamr Video Downloader"
    }


@app.post("/download")
async def download_video(request: DownloadRequest):

    if request.format not in {
        "auto",
        "mp4",
        "gif"
    }:
        raise HTTPException(
            status_code=400,
            detail="Format must be auto, mp4 or gif. dumbass"
        )

    url = request.url.strip()

    if not url.startswith(
        ("http://", "https://")
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid URL."
        )

    job_id = uuid.uuid4().hex

    job_dir = (
        DOWNLOAD_DIR /
        job_id
    )

    job_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    try:

        detected_x_gif = False

        if (
            request.format == "auto"
            and
            is_x_url(url)
        ):

            metadata = get_metadata(
                url
            )

            if metadata is not None:

                detected_x_gif = (
                    x_post_is_gif(
                        metadata
                    )
                )

        should_make_gif = (
            request.format == "gif"
            or
            (
                request.format == "auto"
                and
                detected_x_gif
            )
        )

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
            "-f",
            "bv*+ba/b",
            "-o",
            output_template,
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

            cleanup_job(
                job_dir
            )

            raise HTTPException(
                status_code=500,
                detail=error
            )

        source_file = (
            find_output_file(
                job_dir
            )
        )

        if source_file is None:

            cleanup_job(
                job_dir
            )

            raise HTTPException(
                status_code=500,
                detail="yt-dlp did not produce a file."
            )

        if (
            source_file.stat().st_size
            >
            MAX_DOWNLOAD_SIZE
        ):

            cleanup_job(
                job_dir
            )

            raise HTTPException(
                status_code=413,
                detail="The downloaded file is too large."
            )

        source_extension = (
            source_file.suffix.lower()
        )

        if should_make_gif:

            output_name = (
                get_base_name(
                    source_file.name
                )
                +
                ".gif"
            )

            gif_file = (
                job_dir /
                output_name
            )

            ffmpeg = convert_to_gif(
                source_file,
                gif_file
            )

            if ffmpeg.returncode != 0:

                cleanup_job(
                    job_dir
                )

                raise HTTPException(
                    status_code=500,
                    detail=ffmpeg.stderr[-3000:]
                )

            source_file.unlink(
                missing_ok=True
            )

            source_file = gif_file

        elif request.format == "mp4":

            if source_extension != ".mp4":

                output_name = (
                    get_base_name(
                        source_file.name
                    )
                    +
                    ".mp4"
                )

                mp4_file = (
                    job_dir /
                    output_name
                )

                ffmpeg = convert_to_mp4(
                    source_file,
                    mp4_file
                )

                if ffmpeg.returncode != 0:

                    cleanup_job(
                        job_dir
                    )

                    raise HTTPException(
                        status_code=500,
                        detail=ffmpeg.stderr[-3000:]
                    )

                source_file.unlink(
                    missing_ok=True
                )

                source_file = mp4_file

        else:

            if source_extension == ".gif":

                pass

            elif source_extension != ".mp4":

                output_name = (
                    get_base_name(
                        source_file.name
                    )
                    +
                    ".mp4"
                )

                mp4_file = (
                    job_dir /
                    output_name
                )

                ffmpeg = convert_to_mp4(
                    source_file,
                    mp4_file
                )

                if ffmpeg.returncode != 0:

                    cleanup_job(
                        job_dir
                    )

                    raise HTTPException(
                        status_code=500,
                        detail=ffmpeg.stderr[-3000:]
                    )

                source_file.unlink(
                    missing_ok=True
                )

                source_file = mp4_file

        if (
            source_file.stat().st_size
            >
            MAX_DOWNLOAD_SIZE
        ):

            cleanup_job(
                job_dir
            )

            raise HTTPException(
                status_code=413,
                detail="The converted file is too large."
            )

        return FileResponse(
            path=str(source_file),
            filename=source_file.name,
            media_type=get_media_type(
                source_file.name
            ),
            background=BackgroundTask(
                cleanup_job,
                job_dir
            )
        )

    except subprocess.TimeoutExpired:

        cleanup_job(
            job_dir
        )

        raise HTTPException(
            status_code=504,
            detail="Download timed out."
        )

    except HTTPException:

        raise

    except Exception as error:

        cleanup_job(
            job_dir
        )

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )
