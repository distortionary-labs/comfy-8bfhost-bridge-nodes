# Changelog

## Unreleased

Initial version.

- **8bf Filter** and **8bf Filter (by Name)** nodes that run `.8bf` filters through
  8bf-host on `IMAGE` batches, with optional `MASK` selections.
- 8- and 16-bit runs, with 8bf-host's 8-bit fallback for 8-bit-only filters.
- Filter settings from the last run, the filter's dialog, its defaults or an 8bf-host
  preset file. The last run's settings are stored per filter.
- Configuration in `ComfyUI/user/8bfhost_bridge/config.json`, `EIGHTBF_HOST`, and filters
  in `ComfyUI/models/8bf`.
