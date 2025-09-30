import vapoursynth as vs
import awsmfunc as awf
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from .compat import (
    ensure_frameinfo_compat,
    ensure_placebo_tonemap_compat,
)

ensure_frameinfo_compat()

core = vs.core

_HDR_TRANSFERS = {16, 18}
_BT2020_PRIMARIES = 9
_TRANSFER_PQ = 16
_TRANSFER_HLG = 18


@dataclass(frozen=True)
class _TonemapSettings:
    """Default configuration for libplacebo tonemapping."""

    func: str = "bt2390"
    dst_max: float = 120.0
    dst_min: float = 0.1
    dpd: bool = True
    gamut_mapping: int = 1
    smoothing_period: int = 200
    min_dynamic_peak: float = 1.0
    scene_threshold_low: float = 1.8
    scene_threshold_high: float = 5.0


_TONEMAP_SETTINGS = _TonemapSettings()

# Type hints
LOAD = Literal['ffm2', 'lsmas']
RESIZE = Literal['720p', '1080p', '1440p', '2160p']
KERNELS = Literal['bilinear', 'bicubic', 'point', 'lanczos', 'spline16', 'spline36', 'spline64']
# Constants
SUFFIXES = ['.mp4', '.mkv', '.m2ts', '.ts']
DIMENSIONS = {
    '720p': [1280, 720],
    '1080p': [1920, 1080],
    '1440p': [2560, 1440],
    '2160p': [3840, 2160]
}
KERNEL_DICT = {
    'bilinear': core.resize.Bilinear,
    'bicubic': core.resize.Bicubic,
    'point': core.resize.Point,
    'lanczos': core.resize.Lanczos,
    'spline16': core.resize.Spline16,
    'spline36': core.resize.Spline36,
    'spline64': core.resize.Spline64
}


def path_exists(path):
    if Path(path).exists():
        return Path(path)
    else:
        raise FileNotFoundError(f"The path: <{path}> does not exist")


def verify_resize(clips: list[vs.VideoNode],
                  kernel: KERNELS = 'spline36',
                  **kwargs) -> vs.VideoNode:

    """
    Verify if source requires resizing.

    Determine if the source requires resizing before cropping by calculating and comparing ratios
    between the source and the first encode passed. If encode clips of varying aspect ratios are
    passed, this function will return suboptimal results.
    :param clips: Clips to process. Clip 0 should always be the source, followed by any encodes
    :param kernel: Resizing kernel to use
    :param kwargs: Additional keyword arguments to pass to the kernel resizer
    :return: A resized source if upscale/downscale is detected. Else, the source is returned untouched
    """

    # Quick check to verify there aren't multiple different ARs
    if len(clips) > 2:
        ars = set([(e.width / e.height) for e in clips[1:]])
        if len(ars) > 1:
            raise ValueError("Cannot process encoded clips with different aspect ratios")

    src_width, src_height = clips[0].width, clips[0].height
    enc_width, enc_height = clips[1].width, clips[1].height

    kernel = KERNEL_DICT[kernel.lower()]

    # Downscale. Try to account for column cropping
    if src_width - enc_width > 600:
        type_scale = 'Downscale'
        if src_width // enc_width == 2:
            resized_width, resized_height = DIMENSIONS['1080p']
        elif src_width // enc_width == 3 or src_width // enc_width == 1:
            resized_width, resized_height = DIMENSIONS['720p']
        else:
            raise ValueError(
                f"Unable to determine downscale resizing ratio for dimensions '{enc_width}x{enc_height}'."
            )
    # Upscale. Try to account for column cropping
    elif enc_width - src_width > 600:
        type_scale = 'Upscale'
        if enc_width // src_width == 2 or enc_width // src_width == 3:
            resized_width, resized_height = DIMENSIONS['2160p']
        elif enc_width // src_width == 1:
            resized_width, resized_height = DIMENSIONS['1080p']
        else:
            raise ValueError(
                f"Unable to determine upscale resizing ratio for dimensions '{enc_width}x{enc_height}'."
            )
    # No resizing
    else:
        return clips[0]

    print(
        f"{type_scale} detected.\nSource dimensions: {src_width}x{src_height}"
        f"\nEncode dimensions: {enc_width}x{enc_height}\nResizing kernel: {kernel}"
    )

    return kernel(clip=clips[0], width=resized_width, height=resized_height, **kwargs)


