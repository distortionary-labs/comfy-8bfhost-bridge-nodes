# comfy-8bfhost-bridge-nodes

comfy-8bfhost-bridge-nodes adds ComfyUI nodes that run Photoshop-compatible filter plug-ins (`.8bf` files) on images in a workflow. The nodes do not load filters themselves. Every filter runs in [8bf-host](https://github.com/distortionary-labs/8bf-host), a separate program that you download and install yourself. The nodes pass each image, and the mask if any, to 8bf-host and hand the result to the next node.

It works wherever 8bf-host does: Windows, macOS and Linux. On Linux, Windows filters run under Wine.

It is the ComfyUI counterpart of [krita-8bfhost-bridge](https://github.com/distortionary-labs/krita-8bfhost-bridge), and works the same way.

## What works

- Filters on any `IMAGE`, in 8-bit or 16-bit samples. Each image of a batch is filtered in turn.
- Masks. The filter receives the mask as its selection, and the result is blended through it the way Photoshop's "auto mask" does it, soft edges included. A frame whose mask selects nothing is passed through unchanged.
- Filter dialogs and previews, shown by 8bf-host, on the machine that runs ComfyUI.
- Stored settings. After each run the node keeps the filter's settings as an 8bf-host preset, so later runs can replay them without the dialog, including after a restart.
- 8bf-host preset files you made on the command line.
- 16-bit images with filters that only declare 8-bit support. 8bf-host runs the filter on an 8-bit copy and keeps the full 16-bit data for any pixel the filter left unchanged. You can turn this off.

### Known limitations

- **Dialogs need a desktop.** A filter's dialog belongs to the 8bf-host process, which ComfyUI starts on its own machine. On a headless server or in a container, choose the settings with "show dialog" on a desktop machine, or make a preset with `8bf-host run --dialog --save-preset`, and use "preset file" on the server.
- **No alpha.** ComfyUI keeps alpha in a separate `MASK`, so filters see an RGB image without transparency.
- **No progress within a frame.** 8bf-host does not report progress, so ComfyUI's progress bar moves once per frame. Cancelling the queue asks the filter to stop, as Ctrl+C does for 8bf-host in a terminal. On Windows it ends the 8bf-host process instead.
- Everything 8bf-host's own [compatibility notes](https://github.com/distortionary-labs/8bf-host/blob/main/docs/compatibility.md) list. Some commercial filters check for Photoshop specifically and refuse to run anywhere else.

Because filters run in a separate program, a filter that crashes takes 8bf-host down with it, not ComfyUI.

## Installation

You need ComfyUI and 8bf-host. The nodes use only the Python standard library and the numpy and torch that ComfyUI already depends on.

### 1. Install 8bf-host

Download the build for your system from the [8bf-host releases](https://github.com/distortionary-labs/8bf-host/releases), or build it with `cargo install`. Check that it starts:

```console
$ 8bf-host --version
8bf-host 0.1.0
```

On Linux, Windows filters need Wine and the Windows build of 8bf-host. 8bf-host's guide to [Windows filters on Linux](https://github.com/distortionary-labs/8bf-host/blob/main/docs/wine.md) covers the setup, and `8bf-host doctor` shows whether it is complete.

Check your filters' licenses too. Some commercial filters only permit use inside particular programs.

### 2. Install the nodes

Clone this repository into ComfyUI's `custom_nodes` folder and restart ComfyUI:

```console
$ cd ComfyUI/custom_nodes
$ git clone https://github.com/distortionary-labs/comfy-8bfhost-bridge-nodes
```

From a checkout elsewhere, `make install COMFYUI=/path/to/ComfyUI` copies the nodes there.

### 3. Point the nodes at 8bf-host

The nodes look for 8bf-host in this order:

1. `host_path` in `ComfyUI/user/8bfhost_bridge/config.json`.
2. The `EIGHTBF_HOST` environment variable.
3. `PATH`, then `~/.cargo/bin` and `~/.local/bin`.

ComfyUI started from a desktop launcher often has a shorter `PATH` than your terminal, so the configuration file is the reliable choice. It can also list extra filter folders:

```json
{
  "host_path": "/home/you/.cargo/bin/8bf-host",
  "folders": ["/home/you/filters"]
}
```

8bf-host itself searches `~/8bf-plugins`, the folders in its own configuration file, the `EIGHTBF_HOST_PLUGIN_PATH` variable and, on Windows and macOS, the usual Adobe plug-in folders. The nodes add `ComfyUI/models/8bf` when it exists, so you can keep filters next to your models. Subfolders are searched too, and 8bf-host caches what it found, so later scans only re-read files that changed.

## Using it

The nodes are under **image > filters > 8bf**:

- **8bf Filter** lists the filters 8bf-host can run on this machine as `Category/Name`. Filters it cannot run are left out; `8bf-host list` shows why. Press **R** in ComfyUI, or reload the page, to pick up new filters.
- **8bf Filter (by Name)** takes the filter as text: an id, a name, `Category/Name` or a plug-in file, as 8bf-host's command line accepts them. It suits workflows shared between machines whose filters differ.

Both take an `image` and an optional `mask`, and return the filtered `IMAGE`. The `settings` input says where the filter's settings come from:

| Choice | What runs |
|--------|-----------|
| last used | The settings of this filter's last run, or its defaults if it has never run. |
| show dialog | The filter's dialog opens, once per batch. Every run of the node shows it again. |
| defaults | The filter's own defaults. |
| preset file | The 8bf-host preset named in `preset_file`. The file itself is not changed. |

A common pattern is to set **show dialog**, queue once to choose the settings, then switch to **last used**. ComfyUI only reruns the node when its inputs or the stored settings change.

`depth` chooses 8- or 16-bit samples. 8-bit suits most filters and most images ComfyUI handles; 16-bit keeps more precision for filters that support it. `foreground`, `background` (as `#rrggbb`) and `resolution` are handed to filters that use them. `allow_8bit_fallback` off makes a 16-bit run of an 8-bit-only filter fail instead of running on an 8-bit copy.

## How a filter run works

For each image of the batch:

1. The node writes the image to a temporary folder as a PAM file, and the mask, if any, as a grayscale image.
2. It runs `8bf-host run <filter> frame.pam filtered.pam --save-preset <preset>`, adding the mask, the mask's bounds, the colors, the resolution and, depending on `settings`, `--dialog` or `--preset`.
3. When 8bf-host exits, the node reads `filtered.pam` back and deletes the temporary folder.

8bf-host decides where the filter's code runs: in its own process, in a worker of another architecture, or under Wine. See its [documentation](https://github.com/distortionary-labs/8bf-host/blob/main/docs/where-filters-run.md). Presets are kept in `ComfyUI/user/8bfhost_bridge/presets`, one 8bf-host preset file per filter.

## Troubleshooting

**The filter list says "(8bf-host not set up)".** The nodes could not find or run 8bf-host. ComfyUI's console shows why. Set `host_path` as described above.

**The filter list says "(no 8bf filters found)".** 8bf-host runs but found nothing it can run. `8bf-host list` shows what it found and why a filter cannot run, for example a Windows filter on Linux without the Wine setup.

**A filter fails.** The error ComfyUI shows is 8bf-host's, and the console has its log for the run. You can reproduce a run outside ComfyUI with the same command line; `8bf-host run --help` describes it.

**Debug output.** Start ComfyUI with `COMFY_8BFHOST_BRIDGE_DEBUG=1` in its environment to print each 8bf-host command and its log to the console.

## Project layout

```text
__init__.py                     ComfyUI's entry point
comfy_8bfhost_bridge/
  nodes.py                      Node definitions and glue
  bridge.py                     Filtering a batch of frames, one 8bf-host run each
  host.py                       Finding 8bf-host, building its commands, running it
  images.py                     ComfyUI images and masks to and from PAM files
  catalog.py                    The filter list from `8bf-host list --json`
  config.py                     The configuration file and per-filter presets
tests/
  fixtures/testfilter.c         A filter written for 8bf-host's tests, built natively
```

## Development

The development environment is described in `devenv.nix`: Python 3.12 with pytest, numpy and torch, a C compiler and ruff.

```console
$ make test
..........................................................               [100%]
73 passed in 2.13s
```

Without torch, the node tests are skipped. The tests in `test_with_8bf_host.py`, and one node test, run against a real 8bf-host, using a test filter compiled from `tests/fixtures/testfilter.c`. Point `EIGHTBF_HOST` at a dynamically linked build to enable them. Release builds for Linux are static and cannot load native `.so` filters, so build one in a checkout of 8bf-host with `cargo build`:

```console
$ EIGHTBF_HOST=../8bf-host/target/debug/8bf-host make test
```

## Related projects

- [8bf-host](https://github.com/distortionary-labs/8bf-host), which runs the filters.
- [krita-8bfhost-bridge](https://github.com/distortionary-labs/krita-8bfhost-bridge), the same bridge for Krita.

## License

MIT. See `LICENSE`. Photoshop is a trademark of Adobe.

8bf-host is a separate project, licensed under MIT or Apache-2.0. It is not included here; install it yourself as described above.
