# Downloads

| Platform | Portable / launcher | Installer |
| --- | --- | --- |
| Windows x64 | [YT-Duo-Portable.exe](https://github.com/4Raisan/YT-Duo/releases/download/v4.6.1/YT-Duo-Portable.exe) | [YT-Duo-Setup.exe](https://github.com/4Raisan/YT-Duo/releases/download/v4.6.1/YT-Duo-Setup.exe) |
| Linux | [YT-Duo.sh](https://github.com/4Raisan/YT-Duo/releases/download/v4.6.1/YT-Duo.sh) | — |
| macOS | [YT-Duo.command](https://github.com/4Raisan/YT-Duo/releases/download/v4.6.1/YT-Duo.command) | — |

Download links point to the v4.6.1 release assets. Local builds are written to the windows, linux, and macos subfolders here. Compiled downloads are ignored by Git and distributed through GitHub Releases.

Windows builds bundle Python/Tk. Setup installs for the current user with Start menu, optional desktop shortcut, and uninstall support. Portable defaults to `YT-Duo Downloads` beside the EXE. Setup defaults to `YT-Duo Downloads` in the Windows Downloads known folder; installed settings stay under `%LOCALAPPDATA%/YT-Duo/data` and survive uninstall. Videos use the chosen base folder and music its `Audio` child; Browse overrides the base. Both include Python/Tk, yt-dlp with its YouTube JS helpers, FFmpeg, FFprobe and Deno. No Python, FFmpeg, Deno, winget, or system PATH setup is needed for these Windows downloads. Executables remain unsigned.

Unix launchers embed app source and require Python 3.10+, Tkinter, `yt-dlp[default]`, FFmpeg, FFprobe and Deno. Run `sh YT-Duo.sh` on Linux; use `chmod +x YT-Duo.command` then double-click on macOS, or run with `sh`. Set `YTDUO_PYTHON` to select Python. Native Unix app bundles/installers are not provided.

Verify files against [SHA256SUMS.txt](https://github.com/4Raisan/YT-Duo/releases/download/v4.6.1/SHA256SUMS.txt). See [build instructions](../docs/DEVELOPMENT.md).

## Dependencies

### Windows portable / Setup

Download and run either EXE. Required tools are included in the private runtime cache; nothing is installed globally. Source users can run `YT-Duo.bat`: it checks Python 3.10+, Tk, yt-dlp and its JS helpers, FFmpeg **and** FFprobe, then Deno. It uses winget only to install missing tools. Reopen the launcher after a tool installation refreshes PATH. Failed setup stops before opening the app.

### Linux (Ubuntu / Debian)

```sh
sudo apt update
sudo apt install python3-venv python3-tk ffmpeg
```

Install Deno using its [official instructions](https://docs.deno.com/runtime/getting_started/installation/), then prepare an environment for the launcher:

```sh
python3 -m venv ~/.local/share/ytduo-env
~/.local/share/ytduo-env/bin/python -m pip install -U "yt-dlp[default]"
YTDUO_PYTHON=~/.local/share/ytduo-env/bin/python sh YT-Duo.sh
```

### macOS

Install Python 3.10+ with Tk from [python.org](https://www.python.org/downloads/macos/). If you use Homebrew, `brew install ffmpeg deno` supplies the media tools and JS runtime. Create the same virtual environment shown above, then launch `YT-Duo.command` instead of `YT-Duo.sh`. Native macOS execution is not verified.

### Verify a source installation

From a source checkout or extracted source ZIP:

```sh
python -m pip install -e .
python -m ytduo --check-dependencies
```

The check reports versions and locations, runs FFmpeg/FFprobe/Deno, and returns failure for missing or broken dependencies. It does not download or install anything. FFprobe normally comes with FFmpeg. The Python package named `ffmpeg` is not a substitute for these executables.

[Complete v4.6.1 source ZIP](https://github.com/4Raisan/YT-Duo/releases/download/v4.6.1/YT-Duo-4.6.1-source.zip) · [Third-party notices](../THIRD_PARTY_NOTICES.md). The ZIP contains source, project license, docs, tests, and build configuration—not preinstalled dependencies.
