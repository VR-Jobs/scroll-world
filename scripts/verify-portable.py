#!/usr/bin/env python3
"""Verify that a Scroll World folder works from file:// and unchanged over HTTP(S)."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import unquote, urlsplit


VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}
TEXT_SUFFIXES = {".html", ".css", ".js", ".json"}
RESOURCE_ATTRS = {
    "audio": {"src"},
    "embed": {"src"},
    "iframe": {"src"},
    "img": {"src", "srcset"},
    "input": {"src"},
    "link": {"href"},
    "object": {"data"},
    "script": {"src"},
    "source": {"src", "srcset"},
    "track": {"src"},
    "video": {"src", "poster"},
}
CSS_URL_RE = re.compile(r"url\(\s*([\"']?)(.*?)\1\s*\)", re.IGNORECASE)
ASSET_LITERAL_RE = re.compile(
    r"[\"']((?:\.?\.?/)?[^\"'?#]+\.(?:avif|css|gif|html?|ico|jpe?g|js|json|mp4|mov|png|svg|webm|webp|woff2?)(?:[?#][^\"']*)?)[\"']",
    re.IGNORECASE,
)
DIRECT_VIDEO_RE = re.compile(r"\bdirectVideo\s*:\s*true\b")
DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
ABSOLUTE_FILE_LITERAL_RE = re.compile(r"[\"'`]\s*(?:/(?:Users|home|var|private|assets)/|[A-Za-z]:[\\/])")


class ResourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[tuple[str, str, str]] = []
        self.module_scripts = 0
        self.base_hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        tag = tag.lower()
        if tag == "script" and values.get("type", "").strip().lower() == "module":
            self.module_scripts += 1
        if tag == "base" and values.get("href"):
            self.base_hrefs.append(values["href"])
        for name in RESOURCE_ATTRS.get(tag, set()):
            value = values.get(name)
            if not value:
                continue
            if name == "srcset":
                for candidate in value.split(","):
                    url = candidate.strip().split()[0] if candidate.strip() else ""
                    if url:
                        self.references.append((tag, name, url))
            else:
                self.references.append((tag, name, value.strip()))


def relative_target(root: Path, base: Path, raw: str, context: str, errors: list[str]) -> Path | None:
    value = raw.strip()
    if not value or value.startswith(("#", "data:", "blob:", "mailto:", "tel:", "javascript:")):
        return None
    split = urlsplit(value)
    if split.scheme or split.netloc:
        errors.append(f"external/server-dependent resource in {context}: {value}")
        return None
    decoded = unquote(split.path)
    if not decoded:
        return None
    if decoded.startswith(("/", "\\")) or DRIVE_PATH_RE.match(decoded):
        errors.append(f"resource must use a relative path in {context}: {value}")
        return None
    target = (base / decoded).resolve()
    if target != root and root not in target.parents:
        errors.append(f"resource escapes the delivery folder in {context}: {value}")
        return None
    if not target.is_file():
        errors.append(f"missing local resource in {context}: {value}")
        return None
    return target


def add_reference(
    root: Path,
    base: Path,
    raw: str,
    context: str,
    errors: list[str],
    references: set[Path],
) -> None:
    target = relative_target(root, base, raw, context, errors)
    if target is not None:
        references.add(target)


def probe_video(root: Path, path: Path, errors: list[str]) -> dict:
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,pix_fmt,width,height:format=duration",
        "-of", "json", str(path),
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        errors.append(f"ffprobe failed for {path.name}: {result.stderr.strip() or 'unknown error'}")
        return {"path": path.name, "status": "fail"}
    try:
        payload = json.loads(result.stdout)
        stream = payload.get("streams", [{}])[0]
        duration = float((payload.get("format") or {}).get("duration") or 0)
    except (ValueError, TypeError, IndexError, json.JSONDecodeError):
        errors.append(f"invalid ffprobe metadata: {path.name}")
        return {"path": path.name, "status": "fail"}
    codec = stream.get("codec_name")
    pixel_format = stream.get("pix_fmt")
    valid = duration > 0
    if path.suffix.lower() in {".mp4", ".mov"} and codec != "h264":
        errors.append(f"browser delivery video must be H.264: {path.name} ({codec or 'unknown'})")
        valid = False
    if path.suffix.lower() in {".mp4", ".mov"} and pixel_format not in {"yuv420p", "yuvj420p"}:
        errors.append(f"browser delivery video must use yuv420p: {path.name} ({pixel_format or 'unknown'})")
        valid = False
    if duration <= 0:
        errors.append(f"video has no positive duration: {path.name}")
    return {
        "path": str(path.relative_to(root)),
        "codec": codec,
        "pixel_format": pixel_format,
        "width": stream.get("width"),
        "height": stream.get("height"),
        "duration_seconds": duration,
        "status": "pass" if valid else "fail",
    }


def verify(root: Path, entry_name: str, probe_media: bool = True) -> tuple[dict, list[str]]:
    root = root.resolve()
    errors: list[str] = []
    entry = relative_target(root, root, entry_name, "entry", errors)
    if entry is None:
        return {"schema_version": 1, "status": "fail", "entry": entry_name}, errors
    html = entry.read_text(encoding="utf-8", errors="replace")
    parser = ResourceParser()
    parser.feed(html)
    if parser.base_hrefs:
        errors.append("<base href> is not allowed because it changes local/cloud relative resolution")
    if parser.module_scripts:
        errors.append('script type="module" is not allowed in the portable file:// entry')
    if not DIRECT_VIDEO_RE.search(html):
        errors.append("portable Scroll World entry must set directVideo: true")
    references: set[Path] = {entry}
    for tag, attr, raw in parser.references:
        if tag == "link" and attr == "href":
            # HTMLParser already exposes the reference; non-resource <a> links are never collected.
            if raw.startswith(("http://", "https://", "//")):
                errors.append(f"external stylesheet/icon is not portable: {raw}")
                continue
        add_reference(root, entry.parent, raw, f"<{tag} {attr}>", errors, references)

    # Inline configuration contains clip/poster paths as JavaScript string literals.
    for match in ASSET_LITERAL_RE.findall(html):
        add_reference(root, entry.parent, match, "index.html inline configuration", errors, references)
    for _, raw in CSS_URL_RE.findall(html):
        add_reference(root, entry.parent, raw, "index.html inline CSS", errors, references)

    inspected: set[Path] = set()
    while True:
        pending = [path for path in references if path not in inspected and path.suffix.lower() in TEXT_SUFFIXES]
        if not pending:
            break
        for path in pending:
            inspected.add(path)
            text = path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"[\"'`]\s*(?:file://|https?://(?:localhost|127\.0\.0\.1)(?::\d+)?)", text):
                errors.append(f"local-machine URL is not portable: {path.relative_to(root)}")
            if ABSOLUTE_FILE_LITERAL_RE.search(text):
                errors.append(f"absolute filesystem/root path is not portable: {path.relative_to(root)}")
            if re.search(r"navigator\.serviceWorker\s*\.\s*register", text):
                errors.append(f"service worker dependency is not portable: {path.relative_to(root)}")
            if path.suffix.lower() == ".css":
                for _, raw in CSS_URL_RE.findall(text):
                    add_reference(root, path.parent, raw, f"CSS {path.relative_to(root)}", errors, references)
            elif path.suffix.lower() in {".js", ".json"}:
                if path.suffix.lower() == ".js" and re.search(r"^\s*(?:import|export)\s|\bimport\s*\(", text, re.MULTILINE):
                    errors.append(f"ES module syntax is not portable under file://: {path.relative_to(root)}")
                for raw in ASSET_LITERAL_RE.findall(text):
                    # Runtime asset strings resolve against document.baseURI, not the script file.
                    base = entry.parent if path.suffix.lower() == ".js" else path.parent
                    add_reference(root, base, raw, f"{path.suffix[1:].upper()} {path.relative_to(root)}", errors, references)

    videos = sorted((path for path in references if path.suffix.lower() in VIDEO_SUFFIXES), key=str)
    probes: list[dict] = []
    if videos and probe_media:
        if not shutil.which("ffprobe"):
            errors.append("ffprobe is required to verify portable video codecs")
        else:
            probes = [probe_video(root, path, errors) for path in videos]

    relative_files = sorted(str(path.relative_to(root)) for path in references)
    report = {
        "schema_version": 1,
        "status": "fail" if errors else "pass",
        "entry": str(entry.relative_to(root)),
        "supported_launch_modes": ["file:// double-click", "http://", "https://"],
        "direct_video": bool(DIRECT_VIDEO_RE.search(html)),
        "resource_count": len(relative_files),
        "resources": relative_files,
        "video_probes": probes,
        "errors": errors,
    }
    return report, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="dist", help="portable delivery folder")
    parser.add_argument("--entry", default="index.html", help="entry HTML relative to --root")
    parser.add_argument("--json-output", help="optional report path")
    parser.add_argument("--skip-media-probe", action="store_true", help="tests only; production builds must not use this")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    report, errors = verify(root, args.entry, probe_media=not args.skip_media_probe)
    if args.json_output:
        output = Path(args.json_output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit("portable delivery failed:\n- " + "\n- ".join(errors))
    print(f"portable delivery passed: {root / args.entry}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
