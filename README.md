# Moto P2K

Transfer photos, ringtones, videos and other files between a Mac and a Motorola
P2K phone (RAZR V3r and friends) over a plain USB cable, with no memory card and
no Windows-only tools.

It's a small Mac app with a file browser. You can drag and drop files onto the
phone, download photos (one at a time or as a zip), view images and delete files.
A command-line tool covers scripting.

Tested with a **Motorola RAZR V3r** (firmware `R4515_G_08.BD.B3R`) on Apple Silicon macOS.

## Requirements

- macOS with [Homebrew](https://brew.sh)
- `libusb`: `brew install libusb`
- Python 3.9+ (the system `python3` works)
- A USB data cable for the phone

## Install the app

```sh
./make_app.sh
```

This builds `~/Applications/Moto P2K.app`, with its own virtualenv inside the
bundle. Open it from Spotlight or Finder. It starts a local server on
`127.0.0.1:8765` and opens the UI in your default browser.

Re-run `./make_app.sh` after changing the code.

## Using the app

1. Plug in the phone. The app switches it to P2K mode on its own (see below).
2. The sidebar lists the phone's folders. The *Places* section has shortcuts to
   **Photos** (`/c/mobile/picture`), **Videos**, **Ringtones** and **Java games**.
   Tick *Show system files* to see the `/a` volume too.
3. **Phone → Mac:** select files and click **Download**. One file is saved as-is;
   several files come as `V3r_files.zip`. Both go to your browser's Downloads folder.
4. **Mac → phone:** drag files into the window or click **Upload to phone…**.
   They go into the folder that's open. The phone only accepts Latin file names,
   so names are transliterated (`Фото.jpg` → `Foto.jpg`). Existing files are never
   overwritten: a clashing name gets a suffix (`Foto_1.jpg`).
5. Click an image to preview it.
6. **Quit** stops the app. It also stops on its own 10 minutes after the browser
   tab is closed.

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
./p2k mode                   # just switch the phone to P2K mode
```

Example: back up all photos:

```sh
./p2k pull /c/mobile/picture/ ~/Desktop/V3r_photos
```

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

The phone has these volumes:

- `/a`: system files
- `/c`: user files (photos, ringtones, video, Java)
- `/b`: TransFlash card, if one is inserted

Searching a specific volume takes a UTF-16BE pattern such as `/c/` `FFFE` `*`,
padded to 252 bytes.

Reply type `0x60` means success. `0x70` means the request was rejected, for example
because the file or folder doesn't exist. `0x80` carries data, and its first byte is
a status (`0` = OK).

Unplugging the cable puts the phone back into normal mode.

## Known issues

- Once during early testing, the phone stopped answering on USB right after a delete.
  It didn't happen again with the current code. Unplugging and replugging the
  cable fixes it. The app detects this case and asks you to replug.
- USB 1.1 full speed plus a 1 KB-per-request protocol makes transfers slow,
  at tens of KB/s.
- Only tested on the V3r. Other P2K phones (V3, V3i, E398, L7, …) use the same
  protocol, but their USB IDs or volume layout may differ.

## Credits

The protocol details come from these GPL projects:

- [moto4lin / p2kmoto](https://sourceforge.net/projects/moto4lin/) by Dmitry Nezhevenko
- [p2kc / p2k-core](https://github.com/pulkomandy/p2kc)

## License

GPL-2.0-or-later, the same license as the projects the protocol comes from.
See [LICENSE](LICENSE).
