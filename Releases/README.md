# Downloads

| Platform | Portable / launcher | Installer |
| --- | --- | --- |
| Windows x64 | [YT-Duo-Portable.exe](https://github.com/4Raisan/YT-Duo/releases/download/v4.5.1/YT-Duo-Portable.exe) | [YT-Duo-Setup.exe](https://github.com/4Raisan/YT-Duo/releases/download/v4.5.1/YT-Duo-Setup.exe) |
| Linux | [YT-Duo.sh](https://github.com/4Raisan/YT-Duo/releases/download/v4.5.1/YT-Duo.sh) | — |
| macOS | [YT-Duo.command](https://github.com/4Raisan/YT-Duo/releases/download/v4.5.1/YT-Duo.command) | — |

Download links point to the v4.5.1 release assets. Local builds are written to the windows, linux, and macos subfolders here. Compiled downloads are ignored by Git and distributed through GitHub Releases.

Windows builds bundle Python/Tk. Setup installs for the current user with Start menu, optional desktop shortcut, and uninstall support. Portable defaults to `YT-Duo Downloads` beside the EXE. Setup defaults to `YT-Duo Downloads` in the Windows Downloads known folder; installed settings stay under `%LOCALAPPDATA%/YT-Duo/data` and survive uninstall. Videos use the chosen base folder and music its `Audio` child; Browse overrides the base. Both require FFmpeg/FFprobe on PATH and are unsigned.

Unix launchers embed app source and require Python 3.10+, Tkinter, yt-dlp, and FFmpeg/FFprobe. Run `sh YT-Duo.sh` on Linux; use `chmod +x YT-Duo.command` then double-click on macOS, or run with `sh`. Set `YTDUO_PYTHON` to select Python. Native Unix app bundles/installers are not provided.

Verify files against [SHA256SUMS.txt](SHA256SUMS.txt). See [build instructions](../docs/DEVELOPMENT.md).
