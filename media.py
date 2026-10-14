"""Shrink pictures and music so they suit a P2K phone before upload."""
# SPDX-License-Identifier: GPL-2.0-or-later
import io
import os
import shutil
import subprocess
import tempfile

SCREEN = (176, 220)  # RAZR V3r main display, width x height
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".heic", ".heif", ".tif", ".tiff", ".webp"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff", ".flac", ".ogg", ".opus"}
MP3_BITRATES = (128, 96, 64, 48, 32)
IMAGE_MODES = ("fit", "fill")


class MediaError(Exception):
    pass


def images_supported():
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def find_ffmpeg():
    # apps started from Finder don't get Homebrew's PATH, so look there too
    for path in (shutil.which("ffmpeg"), "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if path and os.path.exists(path):
            return path
    return None


def _ext(name):
    return os.path.splitext(name)[1].lower()


def image_for_screen(data, name, mode="fit", quality=85):
    """Return (data, name) resized for the phone screen, as a baseline JPEG.

    mode "fit":  the whole picture fits inside 176x220 (only ever shrinks)
    mode "fill": scaled and centre-cropped to exactly 176x220, for wallpapers
    Animated GIFs are returned unchanged.
    """
    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass

    try:
        im = Image.open(io.BytesIO(data))
        if getattr(im, "is_animated", False):
            return data, name
        im = ImageOps.exif_transpose(im)
    except Exception:
        raise MediaError("Can't read %s: not a picture, or a format that isn't supported" % name)

    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.getchannel("A"))
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")

    small_already = im.width <= SCREEN[0] and im.height <= SCREEN[1]
    if mode == "fill":
        im = ImageOps.fit(im, SCREEN, Image.LANCZOS)
    else:
        im.thumbnail(SCREEN, Image.LANCZOS)

    out = io.BytesIO()
    im.save(out, "JPEG", quality=quality, optimize=True)  # baseline: old phones can't do progressive
    out = out.getvalue()
    if mode == "fit" and small_already and _ext(name) in (".jpg", ".jpeg") and len(out) >= len(data):
        return data, name
    return out, os.path.splitext(name)[0] + ".jpg"


def _duration(ffmpeg, path):
    ffprobe = os.path.join(os.path.dirname(ffmpeg), "ffprobe")
    if not os.path.exists(ffprobe):
        return None
    r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, timeout=60)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def mp3_for_phone(data, name, kbps=64, on_progress=None):
    """Return (data, name) re-encoded as MP3 at kbps (44.1 kHz; mono at 48 kbps and below).

    Cover art is dropped, tags are kept as ID3v2.3. If the source is already an MP3
    that would only get bigger, it is returned unchanged.
    on_progress(fraction) is called while encoding when the duration is known.
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise MediaError("MP3 compression needs ffmpeg: brew install ffmpeg")
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in" + _ext(name))
        dst = os.path.join(tmp, "out.mp3")
        with open(src, "wb") as f:
            f.write(data)
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", src,
               "-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", "%dk" % kbps, "-ar", "44100",
               "-id3v2_version", "3"]
        if kbps <= 48:
            cmd += ["-ac", "1"]
        duration = _duration(ffmpeg, src) if on_progress else None
        cmd += ["-progress", "pipe:1", "-nostats", dst]
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as p:
            for line in p.stdout:
                # out_time_us is microseconds of audio encoded so far
                if duration and line.startswith("out_time_us="):
                    try:
                        on_progress(min(int(line.split("=", 1)[1]) / 1e6 / duration, 1.0))
                    except ValueError:
                        pass
            err = p.stderr.read()
        if p.returncode != 0 or not os.path.exists(dst):
            msg = (err.strip().splitlines() or ["unknown error"])[-1]
            raise MediaError("Can't convert %s: %s" % (name, msg))
        with open(dst, "rb") as f:
            out = f.read()
    if _ext(name) == ".mp3" and len(out) >= len(data):
        return data, name
    return out, os.path.splitext(name)[0] + ".mp3"


def kind(name):
    """'picture', 'music', 'video' or None, from the file extension."""
    ext = _ext(name)
    if ext in IMAGE_EXT:
        return "picture"
    if ext in AUDIO_EXT or ext in (".mid", ".midi", ".amr"):
        return "music"
    if ext in (".3gp", ".mp4"):
        return "video"
    return None


def prepare(data, name, image_mode=None, mp3_kbps=None, on_progress=None):
    """Apply the requested conversions to one file; other files pass through."""
    ext = _ext(name)
    if image_mode and ext in IMAGE_EXT:
        return image_for_screen(data, name, image_mode)
    if mp3_kbps and ext in AUDIO_EXT:
        return mp3_for_phone(data, name, mp3_kbps, on_progress)
    return data, name
