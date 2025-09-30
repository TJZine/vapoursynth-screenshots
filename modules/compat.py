"""Runtime compatibility helpers for varying VapourSynth plugin builds."""

from __future__ import annotations

from functools import partial, wraps
from typing import Optional

try:  # pragma: no cover - optional dependency at runtime
    from awsmfunc.types.placebo import PlaceboTonemapOpts
except Exception:  # pragma: no cover - awsmfunc may not be available yet
    PlaceboTonemapOpts = None  # type: ignore[assignment]

try:  # pragma: no cover - optional dependency at runtime
    import awsmfunc
    from awsmfunc import base as awf_base
except Exception:  # pragma: no cover - awsmfunc may not be available yet
    awsmfunc = None  # type: ignore[assignment]
    awf_base = None  # type: ignore[assignment]

try:  # pragma: no cover - optional dependency at runtime
    import vapoursynth as vs
except Exception:  # pragma: no cover - vapoursynth may not be available yet
    vs = None  # type: ignore[assignment]

__all__ = [
    "UNSUPPORTED_TONEMAP_MARKERS",
    "ensure_placebo_tonemap_compat",
    "ensure_frameinfo_compat",
]

UNSUPPORTED_TONEMAP_MARKERS: tuple[str, ...] = (
    "does not take argument(s) named",
    "does not take argument named",
)


def ensure_placebo_tonemap_compat() -> None:
    """Reserved for future vs-placebo compatibility hooks."""

    # The previous implementation attempted to mutate awsmfunc's
    # ``PlaceboTonemapOpts`` instances so optional tonemapping arguments were
    # stripped when the installed ``vs-placebo`` plugin did not recognise them.
    # Unfortunately that meant modern libplacebo builds lost the very
    # parameters that control tone-mapping behaviour, resulting in HDR clips
    # passing through untonemapped.  The compatibility strategy is now handled
    # directly in :func:`modules.utils._tonemap_with_placebo`, where we can
    # inspect the actual error returned by the plugin and decide whether to
    # retry via awsmfunc's software fallback instead of silently altering the
    # request data.
    return


def ensure_frameinfo_compat() -> None:
    """Make awsmfunc.FrameInfo tolerant of string frame props on Python 3.13."""

    if awf_base is None or vs is None:
        return

    frameinfo = getattr(awf_base, "FrameInfo", None)
    subtitle_style = getattr(awf_base, "SUBTITLE_DEFAULT_STYLE", None)

    if frameinfo is None or subtitle_style is None:
        return

    if getattr(awf_base.FrameInfo, "__compat_wrapped__", False):
        return

    core = vs.core

    @wraps(frameinfo)
    def _compat_frameinfo(
        clip: "vs.VideoNode",
        title: str,
        style: Optional[str] = subtitle_style,
        newlines: int = 3,
        pad_info: bool = False,
    ) -> "vs.VideoNode":
        if style is None:
            style = subtitle_style

        def _frame_props(
            n: int,
            f: "vs.VideoFrame",
            clip: "vs.VideoNode",
            padding: Optional[str],
        ) -> "vs.VideoNode":
            props = f.props

            if hasattr(props, "get"):
                pict_type = props.get("_PictType")
            elif "_PictType" in props:
                pict_type = props["_PictType"]
            else:
                pict_type = None

            if isinstance(pict_type, bytes):
                pict_display = pict_type.decode()
            elif isinstance(pict_type, str):
                pict_display = pict_type
            elif pict_type is not None:
                pict_display = str(pict_type)
            else:
                pict_display = "N/A"

            if pict_display == "N/A":
                pict_line = "Picture type: N/A"
            else:
                pict_line = f"Picture type: {pict_display}"

            info = f"Frame {n} of {clip.num_frames}\n{pict_line}"

            if pad_info and padding:
                info_text = [padding + info]
            else:
                info_text = [info]

            return core.sub.Subtitle(clip, text=info_text, style=style)

        padding_info: Optional[str] = None

        if pad_info:
            padding_info = " " + "".join(["\n"] * newlines)
            padding_title = " " + "".join(["\n"] * (newlines + 4))
        else:
            padding_title = " " + "".join(["\n"] * newlines)

        clip = core.std.FrameEval(
            clip,
            partial(_frame_props, clip=clip, padding=padding_info),
            prop_src=clip,
        )
        clip = core.sub.Subtitle(clip, text=[padding_title + title], style=style)

        return clip

    _compat_frameinfo.__compat_wrapped__ = True  # type: ignore[attr-defined]

    awf_base.FrameInfo = _compat_frameinfo  # type: ignore[assignment]

    if awsmfunc is not None:
        setattr(awsmfunc, "FrameInfo", _compat_frameinfo)