def crop_file(clip: vs.VideoNode,
              width: int,
              height: int,
              mod_crop: int = 2) -> vs.VideoNode:
    """
    Function for cropping files before processing.
    :param clip: Clip to crop
    :param width: Crop width
    :param height: Crop height
    :param mod_crop: Crop video in accordance to the modulus value specified
    :return: Cropped clip
    """

    src_width, src_height = clip.width, clip.height

    top = bottom = math.ceil((src_height - height) / 2)
    left = right = math.ceil((src_width - width) / 2)

    # Meet requirement for mod cropping specified by mod_crop
    while top % mod_crop != 0:
        top += 1
        bottom += 1

    while right % mod_crop != 0:
        right += 1
        left += 1

    print(f"Crop values:\nLeft: {left}\nRight: {right}\nTop: {top}\nBottom: {bottom}")
    dim_width = src_width - (left + right)
    dim_height = src_height - (top + bottom)
    print(f"Input Dimensions: {src_width}x{src_height}")
    print(f"Cropped Dimensions: {dim_width}x{dim_height}\n")

    return core.std.Crop(clip, left, right, top, bottom)


def load_clips(files: list = None,
               folder: Path = None,
               source_name: str = None,
               load_filter: LOAD = 'ffms2') -> list[vs.VideoNode]:

    """
    Load clips for processing.

    This function converts file paths to VapourSynth clips. Clips can be loaded using either
    ffms2 or lsmas as set by the `load_filter` argument. Default is ffms2 because it is needed
    for use with dynamic tonemapping.

    :param files: List of filepaths to load as clips
    :param folder: A folder containing files to load as clips
    :param source_name: Source file's name. Used to distinguish source from encodes
    :param load_filter: Filter used to load clips. Default is ffm2
    :return: A list of loaded clips
    """

    if not files and not folder:
        raise NameError(
            "No files were provided. Pass a list of file paths or a folder containing files to load"
        )

    if load_filter == 'ffms2':
        load_filter = core.ffms2.Source
        suffix = '.ffindex'
    elif load_filter == 'lsmas':
        load_filter = core.lsmas.LWLibavSource
        suffix = '.lwi'
    else:
        raise ValueError("Unknown load filter specified. Options are 'ffms2' and 'lsmas'")

    if folder and source_name:
        print("\nLoading folder clips...")
        files = [f for f in folder.iterdir() if f.suffix in SUFFIXES and f.stem != source_name]
    elif folder and not source_name:
        print("\nLoading folder clips...no source was provided. Attempting to guess based on file size")
        # Try to guess what src is based on file size. Assumes same directory
        src = max([f for f in folder.iterdir()], key=lambda x: x.stat().st_size)
        files = [f for f in folder.iterdir() if f.suffix in SUFFIXES and f.stem != src.stem]

    clips = [load_filter(f, cachefile=f.with_suffix(suffix)) for f in files]

    return clips


def prepare_clips(clips: list[vs.VideoNode],
                  crop_dimensions: list[int, int],
                  clip_titles: list[str] = None,
                  add_frame_info: bool = True) -> list[vs.VideoNode]:

    """
    Helper function used to prepare clips for comparison or screenshots.

    Prepare clips for usage through the following steps:

    - Crop files using provided dimensions
    - If input clips are HDR, tonemap them
    - If titles were provided, zip them with their clips
    - If frame info overlays are desired, add them

    :param clips: Clips to process. The first clip should always be the source
    :param crop_dimensions: Dimensions used for cropping clips
    :param clip_titles: Titles for frame info overlays. The length of titles must match the length of clips
    :param add_frame_info: Boolean for adding frame info overlay. Default enabled
    :return: List of prepared clips
    """

    # Crop clips
    clips = [crop_file(c, width=crop_dimensions[0], height=crop_dimensions[1]) for c in clips]

    props = _first_frame_props(clips[0])

    if _is_hdr_clip(props):
        _ensure_placebo_tonemap_support()

        tonemapped_clips: list[vs.VideoNode] = []
        for clip in clips:
            tonemapped_clips.append(_process_hdr_clip(clip))

        clips = tonemapped_clips
    else:
        clips = [_convert_to_rgb24(clip) for clip in clips]

    # Zip together clips and titles if present
    if clip_titles:
        if len(clip_titles) != len(clips):
            print("WARNING: The number of titles does not match the number of clips\n")
            zipped = False
        else:
            clips = zip(clips, clip_titles)
            zipped = True
    else:
        zipped = False

    # Add frame info overlay unless specified otherwise
    if add_frame_info and zipped:
        clips = list(clips)
        clips = [awf.FrameInfo(c[0], c[1]) for c in clips]
    elif add_frame_info:
        clips = [awf.FrameInfo(c, f"Clip {i}") for i, c in enumerate(clips)]
    else:
        print("Frame overlay disabled")

    return clips


