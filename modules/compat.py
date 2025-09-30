"""Compatibility helpers for newer VapourSynth and libplacebo builds.

This module currently patches :func:`core.placebo.Tonemap` so that
``awsmfunc.DynamicTonemap`` can interoperate with a wider range of
``vs-placebo`` releases.  Newer versions of awsmfunc expect keyword
arguments such as ``gamut_mode`` or ``tone_mapping_mode`` to be accepted by
``Tonemap``.  However, older ``vs-placebo`` builds (commonly bundled with
VapourSynth R72 distributions) do not recognise these arguments which causes
runtime failures when the comparison helpers attempt to tonemap HDR sources.

The shim below strips unsupported keyword arguments and retries the call with
reduced parameters.  This mirrors the behaviour of newer libplacebo builds
while maintaining backwards compatibility with older binaries.
"""

from __future__ import annotations

from dataclasses import dataclass
import warnings
from typing import Dict, MutableMapping, Set

import vapoursynth as vs

__all__ = ["ensure_placebo_tonemap_compat"]


@dataclass(slots=True)
class _TonemapState:
    """Internal bookkeeping for the Tonemap compatibility shim."""

    unsupported_args: Set[str]
    patched: bool


_STATE = _TonemapState(unsupported_args=set(), patched=False)


def _extract_invalid_arguments(message: str) -> Set[str]:
    """Parse ``vs.Error`` messages for unsupported argument names."""

    markers = (
        "does not take argument(s) named",
        "does not take argument named",
    )

    for marker in markers:
        if marker in message:
            _, suffix = message.split(marker, 1)
            cleaned = (
                suffix.replace("\n", " ")
                .replace("'", "")
                .replace('"', "")
                .replace("(", "")
                .replace(")", "")
            )
            return {part.strip() for part in cleaned.split(",") if part.strip()}

    return set()


def _filter_kwargs(kwargs: MutableMapping[str, object]) -> Dict[str, object]:
    """Remove ``None`` values and previously detected unsupported keys."""

    return {
        key: value
        for key, value in kwargs.items()
        if value is not None and key not in _STATE.unsupported_args
    }


def ensure_placebo_tonemap_compat() -> None:
    """Patch :func:`core.placebo.Tonemap` to ignore unsupported keywords.

    The patch is idempotent and only performed the first time the function is
    invoked.  When no ``vs-placebo`` build is available, the call becomes a
    no-op.
    """

    if _STATE.patched:
        return

    placebo = getattr(vs.core, "placebo", None)
    tonemap = getattr(placebo, "Tonemap", None) if placebo else None

    if tonemap is None:
        # Plugin not available – nothing to patch.
        return

    if getattr(tonemap, "_vapoursynth_screenshots_wrapped", False):
        _STATE.patched = True
        return

    original = tonemap

    def _tonemap_wrapper(clip: vs.VideoNode, /, **kwargs) -> vs.VideoNode:
        filtered_kwargs = _filter_kwargs(dict(kwargs))

        while True:
            try:
                return original(clip, **filtered_kwargs)
            except vs.Error as exc:  # pragma: no cover - depends on plugin runtime
                invalid = _extract_invalid_arguments(str(exc)) & set(filtered_kwargs)

                if not invalid:
                    raise

                for name in sorted(invalid):
                    filtered_kwargs.pop(name, None)

                _STATE.unsupported_args.update(invalid)

                warnings.warn(
                    "Ignoring unsupported vs-placebo Tonemap arguments: "
                    + ", ".join(sorted(invalid)),
                    RuntimeWarning,
                    stacklevel=2,
                )

    _tonemap_wrapper.__doc__ = getattr(original, "__doc__", None)
    _tonemap_wrapper.__name__ = getattr(original, "__name__", "Tonemap")
    setattr(_tonemap_wrapper, "_vapoursynth_screenshots_wrapped", True)

    setattr(placebo, "Tonemap", _tonemap_wrapper)

    _STATE.patched = True
