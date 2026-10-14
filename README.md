# Moto P2K

Transfer photos, ringtones, videos and other files between a Mac and a Motorola
P2K phone (RAZR V3r and friends) over a plain USB cable, with no memory card and
no Windows-only tools.

It's a small Mac app with a file browser. You can:

- drag files onto the phone, with live progress;
- shrink pictures to the 176×220 screen, re-encode music as small MP3s and convert
  any video to a 3GP the phone plays, all on the way;
- download photos (one at a time or as a zip);
- preview images, including the phone's own `.emp` and `.ems` pictures, and play
  the phone's 3GP videos in the browser;
- delete files and see free space.

A command-line tool covers scripting.

Tested with a **Motorola RAZR V3r** (firmware `R4515_G_08.BD.B3R`) on Apple Silicon macOS.

## Requirements

- macOS with [Homebrew](https://brew.sh)
- `libusb`: `brew install libusb`
- Python **3.10 or newer**, e.g. `brew install python`. The `/usr/bin/python3` that
  ships with macOS is 3.9 and is too old for Pillow 12.
- `ffmpeg`, optional, for MP3/video conversion and video previews: `brew install ffmpeg`
- `opencore-amr`, optional, for video conversion: `brew install opencore-amr`. Homebrew's
  ffmpeg can't encode AMR, the sound format the phone's videos use, so the app
  calls this library directly.
- A USB data cable for the phone

## Install the app

```sh
./make_app.sh
```

This builds `~/Applications/Moto P2K.app`, with its own virtualenv (pyusb, Pillow,
pillow-heif) inside the bundle. The virtualenv uses whichever `python3` is first on
your `PATH`, so keep that Python installed.

Open the app from Spotlight or Finder. It starts a local server on `127.0.0.1:8765`
and opens the UI in your default browser. Re-run `./make_app.sh` after changing
the code.

## Using the app

1. **Connect.** Plug in the phone. The app switches it to P2K mode on its own
   (see [How it works](#how-it-works)).
2. **Browse.** The sidebar lists the phone's folders and the free space on each
   volume. *Places* has shortcuts to **Photos** (`/c/mobile/picture`), **Videos**,
   **Ringtones** and **Java games**. Tick *Show system files* to see the `/a` volume
   too. The header shows the free space on the open volume, in orange below 300 KB.
3. **Preview.** Click a picture to see it full size. JPEG, GIF, PNG and BMP work, as
   do the phone's own `.emp` files (GIF animations) and `.ems` files (EMS
   bitmaps, converted to PNG for display). Phone videos (`.3gp`, `.mp4`) get a
   poster frame in the grid. To save USB time, thumbnails load only for pictures
   under 400 KB and videos under 1.5 MB. Clicking one plays it: browsers can't play the phone's
   MPEG-4/AMR video, so the app converts it to H.264/AAC on the fly. The poster and
   the player share one read from the phone. Downloads always give the original file.
4. **Phone → Mac.** Select files and click **Download**. One file is saved as it
   is; several files come as `V3r_files.zip`. Both go to your browser's Downloads
   folder.
5. **Mac → phone.** Drag files into the window or click **Upload to phone…**.
   - **Where files go.** With *Put into Photos / Ringtones / Videos by type* ticked
     (the default), pictures go to `mobile/picture`, music to `mobile/audio` and
     phone videos (`.3gp`, `.mp4`) to `mobile/video`, whichever folder is open.
     Sorting looks at the file as it is uploaded, after any conversion. A `.mov` or
     `.mkv` uploaded unconverted is not a phone video, so it stays in the open folder. The phone only lists
     ringtones from the audio folder. Files stay on the open folder's volume (phone
     or memory card). Anything else, or everything when the box is unticked, goes
     into the open folder.
   - **Progress.** Each file shows a progress bar through three stages: sending to
     the app, compressing, then writing to the phone with speed and time left.
     Writing runs at about 30 KB/s, so a 1.4 MB MP3 takes just under a minute. If a
     file fails, the rest are skipped and the log says how many were sent.
   - **Names.** The phone only accepts Latin file names, so names are
     transliterated (`Фото.jpg` → `Foto.jpg`). Existing files are never
     overwritten: a clashing name gets a suffix (`Foto_1.jpg`). Files bigger than
     the free space are refused before anything is written.
6. **Compression on upload.** Choose it in the *When uploading* bar; the choice is
   remembered.
   - **Pictures.**
     - *Fit screen 176×220* shrinks a photo to fit the V3r display.
     - *Wallpaper 176×220* scales and centre-crops to fill the screen exactly.
     - Either way the result is a baseline JPEG, and the EXIF rotation is applied.
     - HEIC, PNG, WebP, TIFF and BMP are converted too. Animated GIFs are left as
       they are.
     - A 3 MB phone photo ends up at about 7–15 KB.
   - **Music.** *Compress MP3 to N kbps* re-encodes to 44.1 kHz MP3.
     - At 48 kbps and below the output is mono.
     - Cover art is dropped; the tags are kept.
     - M4A, AAC, WAV, AIFF, FLAC and OGG become MP3 too.
     - An MP3 that would only get bigger is uploaded unchanged.
     - Needs ffmpeg.
   - **Videos.** *Convert to 3GP* turns MP4, MOV, MKV, AVI, WebM and others into
     what the phone records itself: 3GP, MPEG-4 Simple Profile 176×144, AMR-NB
     12.2 kbps 8 kHz mono.
     - *small* targets 64 kbps at 12 fps, about 0.8 MB per minute in practice.
     - *normal* targets 128 kbps at 15 fps, about 1.3 MB per minute in practice.
     - The picture is letterboxed to keep its shape; rotation metadata is applied.
     - A file that already is a phone-ready 3GP (MPEG-4 or H.263 at up to 176×144,
       with AMR or no sound) is uploaded unchanged, not re-encoded.
     - A video with a sound track that can't be converted is refused, never
       uploaded silent. A file with no video track is refused too.
     - Needs ffmpeg and opencore-amr.

   The transfer log shows each file's size before and after.
7. **Quit.** **Quit** stops the app. It also stops on its own 10 minutes after the
   browser tab is closed.

The app listens only on `127.0.0.1`. It rejects requests with a foreign `Host` or
`Origin`, and every state-changing request must carry an `X-P2K` header. This
stops other websites open in the browser from touching the phone.

## Command line

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

./p2k ls [filter]            # list files (filter = path substring, e.g. picture)
./p2k get <path> [dest]      # download one file
./p2k pull <filter> <dir>    # download every file whose path contains filter
./p2k put <file> <path>      # upload (path ending in / keeps the file name)
    [--fit | --wallpaper]    #   resize pictures to the 176x220 screen
    [--mp3 KBPS]             #   re-encode music as MP3 (128/96/64/48/32)
    [--video small|normal]   #   convert video to 3GP 176x144 (MPEG-4 + AMR)
./p2k df                     # free space per volume
./p2k volumes                # list volumes
./p2k mode                   # just switch the phone to P2K mode
```

`put` transliterates the target file name like the app does. When a conversion
changes the format, `put` also fixes the extension.

Example: back up all photos:

```sh
./p2k pull /c/mobile/picture/ ~/Desktop/V3r_photos
```

Example: put a wallpaper and a ringtone on the phone:

```sh
./p2k put --wallpaper ~/Pictures/IMG_1234.HEIC /c/mobile/picture/
./p2k put --mp3 64 ~/Music/song.m4a /c/mobile/audio/
./p2k put --video small ~/Movies/clip.mov /c/mobile/video/
```

## Files

| File           | What it is                                                    |
|----------------|---------------------------------------------------------------|
| `p2k.py`       | P2K protocol, file-name rules and the CLI                     |
| `media.py`     | Conversions, AMR encoding, video previews, EMS decoding       |
| `p2k_app.py`   | Local HTTP server behind the app                              |
| `p2k_ui.html`  | The app's UI                                                  |
| `make_app.sh`  | Builds `~/Applications/Moto P2K.app`                          |
| `p2k`          | CLI wrapper that runs `p2k.py` from `.venv`                   |

## How it works

When the phone is plugged in normally, it shows up as a USB modem
(`22b8:4902`, `/dev/cu.usbmodem*`). The tool sends `AT+MODE=8` to that port. The
phone then re-enumerates as `22b8:4901` and exposes Motorola's vendor-specific
*Test Command* interface (interface 8). File access uses the P2K protocol over
USB control transfers on that interface:

| FSAC sub-command | Meaning                                  |
|------------------|------------------------------------------|
| `0x00`           | open file (with attribute)               |
| `0x01` / `0x02`  | read / write (up to 1 KB per packet)     |
| `0x04`           | close                                    |
| `0x05`           | delete                                   |
| `0x07` / `0x08`  | search request / fetch file-list records |
| `0x0A`           | list volumes                             |
| `0x0B`           | free space of a volume                   |

The phone has these volumes:

- `/a`: system files
- `/c`: user files (photos, ringtones, video, Java)
- `/b`: TransFlash card, if one is inserted

Searching a specific volume takes a UTF-16BE pattern such as `/c/` `FFFE` `*`,
padded to 252 bytes. The free-space query takes two zero bytes followed by the
UTF-16BE volume name (`/c`). The volume list comes back as UTF-16BE names
separated by `U+FFFE`.

Reply type `0x60` means success. `0x70` means the request was rejected, for example
because the file or folder doesn't exist. `0x80` carries data, and its first byte is
a status (`0` = OK).

Videos the phone records, like the bundled `HELLOMOTO.3gp`, are 3GP files with
MPEG-4 Part 2 Simple Profile video at 176×144 and 15 fps (about 130 kbps), plus
AMR-NB audio at 8 kHz mono and 12.2 kbps. The converter produces the same format.

`.ems` pictures are 3 header bytes (`04`, width, height), then 6 bits per pixel
(2 bits each for R, G and B), packed MSB first. The stock ones are 32×32.

Unplugging the cable puts the phone back into normal mode.

## Known issues

- Once during early testing, the phone stopped answering on USB right after a delete.
  It didn't happen again with the current code. Unplugging and replugging the
  cable fixes it. The app detects this case and asks you to replug.
- USB 1.1 full speed plus a 1 KB-per-request protocol makes transfers slow:
  about 30 KB/s when writing to a V3r, and somewhat faster when reading.
- Only tested on the V3r, and without a memory card. Other P2K phones (V3, V3i,
  E398, L7, …) use the same protocol, but their USB IDs or volume layout may differ.

## Credits

The protocol details come from these GPL projects:

- [moto4lin / p2kmoto](https://sourceforge.net/projects/moto4lin/) by Dmitry Nezhevenko
- [p2kc / p2k-core](https://github.com/pulkomandy/p2kc)

## License

GPL-2.0-or-later, the same license as the projects the protocol comes from.
See [LICENSE](LICENSE).