def get_dimensions(resolution: str | int, clip: vs.VideoNode = None) -> list[int, int]:
    """
    Helper function which returns dimensions for common resolutions
    :param resolution: Common resolution, such as 1080 or '1080p'
    :param clip: Reference clip to scale dimensions from based on preset 'resolution'
    :return: Integer dimensions as a list
    """

    if isinstance(resolution, int):
        resolution = str(resolution)

    if '720' in resolution:
        dimensions = DIMENSIONS['720p']
        num = 720
    elif '1080' in resolution:
        dimensions = DIMENSIONS['1080p']
        num = 1080
    elif '1440' in resolution:
        dimensions = DIMENSIONS['1440p']
        num = 1440
    elif '2160' in resolution:
        dimensions = DIMENSIONS['2160p']
        num = 2160
    else:
        raise ValueError(f"Unknown resolution: {resolution}")

    if clip:
        resized = awf.zresize(clip, preset=num)
        dimensions = [resized.width, resized.height]

    return dimensions


def _ensure_placebo_tonemap_support() -> None:
    """Validate that a modern vs-placebo Tonemap implementation is available."""

    ensure_placebo_tonemap_compat()

    placebo = getattr(core, "placebo", None)
    tonemap = getattr(placebo, "Tonemap", None) if placebo else None

    if tonemap is None:
        raise RuntimeError(
            "HDR content detected but the vs-placebo plugin is not available. "
            "Install a recent vs-placebo build to enable libplacebo tonemapping."
        )


def _first_frame_props(clip: vs.VideoNode) -> "vs.VideoFrameProps":
    return clip.get_frame(0).props


def _read_prop(props: "vs.VideoFrameProps", key: str) -> Optional[int]:
    value: Optional[int]
    if hasattr(props, "get"):
        value = props.get(key)  # type: ignore[assignment]
    elif key in props:
        value = props[key]
    else:
        value = None

    if value is None:
        return None

    if isinstance(value, (bytes, bytearray)):
        try:
            decoded = value.decode()
        except Exception:
            return None
        try:
            return int(decoded)
        except ValueError:
            return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_hdr_clip(props: "vs.VideoFrameProps") -> bool:
    transfer = _read_prop(props, "_Transfer")
    primaries = _read_prop(props, "_Primaries")

    return transfer in _HDR_TRANSFERS and primaries == _BT2020_PRIMARIES


def _convert_to_rgb48(
    clip: vs.VideoNode,
    props: Optional["vs.VideoFrameProps"] = None,
) -> vs.VideoNode:
    if props is None:
        props = _first_frame_props(clip)

    if clip.format is not None and clip.format.color_family == vs.RGB and clip.format.bits_per_sample == 16:
        return clip

    resize_kwargs = {"format": vs.RGB48}

    matrix = _read_prop(props, "_Matrix")
    transfer = _read_prop(props, "_Transfer")
    primaries = _read_prop(props, "_Primaries")
    color_range = _read_prop(props, "_ColorRange")

    if matrix is not None:
        resize_kwargs["matrix_in"] = matrix
    if transfer is not None:
        resize_kwargs["transfer_in"] = transfer
    if primaries is not None:
        resize_kwargs["primaries_in"] = primaries
    if color_range is not None:
        resize_kwargs["range_in"] = color_range

    return core.resize.Spline36(clip, **resize_kwargs)


def _convert_to_rgb24(
    clip: vs.VideoNode,
    props: Optional["vs.VideoFrameProps"] = None,
) -> vs.VideoNode:
    if clip.format is not None and clip.format.color_family == vs.RGB and clip.format.bits_per_sample == 8:
        return clip

    if props is None:
        props = _first_frame_props(clip)

    resize_kwargs = {
        "format": vs.RGB24,
        "dither_type": "error_diffusion",
    }

    matrix = _read_prop(props, "_Matrix")
    transfer = _read_prop(props, "_Transfer")
    primaries = _read_prop(props, "_Primaries")
    color_range = _read_prop(props, "_ColorRange")

    if matrix is not None:
        resize_kwargs["matrix_in"] = matrix
    if transfer is not None:
        resize_kwargs["transfer_in"] = transfer
    if primaries is not None:
        resize_kwargs["primaries_in"] = primaries
    if color_range is not None:
        resize_kwargs["range_in"] = color_range

    return core.resize.Spline36(clip, **resize_kwargs)


