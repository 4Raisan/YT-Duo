# Development and packaging

Install with `python -m pip install -e .`. Run offline checks with `python -m unittest discover -s tests -t . -q`. Run `python tests/media_smoke.py` with installed FFmpeg/FFprobe for real engine conversions, duplicate decisions, queue naming and private-storage checks using synthetic fixtures. CI runs full unit/Tk tests on Windows/Linux, headless engine/CLI tests on macOS, and real media checks on Linux/macOS; the Windows build tests the same paths against its minimal FFmpeg tools.

## Structure

- `src/ytduo/`: application, shared engine, packaged PNG/ICO icons.
- `scripts/`: release builder, Inno Setup recipe, diagram generator, fixture capture helper.
- `tests/`: offline regressions, live smoke script, fixtures.
- `docs/assets/`: approved red branding and self-contained architecture/workflow SVGs.
- `Releases/`: platform-specific local outputs, checksum manifest, download guide. Compiled downloads are ignored by Git.

Personal settings, queues, downloaded media (including YT-Duo Downloads/), temporary streams, runtime caches, and build files do not belong in Git. Working directories are recreated as needed. No private queue ships with the project.

## Build

```sh
python -m pip install -e . -r requirements-build.txt
python scripts/build_release.py
python scripts/build_release.py --windows
python scripts/build_release.py --windows --setup --iscc "PATH/TO/ISCC.exe"
```

