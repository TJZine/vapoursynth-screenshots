"""Runtime compatibility helpers for varying VapourSynth plugin builds."""

from __future__ import annotations

from typing import Iterable

try:  # pragma: no cover - optional dependency at runtime
    from awsmfunc.types.placebo import PlaceboTonemapOpts
except Exception:  # pragma: no cover - awsmfunc may not be available yet
    PlaceboTonemapOpts = None  # type: ignore[assignment]

__all__ = [
    "UNSUPPORTED_TONEMAP_MARKERS",
    "ensure_placebo_tonemap_compat",
]

_UNSUPPORTED_TONEMAP_KEYS: tuple[str, ...] = (
    "gamut_mode",
    "tone_mapping_mode",
    "tone_mapping_crosstalk",
)

UNSUPPORTED_TONEMAP_MARKERS: tuple[str, ...] = (
    "does not take argument(s) named",
    "does not take argument named",
)


_SEEN_DROPPED_KEYS: set[str] = set()


def _describe_missing(keys: Iterable[str]) -> str:
    return ", ".join(sorted(set(keys)))


def ensure_placebo_tonemap_compat() -> None:
    """Ensure awsmfunc.DynamicTonemap works across vs-placebo builds."""

    if PlaceboTonemapOpts is None:
        return

    original = getattr(PlaceboTonemapOpts, "vsplacebo_dict", None)

    if original is None:
        return

    if getattr(PlaceboTonemapOpts.vsplacebo_dict, "__compat_wrapped__", False):
        return

    def _compat_vsplacebo_dict(self):  # type: ignore[override]
        data = original(self)

        dropped = []
        for key in _UNSUPPORTED_TONEMAP_KEYS:
            if data.get(key) is None:
                data.pop(key, None)
                dropped.append(key)

        if dropped:
            missing = _describe_missing(dropped)
            if missing not in _SEEN_DROPPED_KEYS:
                print(
                    "vs-placebo Tonemap lacks explicit support for "
                    f"{missing}; calling without those parameters."
                )
                _SEEN_DROPPED_KEYS.add(missing)

        return data

    _compat_vsplacebo_dict.__compat_wrapped__ = True  # type: ignore[attr-defined]

    PlaceboTonemapOpts.vsplacebo_dict = _compat_vsplacebo_dict  # type: ignore[assignment]

