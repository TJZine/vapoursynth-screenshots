#!/usr/bin/env python3

"""
Take wonderful screenshots using VapourSynth.

This script utilizes VapourSynth to generate comparison screenshots for encoding. The script will
overlay frame info (frame count, frame type, etc.) unless specified otherwise via the
`--no_frame_info` flag.

You can pass the script a series of specific frames, or generate random frames using the
`--random_frames` argument. When comparing test encodes to the source, use the `--offset`
argument to specify a frame offset from the source so screenshots are properly aligned. If the source
is HDR/DoVi/HDR10+, the screenshots are automatically tonemapped.

Each screenshot is tagged with an alphabet character to distinguish which video it corresponds to;
for example, source screens will be '1a.png', '2a.png', encode 1 screens will be '1b.png', '2b.png',
etc. When existing screenshots are detected, the alphabet characters are incremented to prevent
overwriting if the new ones are saved to an existing directory.

--- EXAMPLES ---

Generate screenshots for test encodes with an offset of 2000 frames::

    python screenshots.py 'C:\\Path\\src.mkv' --encodes 'C:\\Path\\t1.mkv' 'C:\\Path\\t2.mkv' --offset 2000

Generate 25 random screenshotS ranging from frame 100-25000::

    python screenshots.py 'C:\\Path\\src.mkv' --encodes 'C:\\Path\\t1.mkv' --random_frames 100 25000 25

Specify an input directory containing encode files::

    python screenshots.py '~/Ex Machina 2014/ex_machina_src.mkv' --input_directory '~/Ex Machina 2014'

Use `--help` for the full list of options.

"""

import vapoursynth as vs
import awsmfunc as awf

import argparse
import re
import random
from pathlib import Path

from modules import (
    path_exists,
    verify_resize,
    load_clips,
    prepare_clips,
    SUFFIXES
)

try:
    import argcomplete
    completer = True
except ImportError:
    print("argcomplete not found. Autocomplete will be disabled on Linux shells")
    completer = False

core = vs.core


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            'CLI script for generating comparison screenshots using VapourSynth. '
            'The script can accept a variable amount of clips to process and will '
            'automatically generate frame info overlays unless instructed otherwise. '
            'Oh yeah, it automatically tonemaps HDR clips, too!'
        ),
        epilog='Awsmfunc created by OpusGang. All credit goes to them.'
    )
    if completer:
        argcomplete.autocomplete(parser)

    parser.add_argument('--source', '-s', nargs='?', metavar='SOURCE', type=path_exists,
                        help='Path to source file. Required')
    parser.add_argument('--frames', '-f', nargs='+', metavar='FRAMES', type=int,
                        help="Screenshot frames. If running tests, be sure to set '--offset'")
    parser.add_argument('--random_frames', '-r', nargs=3, metavar=('START', 'STOP', 'COUNT'), type=int,
                        help="Generate random frames in the form 'start stop count'. If running tests, be sure to set '--offset'")
    parser.add_argument('--offset', '-o', nargs='?', metavar='OFFSET', type=int, default=0,
                        help="Offset (in frames) from source. Useful for comparing test encodes")
    parser.add_argument('--crop', '-c', nargs='+', metavar='CROP', type=int,
                        help="Use custom dimensions instead of using the first encode in the form 'WIDTH HEIGHT'. All files should use the same values")
    parser.add_argument('--encodes', '-e', metavar='ENCODES', type=path_exists, nargs='+',
                        help='Paths to encoded file(s) you wish to screenshot')
    parser.add_argument('--titles', '-t', metavar='TITLES', type=str, nargs='+',
                        help='ScreenGen titles for the overlay. Should match the order of --encodes')
    parser.add_argument('--input_directory', '-d', metavar='IN_FOLDER', type=path_exists, nargs='?',
                        help='Path to folder containing encoded file(s). Replaces --encodes')
    parser.add_argument('--output_directory', '-od', metavar='OUT_FOLDER', type=Path, nargs='?',
                        help='Path where output screenshots are saved. Default behavior saves them in the parent of source')
    parser.add_argument('--resize_kernel', '-k', metavar='KERNEL', type=str, nargs='?', default='spline36',
                        help="Specify kernel used for resizing (if encodes are upscaled/downscaled). Default is 'spline36'")
    parser.add_argument('--load_filter', '-lf', type=str, choices=('lsmas', 'ffms2'), default='ffms2',
                        help="Filter used to load & index clips. Default is 'ffms2'")
    parser.add_argument('--no_frame_info', '-ni', action='store_false',
                        help="Don't add frame info overlay to clips. This flag negates the default behavior")

    args = parser.parse_args()
    print("------------------------ START ------------------------")

    # Check input
    if not args.frames and not args.random_frames:
        raise NameError(
            "No frames were provided. Specify frames via the `--frames` argument or random "
            "frames via the `--random_frames` argument."
        )
    if not args.encodes and not args.input_directory and not args.source:
        raise NameError("No files or directories were provided")

    # Set root for folder iteration
    if not args.source:
        no_src = True
        if args.encodes:
            root = args.encodes[0].parent
        else:
            root = args.input_directory
    else:
        no_src = False
        root = args.source.parent

    # Load clips from directory
    if args.input_directory:
        if no_src:
            # Try to guess what src is based on file size. Assumes same directory
            print("Loading folder clips...no source was provided. Attempting to guess based on file size")
            src_name = max([f for f in root.iterdir()], key=lambda x: x.stat().st_size).stem
            print(f"Source (best guess): {src_name}\n")
        else:
            print("Loading folder clips...")
            src_name = args.source.stem
        args.encodes = [f for f in root.iterdir() if f.suffix in SUFFIXES and f.stem != src_name]

    # Try to create output directory if passed. Else, use root
    if args.output_directory and not args.output_directory.exists():
        try:
            args.output_directory.mkdir(parents=True)
        except OSError as e:
            print(f"Failed to generate output folder: {e}. Using '{root}' instead")
            args.output_directory = root / f'screens-offset_{args.offset}'
    elif not args.output_directory:
        # don't overwrite
        screen_count = sum(1 for d in root.iterdir() if d.is_dir() and 'screens' in d.stem)
        args.output_directory = root / f'screens t{screen_count + 1}-offset_{args.offset}'
        args.output_directory.mkdir(parents=True, exist_ok=True)

    # Only encodes passed
    if no_src and args.encodes:
        files = args.encodes
    # Only src passed
    elif not args.encodes and not args.input_directory:
        files = [args.source]
    else:
        files = [args.source, *args.encodes]

    # Titles. Making assumption - probably didn't add 'Source' as a title
    if args.titles and len(files) - len(args.titles) == 1 and not no_src:
        args.titles.insert(0, 'Source')
    # Only source passed, no title
    elif not args.titles and len(files) == 1 and not no_src:
        args.titles = ['Source']
    # Set titles to file names
    elif not args.titles:
        args.titles = [str(f.stem) for f in files]

    return (files,
            args.crop,
            args.titles,
            args.output_directory,
            args.resize_kernel,
            args.no_frame_info,
            args.frames,
            args.random_frames,
            args.offset,
            args.load_filter[0] if type(args.load_filter) is list else args.load_filter,
            no_src)