Windows must be built on Windows. Artifacts are compiled under `build/release-stage` and published with same-volume atomic renames; do not compile or copy EXEs directly into a custom release directory with inherited integrity labels. Setup requires [Inno Setup 6](https://jrsoftware.org/isdl.php), installed separately. Build compilers must be installed separately; the builder fetches only pinned, checksum-verified FFmpeg and Deno inputs and fails visibly if Setup is requested without an Inno compiler. Manual CI builds both Windows variants, validates dependencies and source installation, uploads artifacts, and stages a draft release. Publishing that draft is a separate final step.

PyInstaller creates a directory bundle. The builder stores its files in a ZIP and compresses that complete archive with Windows LZMS (32 MiB blocks); the .NET launcher uses the built-in Windows decoder on first launch. Cached launches do not decompress again. This replaces per-file Deflate without adding a decoder dependency. Setup wraps the same portable EXE. A small unfrozen shell helper is embedded separately. Folder and GitHub actions run in background threads through that helper with sanitized child environment variables; the frozen Python process never changes its live DLL search directory for those actions. Failures return through the service event bridge and release the pending-action state.

Runtime caches live under `%LOCALAPPDATA%/YT-Duo/runtime`, with a `%TEMP%/YT-Duo/runtime` fallback. Startup probes writability and reports a visible error if neither works; it never extracts beside the EXE or changes filesystem permissions. Hash-addressed versions have shared in-use leases. Under the extraction lock, the launcher can prune older idle versions with recognized leases, while retaining the current version and one other version; active or legacy caches are preserved. The launcher stays alive while its GUI runs to hold the lease. Setup uses the same portable payload with an installation marker; installed data lives independently under LocalAppData/YT-Duo/data, and uninstall preserves it. YTDUO_HOME overrides the data root.

Unix launchers embed Python modules and the PNG icon. They use an existing Python/Tk/yt-dlp environment, not a bundled interpreter. Native macOS/Linux app packages and installers require separate platform builds and are not claimed here.

Regenerate architecture/workflow SVGs with `python scripts/build_diagrams.py`. The compact README logo is docs/assets/yt-duo-logo.svg: a transparent, self-contained SVG with the original PNG mark embedded and a vector text wordmark. Its wordmark adapts to light/dark color schemes. The original banner and transparent mark remain in docs/assets/yt-duo-logo.png and yt-duo-icon.png; application icons are in src/ytduo/assets.

The play triangle uses bright ivory with a crimson edge for contrast against the red glass. The banner and transparent mark were edited with built-in imagegen using the instruction: change only the central triangle to ivory, preserve the red facets, shape, layout, and typography. PNG/ICO derivatives, diagrams, and release downloads use the same treatment.

## Validation limits

Offline GUI/engine tests stub network requests. Passing tests do not prove live YouTube availability or native macOS execution. Windows downloads are unsigned.

The previous Releases directory inherited a Low mandatory-integrity label. EXEs created there launched with a Low token (S-1-16-4096), while identical diagnostics built normally launched at Medium (S-1-16-8192). Low processes could not write the normal profile/Temp cache or activate the desktop shell reliably. See [Microsoft's integrity-control explanation](https://learn.microsoft.com/en-us/windows/win32/secauthz/mandatory-integrity-control). The packaging fix preserves each staged file by renaming it on the same volume, rather than creating a replacement file under the destination's inherited label. No ACLs, integrity labels, or Windows security settings are edited. A regression checks that publication retains the built file's identity.

## Local 4.2.1 verification

The published portable and Setup files no longer carry the Low label. Direct release-path tests passed eight repeated Unicode-folder opens after an expected missing-folder failure, followed by successful GitHub browser activation. The actual packaged GUI opened downloads and GitHub successfully. A deliberate path collision produced a visible Open error; after dismissing it, Star still opened GitHub and Open succeeded again after restoring the test folder. Text-size changes were exercised in the GUI; expanded layouts are repositioned within their monitor work area to keep controls reachable.

The final offline suite passed 262 tests. The final Setup installed the byte-identical portable payload and installation marker into an isolated test directory, then uninstalled cleanly. The final packaged GUI kept the expanded Options panel and footer on screen at 125% text size, then restored 100%. All four release checksums passed; both Unix source payloads and the frozen Windows modules matched the current source. Diagram generation was deterministic and embedded the current icon. Native macOS/Linux execution and live media downloads remain separate checks; Windows downloads remain unsigned.

## Local 4.3.0 verification

The final offline suite passed 270 tests, including verified-paste Auto download, stale/failed result rejection, current music settings, live-queue handoff, preflight retry, independent disclosure state, session-only Terminal hiding, startup sizing before update results, and overflow at a lower window position.

The final packaged Windows GUI was inspected with the saved High Contrast theme and 110% text size. Options shows a persistent active button and labeled separator. Options and Terminal can be opened independently, and scrolling reaches both the Terminal body and footer within the window. The final installer round trip passed; its installed executable matched the final portable SHA-256 exactly. All four release checksums passed, both Unix payloads matched current source, and the frozen Windows module code matched current source.

Auto-download flow is tested with offline metadata and engine boundaries; this run did not download live media. Native macOS/Linux execution remains unverified. Existing user media and portable preferences were preserved.

## Local 4.3.1 verification

Removed the main scrolling viewport. Opening Options closes Terminal; opening Terminal closes Options. First activity does not replace an open Options panel, and hidden logs are retained. Compact spacing, side-by-side preference checkboxes and a short internally scrollable queue keep the options and footer reachable.

All 270 offline tests passed, including mutual exclusion, hidden-log retention, startup sizing and 125% text with a queued item and running download controls. The rebuilt portable GUI was checked at the saved 110% text size: both switching directions worked, Options and the footer fit, and no outer scrollbar appeared. Portable and Setup were rebuilt as 4.3.1; all four distribution checksums passed. User media and preferences were preserved.

## Local 4.4.0 verification

All 274 offline tests passed. New flow coverage checks reopening Terminal for each batch/item, progress-row control placement, cooperative pause/resume with worker progress, Stop while paused, five-second duplicate timeout, explicit redownload approval and Stop while a duplicate decision is pending.

The packaged Windows GUI started with Terminal expanded. An existing archived media link showed the real five-second redownload popup and the aligned Pause/Stop controls. Leaving the popup untouched skipped the item and reported one skipped, zero downloaded. The existing media SHA-256 remained unchanged. No new media was downloaded for this verification. Pause/Resume/Stop worker behavior was verified offline; live FFmpeg suspension is not claimed.

Portable and Setup were rebuilt as 4.4.0. All four distribution checksums passed; frozen GUI, worker and engine code matched current source. The installer installed the byte-identical portable payload into an isolated test folder and uninstalled cleanly. User settings and media were preserved.

## Local 4.5.0 verification

The final offline suite passed 282 tests. Additional assertions verified that successful original-audio and MP3 processing saves under Audio and deletes the source. Coverage includes installer/portable defaults, redirected Downloads resolution, saved custom destinations, one-time legacy-default migration, creation and write-probe failures offering Browse, scoped recycle cleanup, silent empty input, animated scanning, fixed Terminal rows, eight themes and progress-track symbol controls.

The final portable GUI showed its new default and the Audio child for Music. Startup removed the legacy recycle directory. Empty Download stayed silent. The theme menu contained eight choices without Nord; a real paste showed the scanning arc. An existing archived music link showed the compact ten-second prompt and inline pause/stop symbols; timeout skipped the item without downloading media. Terminal height stayed fixed. The final Setup installed a byte-identical portable payload; its launched GUI defaulted to the actual Windows Downloads/YT-Duo Downloads folder. Uninstall passed. Existing media hashes were unchanged.

Frozen GUI/config/engine/theme/widgets code and both Unix payloads matched current source. All four distribution checksums passed. Native Unix execution and live fresh media downloads were not run for this update. Failed/interrupted partial streams can still remain in the temporary video/audio folders; startup cleanup is specifically scoped to recycle.

## Local 4.5.1 verification

Fixed incremental startup rendering. The root is withdrawn before construction and layout measurements. The completed window maps at zero opacity while native frame events and geometry settle, then becomes opaque. There is no artificial startup delay.

All 283 offline tests passed. A real Tk main-entry regression records that mapping happens only after construction and at zero opacity, then checks that visible controls and dimensions remain stable across subsequent event processing. The rebuilt Windows portable was launched and inspected; minimize/restore and close were exercised. Setup installed the byte-identical portable payload and uninstalled cleanly. Frozen GUI and Unix payloads matched current source; all four release checksums passed. Native Unix startup remains unverified.

## Publication verification

GitHub's Windows runner exposed an Options overflow at 125% text size on a short screen. The app now removes extra section spacing only when the queue's compact height is insufficient, then restores normal spacing when space is available. Text size and controls remain unchanged. A regression forces the constrained layout and checks restoration.

The final application code passed 284 offline tests locally and on GitHub's Windows runner. The same successful workflow built the portable EXE, Setup, and Unix launchers. Release files and SHA256SUMS.txt were synchronized from those CI artifacts. The release tag adds only this publication record and the checksum update to the tested application code. Native macOS/Linux execution and live media downloads remain unverified for this publication.

## Bundled dependencies (4.6.0)

Windows builds explicitly download the versioned FFmpeg shared build and official Deno archive in scripts/windows-tools.json. Every archive and downloaded license must match its pinned SHA-256. Only the required tools, FFmpeg DLLs, and supplied text notices are packaged; ffplay is omitted. The builder runs all three tools and a short FFmpeg-to-FFprobe audio smoke check before freezing. Python packaging installs yt-dlp's default extras, and PyInstaller collects yt-dlp-ejs data and metadata.

The GitHub build runs the packaged app's noninteractive dependency report with a system-only PATH, proving that bundled tools and EJS helpers load without development dependencies. The source ZIP contains tracked project files and licenses and is installed into a separate environment before upload. Offline tests run automatically on Windows and Linux pushes/pull requests.

Unix launchers remain source-based and do not bundle tools or Python. Follow Releases/README.md for venv, Tk, FFmpeg/FFprobe and Deno installation. Application startup never downloads tools. Third-party source/license links and distribution notices are in THIRD_PARTY_NOTICES.md.

## Compact Windows packaging (4.6.2)

FFmpeg 9.0.2 is compiled from pinned source using scripts/build_ffmpeg.sh in MSYS2 MINGW64 (GCC, NASM and LAME). It includes only the local-file protocols, containers, codec parsers/decoders, MP3 encoder and audio filters needed by the app. General video encoders, device capture, networking and unrelated filters are omitted. The builder checks synthetic H.264/AAC, VP9/Opus and AV1/Opus clips: stream-copy merging, original-audio copying, five MP3 qualities and FFprobe inspection. Run prepare_ffmpeg_source(), then bash scripts/build_ffmpeg.sh in MSYS2 before the Windows packaging command. Python/Tk, yt-dlp/EJS and Deno remain bundled. Tool extraction uses a manifest-specific directory so changing inputs cannot retain DLLs from the previous build. Compression changes do not re-encode downloaded media.

## 4.6.4 direct update flow

`updates.UpdateChecker` performs automatic stable-release discovery. GUI startup never installs. The footer Update to latest action requests a fresh check, stages a platform-matching asset from the official versioned GitHub release, verifies SHA-256 from SHA256SUMS.txt, and hands off through Tk events. Transfers run outside the UI thread; active downloads block updates and staged updates block new downloads. Network and integrity failures restore a retryable action with a visible error.

Windows downloads the portable payload for both distributions. Its new C# launcher handles --apply-update before payload extraction, waits for the old Python/launcher processes, atomically replaces the existing EXE with a rollback copy, then relaunches from the same directory. Installed markers, shortcuts, config and media are untouched. Unix launchers export their original path before entering the temporary Python source payload; an atomic same-directory rename keeps file permissions and starts the updated launcher. Source checkouts deliberately require a source upgrade.

Only HTTPS GitHub/release-asset redirects are allowed. Asset names and URLs derive from a validated numeric stable tag, rather than arbitrary release links. Checksums detect corruption; they are supplied by the same repository and are not independent publisher signatures. A new release must keep versioned assets and the checksum manifest together. Pre-4.6.4 builds require a manual upgrade once.

### 4.6.5 TLS follow-up

The live release-asset check exposed an incomplete system CA store on the local Windows Python installation. Update checks and asset downloads now use a verified SSL context combining system roots with Certifi public roots; hostname and certificate verification remain required. Certifi is an explicit source dependency and its data is collected into Windows builds. This does not disable verification or change Windows trust settings.

## 4.6.6 monitor layout and update maintenance

Window sizing captures the current monitor before expanding Options or Terminal. Native monitor bounds and repositioning share a scoped physical DPI coordinate space; available height is converted to Tk units using the actual window rectangle ratio. This avoids failed point conversions outside the window, primary-screen assumptions and lost negative monitor origins. Minimized windows defer layout until restored. Button strokes are inset inside their Canvas even when pressed. The native rounded clipping region uses window coordinate units with a fractional-DPI rounding margin and invalidates its cache on physical-size changes, preventing stale clipping after crossing between differently scaled displays.

The update action is hidden at startup and for current/unavailable releases; only a confirmed newer stable version shows its green outline. Linux/macOS primary assets include their version and platform. Small unversioned aliases remain for older updaters. The new updater prefers versioned assets and accepts legacy manifests.

Windows waits for the old launcher and Python process, replaces the verified payload and restarts. The new launcher waits for the updater to exit, removes only its recognized temporary EXE in an owned temporary directory, and removes that directory only when empty. Idle runtimes with a lease are pruned; current, active, linked and unrecognized directories are retained. Old shell helpers are removed when unlocked. Unix cleanup errors after successful restart are nonfatal. Settings and finished media are outside these cleanup paths.
