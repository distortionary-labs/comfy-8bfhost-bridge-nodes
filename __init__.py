"""ComfyUI loads custom nodes from this file."""

# pytest imports this file as a plain module while collecting the tests,
# where the relative import cannot work. ComfyUI loads it as a package.
if __package__:
    from .comfy_8bfhost_bridge.nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

    __all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