def generate_screenshots(clips: list[vs.VideoNode],
                         folder: Path,
                         frames: list,
                         offset: int = None,
                         no_source: bool = False) -> None:

    """
    Generate screenshots using ScreenGen.
    :param clips: Source and encode clips to process
    :param folder: Output folder for screenshots
    :param frames: Screenshot frames
    :param offset: Frame offset from source. Used for generating test encodes
    :param no_source: Boolean indicating if source was passed
    :return: Void
    """

    chars = []
    clip_len = len(clips)

    if offset:
        src_frames = [x + offset for x in frames]
    else:
        src_frames = frames

    # Generate tags. Increment chars to prevent overwriting
    for file in folder.iterdir():
        if file.suffix in ('.jpg', '.jpeg', '.png'):
            char = ord(re.search("[A-Za-z]", file.name)[0])
            if char and char not in chars:
                chars.append(char)
    if len(chars) == 0:
        tags = [chr(ord('a') + c) for c in range(0, clip_len)]
    else:
        tags = [chr(c + clip_len) for c in chars]
        if len(tags) < clip_len:
            difference = clip_len - len(tags)
            for i in range(1, difference + 1):
                last = tags[-1]
                tags.append(chr(ord(last) + 1))

    # screenshots for source. Pop src tag to prevent conflict
    if not no_source:
        awf.ScreenGen(clips[0], folder, tags[0], frame_numbers=src_frames)
        tags.pop(0)
    for i, clip in enumerate(clips[1:]):
        awf.ScreenGen(clip, folder, tags[i], frame_numbers=frames)


def generate_random_frames(clips: list[vs.VideoNode],
                           frame_range: list[int]) -> list[int]:
    """
    Generate random frames for screenshots.

    This function takes input in the form [start, stop, count] to generate sequential
    frames randomly.

    :param clips: Encoded clips. Used to get frame counts where the smallest value is used for stop
    :param frame_range: Frame range and count in the form [start, stop, count]
    :return: A list of random, sequential frames
    """

    # Get the smallest number of frames for all clips
    frame_count = min([c.num_frames for c in clips])
    if frame_range[0] > frame_count:
        raise ValueError("random_frames: Start frame is greater than the smallest clip's end frame.")

    # Handle out-of-bounds errors if stop is greater than frame count
    stop = frame_range[1] if frame_range[1] < frame_count - 5 else frame_count - 5
    rand_frames = random.sample(range(frame_range[0], stop), frame_range[2])
    rand_frames.sort()

    return rand_frames


def main():
    (files,
     crop,
     titles,
     out_folder,
     kernel,
     overlay,
     frames,
     rand_frames,
     offset,
     load_filter,
     no_source) = parse_args()

    if no_source:
        index = 0
    else:
        index = 1
        print("Source: ", files[0])

    print("Encodes: ", files[index:])
    print(f"Frame offset: {offset}\n")

    # Load from dir or load files
    clips = load_clips(files=files, load_filter=load_filter)

    if len(clips) == 1:
        if not crop:
            if not no_source:
                print("WARNING: No crop values were provided. The source will be uncropped.")
            crop = [clips[0].width, clips[0].height]
        if rand_frames:
            frames = generate_random_frames(clips, rand_frames)
    elif len(clips) > 1:
        if rand_frames:
            frames = generate_random_frames(clips[index:], rand_frames)
        # If no crop passed, use encode 1 dimensions
        if not crop:
            crop = [clips[index].width, clips[index].height]
        if not no_source:
            # Check if source requires resizing
            clips[0] = verify_resize(clips, kernel=kernel)
    else:
        raise ValueError("The number of clips could not be determined, or an unexpected value was received.")

    # Crop, Tonemap (if applicable), and Frame Info (if applicable)
    kwargs = {
        'clips': clips,
        'crop_dimensions': crop,
        'clip_titles': titles if titles else None,
        'add_frame_info': overlay
    }
    clips = prepare_clips(**kwargs)

    generate_screenshots(clips, out_folder, frames, offset, no_source=no_source)


if __name__ == '__main__':
    main()