def _normalize_props_for_placebo_rgb16(
    clip: vs.VideoNode,
    props: "vs.VideoFrameProps",
) -> vs.VideoNode:
    kwargs = {
        "_Matrix": 0,
        "_ColorRange": 0,
    }

    transfer = _read_prop(props, "_Transfer")
    primaries = _read_prop(props, "_Primaries")

    if transfer is not None:
        kwargs["_Transfer"] = transfer
    if primaries is not None:
        kwargs["_Primaries"] = primaries

    return core.std.SetFrameProps(clip, **kwargs)


def _deduce_src_csp_from_props(props: "vs.VideoFrameProps") -> Optional[int]:
    transfer = _read_prop(props, "_Transfer")
    primaries = _read_prop(props, "_Primaries")

    if primaries != _BT2020_PRIMARIES:
        return None

    if transfer == _TRANSFER_PQ:
        return 1
    if transfer == _TRANSFER_HLG:
        return 2

    return None


def _apply_tonemap_props(clip: vs.VideoNode) -> vs.VideoNode:
    settings = _TONEMAP_SETTINGS
    tonemap_prop = (
        f"placebo:{settings.func},dpd={str(settings.dpd).lower()},dst_max={settings.dst_max}"
    )
    clip = core.std.SetFrameProps(clip, _Tonemapped=tonemap_prop)
    print(
        "[libplacebo] HDR->SDR tonemap applied using function '",
        f"{settings.func}' (dpd={settings.dpd}, dst_max={settings.dst_max}).",
        sep="",
    )
    return clip


def _tonemap_with_retries(
    clip: vs.VideoNode,
    src_csp_hint: Optional[int],
) -> vs.VideoNode:
    placebo = getattr(core, "placebo", None)
    tonemap = getattr(placebo, "Tonemap", None) if placebo else None

    if tonemap is None:
        raise RuntimeError("vs-placebo Tonemap is not available")

    settings = _TONEMAP_SETTINGS
    base_kwargs = dict(
        dst_csp=0,
        dst_prim=1,
        dst_max=settings.dst_max,
        dst_min=settings.dst_min,
        dynamic_peak_detection=settings.dpd,
        gamut_mapping=settings.gamut_mapping,
        tone_mapping_function_s=settings.func,
        use_dovi=True,
        smoothing_period=settings.smoothing_period,
        min_dynamic_peak=settings.min_dynamic_peak,
        scene_threshold_low=settings.scene_threshold_low,
        scene_threshold_high=settings.scene_threshold_high,
    )

    attempts: list[dict] = []
    if src_csp_hint is not None:
        attempt_kwargs = base_kwargs.copy()
        attempt_kwargs["src_csp"] = src_csp_hint
        attempts.append(attempt_kwargs)
    attempts.append(base_kwargs)
    forced_pq = base_kwargs.copy()
    forced_pq["src_csp"] = 1
    attempts.append(forced_pq)

    last_exc: Optional[Exception] = None

    for index, kwargs in enumerate(attempts, start=1):
        try:
            tonemapped = tonemap(clip, **kwargs)
        except vs.Error as exc:
            last_exc = exc
            print(f"[Tonemap attempt {index} failed] {exc}")
            continue
        else:
            return _apply_tonemap_props(tonemapped)

    if last_exc is not None:
        raise last_exc

    raise RuntimeError("Unknown error during tonemap attempts")


def _finalize_rgb24(clip: vs.VideoNode) -> vs.VideoNode:
    return core.resize.Spline36(
        clip,
        format=vs.RGB24,
        dither_type="error_diffusion",
    )


def _process_hdr_clip(clip: vs.VideoNode) -> vs.VideoNode:
    props = _first_frame_props(clip)

    try:
        rgb16 = _convert_to_rgb48(clip, props)
        normalized = _normalize_props_for_placebo_rgb16(rgb16, props)
        src_csp_hint = _deduce_src_csp_from_props(props)
        tonemapped = _tonemap_with_retries(normalized, src_csp_hint)
    except Exception as exc:
        print(f"[ERROR] Color processing failed ({exc}). Falling back to SDR conversion.")
        return _convert_to_rgb24(clip, props)

    return _finalize_rgb24(tonemapped)
