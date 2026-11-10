"""Settings: built-in defaults < config file < TANDEM_* environment variables < command line."""
import os
import tomllib
from dataclasses import dataclass, field

MAC_BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"
DEFAULT_PATH = "~/.config/blender-tandem/config.toml"


@dataclass
class Config:
    blender: str = MAC_BLENDER        # [local] blender
    remote_host: str = ""             # [remote] host: ssh host or alias; empty = this Mac only
    remote_blender: str = MAC_BLENDER  # [remote] blender
    remote_dir: str = "tandem-jobs"   # [remote] dir: job directories on the remote, relative to its home
    remote_interface: str = ""        # [remote] interface: e.g. bridge0, reach the remote by its link-local
                                      #   address on this interface (a Thunderbolt cable) instead of by name
    remote_busy_file: str = ""        # [remote] busy_file: an extra marker file kept while the remote renders
    remote_before: str = ""           # [remote] before: shell command run on the remote before each render
    remote_after: float = 30          # [remote] start_after: seconds of local rendering before deciding
    remote_min: float = 120           # [remote] min_job: start the remote only if the local Mac alone
                                      #   would need longer than this for what is left
    remote_stall: float = 1800        # [remote] stall: reachable but no new frame for this long -> dropped
    sync: float = 10                  # [remote] sync: seconds between exchanges of frames and cursors
    yield_processes: list = field(default_factory=lambda: ["Blender"])   # [yield] processes
    yield_cpu: float = 50             # [yield] cpu: % of one core that counts as "rendering"
    yield_quiet: float = 60           # [yield] quiet: seconds below that before the local worker returns
    yield_abort: float = 20           # [yield] abort_frame: give up the frame in progress when it still
                                      #   needs more than this many seconds; otherwise let it finish


KEYS = {  # (section, key) -> Config field
    ("local", "blender"): "blender",
    ("remote", "host"): "remote_host", ("remote", "blender"): "remote_blender", ("remote", "dir"): "remote_dir",
    ("remote", "interface"): "remote_interface", ("remote", "busy_file"): "remote_busy_file",
    ("remote", "before"): "remote_before", ("remote", "start_after"): "remote_after",
    ("remote", "min_job"): "remote_min", ("remote", "stall"): "remote_stall", ("remote", "sync"): "sync",
    ("yield", "processes"): "yield_processes", ("yield", "cpu"): "yield_cpu", ("yield", "quiet"): "yield_quiet",
    ("yield", "abort_frame"): "yield_abort",
}
ENV = {"TANDEM_BLENDER": "blender", "TANDEM_REMOTE": "remote_host", "TANDEM_REMOTE_BLENDER": "remote_blender"}


def load(path=None, env=os.environ):
    """Config from `path` (default: $TANDEM_CONFIG or ~/.config/blender-tandem/config.toml), then env."""
    cfg = Config()
    explicit = path or env.get("TANDEM_CONFIG")
    path = os.path.expanduser(explicit or DEFAULT_PATH)
    if os.path.exists(path):
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        for section, values in data.items():
            if not isinstance(values, dict):
                raise SystemExit(f"{path}: '{section}' must be a [section]")
            for key, value in values.items():
                if (section, key) not in KEYS:
                    raise SystemExit(f"{path}: unknown setting [{section}] {key}")
                setattr(cfg, KEYS[(section, key)], value)
    elif explicit:
        raise SystemExit(f"config file not found: {path}")
    for name, attr in ENV.items():
        if env.get(name):
            setattr(cfg, attr, env[name])
    return cfg
