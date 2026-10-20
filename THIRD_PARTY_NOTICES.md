# Third-party components

YT-Duo's own code is under the [MIT License](LICENSE). Bundled components retain their upstream licenses; the MIT license does not replace them.

| Component | License / source | Windows distribution |
| --- | --- | --- |
| Python | [PSF license](https://docs.python.org/3/license.html) | Bundled by PyInstaller, with its runtime notices. |
| Tcl/Tk | [Tcl/Tk license](https://www.tcl-lang.org/software/tcltk/license.html) | Bundled by PyInstaller. |
| yt-dlp | [Unlicense and dependency notices](https://github.com/yt-dlp/yt-dlp/blob/master/LICENSE) | Python package and its default dependencies. |
| yt-dlp-ejs | [Upstream license](https://github.com/yt-dlp/ejs) | JavaScript solver scripts; no runtime script download is enabled by YT-Duo. |
| FFmpeg / FFprobe 9.0.2 | [LGPL 2.1+](https://ffmpeg.org/legal.html), [FFmpeg source snapshot](https://github.com/FFmpeg/FFmpeg/tree/946fcce07b) | Purpose-built static FFmpeg/FFprobe with only the enabled components in scripts/build_ffmpeg.sh; includes its license and build configuration. |
| LAME 3.100 | [LGPL 2+ and source](https://sourceforge.net/projects/lame/files/lame/3.100/) | Statically linked MP3 encoder from MSYS2; license and installed build-package versions included. |
| Deno 2.9.7 | [MIT license and source](https://github.com/denoland/deno/tree/v2.9.7) | Official unmodified Windows binary and LICENSE.md. |

FFmpeg source is pinned to a commit and verified before compilation. Its build recipe is included in the project source ZIP; LAME package sources are maintained in [MSYS2 MINGW-packages](https://github.com/msys2/MINGW-packages/tree/master/mingw-w64-lame).

The pinned download URLs and SHA-256 values are in [windows-tools.json](scripts/windows-tools.json). The release builder verifies them before extraction, keeps the supplied license/readme files, and tests the tools before packaging. Build inputs are downloaded only during an explicit Windows build, not during application startup. FFmpeg and Deno run as separate programs.

The portable application's runtime folder contains `tools/licenses/`, the project license, and this notice. Setup also installs LICENSE and this notice beside the application. Download the notices alongside the release assets when redistributing them.
