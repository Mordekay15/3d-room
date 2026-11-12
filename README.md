# Roomwalk

Film a room with your phone, and walk around inside it on your computer.

Roomwalk turns an ordinary video into a photorealistic 3D scan (a *Gaussian
splat*) and opens it in a first-person viewer: you walk with the keyboard or a
thumb-stick, a floor-plan minimap shows where you are and which way you are
looking, and walls and furniture stop you from walking through them.

Everything runs on your own computer. Your videos are not uploaded anywhere.

## What you need

- A computer with Windows 10/11, macOS (Apple Silicon) or Linux
- Python 3.10 to 3.14. **Not 3.15 yet**: one of the parts Roomwalk uses has no 3.15 version
- A graphics chip that supports Vulkan, DirectX 12 or Metal. Built-in laptop
  graphics such as **Intel Iris Xe** work; a dedicated graphics card is much faster
- About 2 GB of free disk space, and an internet connection for the first run

## Start it (Windows)

1. Install **Python 3.14** from [python.org](https://www.python.org/downloads/windows/)
   (pick the latest 3.14 "Windows installer (64-bit)", not 3.15). On the
   installer's first screen, tick **Add python.exe to PATH**.
2. Download this project: green **Code** button on GitHub, then **Download ZIP**.
   Unzip it somewhere simple, like `C:\Roomwalk`.
3. Double-click **`start.bat`**.

The first start sets itself up (a few minutes, a few hundred MB). After that a
black window stays open, and the app opens in your browser at
`http://localhost:8765`. Close the black window to stop the app.

On macOS or Linux, run `./start.sh` in a terminal instead.

## Make your first scan

1. Open the **Example room** first and try the controls.
2. Film your room (see the tips below), copy the video to the computer.
3. On the app's page, choose the video, pick a quality, press **Start the scan**.
4. Watch the four steps tick by. You can **peek** inside while it is still
   training; the picture sharpens as it goes.
5. When it says *Ready*, press **Walk inside**.

Start with **Quick** quality for your first scan. It proves the video works
before you spend hours on a detailed one, and you can press
**Rebuild at Standard quality** afterwards without redoing the earlier steps.

### How long it takes

The first two steps use the processor and take roughly 5 to 20 minutes.
The third step (training) uses the graphics chip and is the slow one. On
built-in graphics like Intel Iris Xe, expect somewhere between half an hour
(Quick) and many hours (Detailed); the page shows a live estimate once
training has been running for a minute. Plug the laptop in, and close games
and spare browser tabs, because training needs the graphics memory.

### Filming a room that scans well

- **Walk, don't spin.** The app works out depth from the camera changing
  position. Turning on the spot gives it nothing to work with.
- **Slowly, for 45 to 90 seconds.** Circle the room facing inwards, then once
  more facing the walls.
- **Turn every light on.** Dark corners come out as fog.
- **Phone sideways at chest height**, tilting up and down now and then so the
  floor and ceiling are seen.
- **Nothing should move**: no people, pets, or a TV playing.
- Mirrors, windows and bare white walls are hard for this technique and will
  look soft or strange.

## Walking around

| | Keyboard and mouse | Phone or tablet |
|---|---|---|
| Look | Click the picture, then move the mouse (Esc frees it). Or drag. | Drag with your right thumb |
| Walk | W A S D or the arrow keys | Left thumb-stick |
| Run | Hold Shift | Push the stick further |
| Jump to a spot | Click the minimap | Tap the minimap |
| Replay the filmed path | T, or **Play tour** | **Play tour** |
| Bigger map | M | **Enlarge** |

**Settings** has eye height, walking speed, field of view, picture sharpness,
a switch for walking through walls, and a free-flying mode.

### View it on an iPad or phone

Double-click **`start-wifi.bat`** instead of `start.bat`. The black window then
shows a second address (like `http://192.168.1.20:8765`); open that in the
browser of a phone or tablet on the same Wi-Fi. Windows may ask to allow the
app through the firewall; allow it for private networks. Only use this on a
home network you trust: anyone on that Wi-Fi can open the app while it runs.

## What to expect from the result

A scan made this way looks like your real room, because it is built from your
real footage: actual textures, lighting and reflections. It is not a clean
designer model, though. Places the camera never saw well are blurry or
missing, thin objects and glass can look smeared, and floating wisps can
appear in empty space. More careful filming fixes most of that.

The room's size comes from one assumption: how high you held the phone. If
things feel too big or too small, change **Eye height** in Settings.

## If something goes wrong

- **"Only N of M frames could be placed"** or the camera path fails: the video
  moved too fast, was too dark, or showed mostly blank walls. Film again, slower.
- **The 3D engine could not use the graphics chip**: update the graphics
  driver (for Intel graphics, use Intel's Driver & Support Assistant), restart, Retry.
- **Out of memory during training**: use Quick quality and close other programs.
- **The engine download fails**: download `brush-app-x86_64-pc-windows-msvc.zip`
  from the [Brush releases page](https://github.com/ArthurBrussee/brush/releases),
  unzip it into `tools\brush` inside the Roomwalk folder, and press Retry.
- **Walking is choppy**: Settings, Picture sharpness, Fast.
- Each scan keeps a `log.txt` in its folder under `scenes\` with the details.

A failed or stopped scan carries on from where it stopped when you press
Retry; finished steps are not repeated.

## Already have a splat file?

Phone apps that make Gaussian splats can export a `.ply` file. Use
**Import a .ply file** on the home page to walk through it here. If the room
comes in upside down or on its side, fix it under Settings in the viewer.

## How it works

1. **Frames** - `ffmpeg` reads the video; the sharpest frame in each time slot is kept.
2. **Camera tracking** - [COLMAP](https://colmap.github.io/) (through `pycolmap`)
   finds landmarks, matches them between frames and solves where the camera
   was for every frame.
3. **Training** - [Brush](https://github.com/ArthurBrussee/brush) fits a few
   hundred thousand soft coloured blobs ("Gaussian splats") until they
   reproduce every frame. Brush is used because it runs on ordinary graphics
   chips, not only NVIDIA cards. It is downloaded automatically on first use.
4. **Making it walkable** - the floor is found, the scan is turned upright and
   scaled to metres, the walls are squared to the map, and a floor plan and a
   collision grid are drawn from the splats.
5. **Viewer** - [three.js](https://threejs.org/) with the
   [Spark](https://sparkjs.dev/) splat renderer, bundled in `web/vendor` so
   the viewer works offline.

Layout: `roomwalk/` is the Python app (`server.py` web app, `pipeline.py` the
four steps, `align.py` floor/scale/maps), `web/` the pages, `scenes/` your
scans, `tools/` the downloaded engine.

Advanced: add `"overrides": {"steps": 20000, "max_splats": 1000000}` to a
scan's `meta.json` to change any preset number for that scan, then Retry.
`python -m roomwalk --help` lists the start options.

## Credits and licences

COLMAP (BSD), Brush (Apache-2.0), three.js (MIT) and Spark (MIT) do the heavy
lifting. Their licence files are in `web/vendor` or shipped with the downloads.
