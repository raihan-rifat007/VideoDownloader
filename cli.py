import argparse
import json
import sys

import core


def _emit(obj, as_json, human):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False))
    else:
        human(obj)


def _cmd_download(args):
    if args.name and len(args.urls) > 1:
        raise core.ReclipError("--name can only be used with a single URL")
    if args.audio and args.quality is not None:
        raise core.ReclipError("--quality does not apply to --audio downloads")
    kind = "audio" if args.audio else "video"
    if len(args.urls) == 1:
        result = core.download(args.urls[0], kind=kind, quality=args.quality,
                               output_dir=args.output, name=args.name,
                               timeout=args.timeout)
        _emit(result, args.json, lambda r: print(f"Saved: {r['file']}"))
        return 0

    results = []
    for url in args.urls:
        try:
            results.append(core.download(url, kind=kind, quality=args.quality,
                                         output_dir=args.output,
                                         timeout=args.timeout))
        except core.ReclipError as e:
            results.append({"url": url, "error": str(e)})

    def human(_):
        for r in results:
            if "error" in r:
                print(f"error: {r['url']}: {r['error']}", file=sys.stderr)
            else:
                print(f"Saved: {r['file']}")
    _emit(results, args.json, human)
    return 1 if any("error" in r for r in results) else 0


def _cmd_info(args):
    info = core.probe(args.url)

    def human(o):
        print(f"Title: {o['title']}")
        if o.get("uploader"):
            print(f"Uploader: {o['uploader']}")
        if o.get("duration") is not None:
            print(f"Duration: {o['duration']}s")
        if o["formats"]:
            print("Qualities: " + ", ".join(f["label"] for f in o["formats"]))
    _emit(info, args.json, human)


def _cmd_playlist(args):
    urls = core.expand_playlist(args.url)
    _emit({"urls": urls}, args.json, lambda _: [print(u) for u in urls])


def _cmd_transcript(args):
    result = core.transcript(args.url, lang=args.lang)

    def human(o):
        if o["text"] is None:
            langs = ", ".join(o["available_langs"]) or "none"
            print(f"No subtitles for that language. Available: {langs}", file=sys.stderr)
        else:
            print(o["text"])
    _emit(result, args.json, human)


def _build_parser():
    parser = argparse.ArgumentParser(prog="reclip",
                                     description="Download media and read transcripts via yt-dlp.")
    sub = parser.add_subparsers(dest="command", required=True)

    d = sub.add_parser("download", help="Download one or more URLs")
    d.add_argument("urls", nargs="+")
    mode = d.add_mutually_exclusive_group()
    mode.add_argument("--audio", action="store_true", help="Extract MP3 audio")
    mode.add_argument("--video", action="store_true", help="Download MP4 video (default)")
    d.add_argument("--quality", type=int,
                   help="Cap video height, e.g. 1080 (video only)")
    d.add_argument("-o", "--output", default=".", help="Output directory")
    d.add_argument("--name", help="Output filename (single URL only)")
    d.add_argument("--timeout", type=int, default=core.DOWNLOAD_TIMEOUT,
                   help=f"Per-download timeout in seconds (default {core.DOWNLOAD_TIMEOUT})")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=_cmd_download)

    i = sub.add_parser("info", help="Show metadata and available qualities")
    i.add_argument("url")
    i.add_argument("--json", action="store_true")
    i.set_defaults(func=_cmd_info)

    p = sub.add_parser("playlist", help="Expand a playlist to its video URLs")
    p.add_argument("url")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_playlist)

    t = sub.add_parser("transcript", help="Fetch subtitles as text")
    t.add_argument("url")
    t.add_argument("--lang", default="en", help="Subtitle language (default en)")
    t.add_argument("--json", action="store_true")
    t.set_defaults(func=_cmd_transcript)

    return parser


def main(argv=None):
    """Run the ReClip command line and return its process exit status."""
    args = _build_parser().parse_args(argv)
    try:
        rc = args.func(args)
        return 0 if rc is None else rc
    except core.ReclipError as e:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(e)}))
        else:
            print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
