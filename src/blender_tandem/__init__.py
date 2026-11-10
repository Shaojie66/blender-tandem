"""blender-tandem: render a Blender animation on two Macs at once.

    from blender_tandem import render
    result = render("shot.blend", frames="1-240")      # result.job, result.missing
"""
__version__ = "0.1.0"


def render(*args, **kwargs):
    from .cli import render as _render   # imported late so that `import blender_tandem` stays cheap
    return _render(*args, **kwargs)
