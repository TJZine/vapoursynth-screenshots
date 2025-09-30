"""Runtime compatibility helpers for varying VapourSynth plugin builds."""

from __future__ import annotations

from typing import Iterable

import vapoursynth as vs

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


class _PlaceboProxy:
    """Proxy around a vs-placebo plugin object with a patched Tonemap."""

    __slots__ = ("_plugin", "Tonemap")

    def __init__(self, plugin: vs.Plugin, tonemap: vs.Function) -> None:
        self._plugin = plugin
        self.Tonemap = tonemap

    def __getattr__(self, name: str):
        return getattr(self._plugin, name)


def _describe_missing(keys: Iterable[str]) -> str:
    return ", ".join(sorted(set(keys)))


def ensure_placebo_tonemap_compat() -> None:
    """Ensure awsmfunc.DynamicTonemap works across vs-placebo builds."""

    core = vs.core
    placebo = getattr(core, "placebo", None)

    if placebo is None:
        return

    if isinstance(placebo, _PlaceboProxy):
        return

    tonemap = getattr(placebo, "Tonemap", None)

    if tonemap is None:
        return

    seen: set[str] = set()

    def _tonemap_wrapper(*args, **kwargs):  # type: ignore[override]
        try:
            return tonemap(*args, **kwargs)
        except vs.Error as exc:  # pragma: no cover - depends on plugin runtime
            message = str(exc)

            if any(marker in message for marker in UNSUPPORTED_TONEMAP_MARKERS):
                dropped = [
                    key for key in _UNSUPPORTED_TONEMAP_KEYS if key in kwargs
                ]

                if dropped:
                    filtered_kwargs = {
                        key: value for key, value in kwargs.items() if key not in dropped
                    }

                    missing = _describe_missing(dropped)

                    if missing not in seen:
                        print(
                            "vs-placebo Tonemap is missing support for "
                            f"{missing}; retrying without those parameters."
                        )
                        seen.add(missing)

                    return tonemap(*args, **filtered_kwargs)

            raise

    proxy = _PlaceboProxy(placebo, _tonemap_wrapper)
    setattr(core, "placebo", proxy)

