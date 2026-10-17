"""Publish bounded Protomaps Basemap archives as GitHub release assets.

Reads regions.json, finds the newest daily Protomaps planet build, extracts each
region with `pmtiles extract` (range reads; only the region is transferred), splits
any extract larger than the size limit into overlapping halves, uploads each file to
a draft release, writes index.json and publishes the release as latest.

Runs on a GitHub Actions runner (normal internet access). Standard library only;
needs the `pmtiles` and `gh` CLIs on PATH and GH_TOKEN with contents: write.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "basemaps"
REPO = os.environ["GITHUB_REPOSITORY"]
PLANET = "https://build.protomaps.com/{date}.pmtiles"


def log(message: str) -> None:
    print(message, flush=True)


def run(*command: str, capture: bool = False) -> str:
    log("$ " + " ".join(command))
    result = subprocess.run(command, check=True, text=True, capture_output=capture)
    return result.stdout if capture else ""


def available(date: str) -> bool:
    """True when the planet build answers a one-byte range read."""
    request = urllib.request.Request(
        PLANET.format(date=date),
        headers={"Range": "bytes=0-0", "User-Agent": "market-maps-basemaps/1 (+github.com/" + REPO + ")"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status in (200, 206)
    except (urllib.error.URLError, TimeoutError) as error:
        log(f"{date}: {error}")
        return False


def planet_date() -> str:
    """Newest available daily build, or PLANET_DATE when pinned."""
    pinned = os.environ.get("PLANET_DATE", "").strip()
    if pinned:
        return pinned
    try:
        request = urllib.request.Request(
            "https://build-metadata.protomaps.dev/builds.json",
            headers={"User-Agent": "market-maps-basemaps/1"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            builds = json.load(response)
        dates = sorted(
            (str(build.get("key", "")).removesuffix(".pmtiles") for build in builds),
            reverse=True,
        )
        for date in dates:
            if len(date) == 8 and date.isdigit() and available(date):
                return date
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        log(f"Build list unavailable: {error}")
    today = datetime.now(UTC).date()
    for back in range(0, 21):
        date = (today - timedelta(days=back)).strftime("%Y%m%d")
        if available(date):
            return date
    raise RuntimeError("No Protomaps planet build found in the last 21 days")


def header(path: Path | str) -> dict[str, str]:
    fields: dict[str, str] = {}
    # Header lines read "max zoom: 15"; metadata lines read "version 4.13.4".
    for line in run("pmtiles", "show", str(path), capture=True).splitlines():
        key, _, value = line.partition(": ") if ": " in line else line.partition(" ")
        fields[key.strip()] = value.strip()
    return fields


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def split(bbox: list[float], overlap: float) -> list[list[float]]:
    west, south, east, north = bbox
    if east - west >= north - south:
        middle = (west + east) / 2
        return [[west, south, middle + overlap, north], [middle - overlap, south, east, north]]
    middle = (south + north) / 2
    return [[west, south, east, middle + overlap], [west, middle - overlap, east, north]]


UNITS = {"B": 1, "kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}


def estimate(source: str, bbox: list[float], maxzoom: int) -> float:
    """Archive size in bytes reported by `pmtiles extract --dry-run` (nothing is written)."""
    result = subprocess.run(
        ["pmtiles", "extract", source, str(WORK / "dry-run.pmtiles"), "--dry-run",
         "--bbox=" + ",".join(f"{value:.4f}" for value in bbox), f"--maxzoom={maxzoom}"],
        check=True, text=True, capture_output=True,
    )
    match = re.search(r"archive size of ([0-9.]+) ([kKMGT]?B)", result.stdout + result.stderr)
    if not match:
        raise RuntimeError("pmtiles dry run reported no archive size")
    return float(match.group(1)) * UNITS[match.group(2)]


def extract(source: str, name: str, bbox: list[float], config: dict) -> list[tuple[str, list[float], Path]]:
    """Extract a region, splitting first (by dry-run estimate) so every file fits the limit."""
    limit = config["max_file_mb"] * 1024 * 1024
    size = estimate(source, bbox, config["maxzoom"])
    log(f"{name}: estimated {size / 1048576:.0f} MB")
    if size <= limit:
        target = WORK / f"{name}.pmtiles"
        run(
            "pmtiles", "extract", source, str(target),
            "--bbox=" + ",".join(f"{value:.4f}" for value in bbox),
            f"--maxzoom={config['maxzoom']}",
        )
        if target.stat().st_size <= limit:
            return [(name, bbox, target)]
        target.unlink()
    log(f"{name} exceeds {config['max_file_mb']} MB; splitting")
    parts: list[tuple[str, list[float], Path]] = []
    for index, half in enumerate(split(bbox, config["split_overlap_degrees"]), start=1):
        parts.extend(extract(source, f"{name}-{index}", half, config))
    return parts


def main() -> None:
    config = json.loads((ROOT / "regions.json").read_text())
    WORK.mkdir(parents=True, exist_ok=True)
    date = planet_date()
    source = PLANET.format(date=date)
    planet = header(source)
    log(f"Planet {date}: schema {planet.get('version')}")
    tag = f"planet-{date}-run{os.environ.get('GITHUB_RUN_NUMBER', '0')}"
    run(
        "gh", "release", "create", tag, "--repo", REPO, "--draft",
        "--title", f"Basemaps from Protomaps planet {date}",
        "--notes",
        f"Bounded Protomaps Basemap archives extracted from the {date} daily planet build "
        "(https://build.protomaps.com). Map data © OpenStreetMap contributors, ODbL. "
        "See index.json for coverage.",
    )
    files = []
    for region in config["regions"]:
        for name, bbox, path in extract(source, region["name"], region["bbox"], config):
            info = header(path)
            entry = {
                "name": name,
                "region": region["name"],
                "title": region["title"],
                "bbox": [round(value, 4) for value in bbox],
                "maxzoom": int(info.get("max zoom", config["maxzoom"])),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "url": f"https://github.com/{REPO}/releases/download/{tag}/{path.name}",
            }
            run("gh", "release", "upload", tag, str(path), "--repo", REPO, "--clobber")
            path.unlink()
            files.append(entry)
            log(f"Published {name}: {entry['bytes'] // 1048576} MB")
    index = {
        "schema_version": 1,
        "release": tag,
        "built_at": datetime.now(UTC).isoformat(),
        "planet": {"date": date, "url": source, "basemap_schema": planet.get("version")},
        "credit": "© OpenStreetMap contributors · Protomaps",
        "licence": "Map data ODbL (OpenStreetMap contributors); Protomaps Basemap produced work",
        "files": files,
    }
    index_path = WORK / "index.json"
    index_path.write_text(json.dumps(index, indent=1) + "\n")
    run("gh", "release", "upload", tag, str(index_path), "--repo", REPO, "--clobber")
    run("gh", "release", "edit", tag, "--repo", REPO, "--draft=false", "--latest")
    log(f"Released {tag} with {len(files)} files")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # Surface the reason as a GitHub annotation.
        detail = f"{type(error).__name__}: {error}"
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            detail += " | " + error.stderr.strip().replace("\n", " ")[-800:]
        print(f"::error title=Basemap build failed::{detail}", flush=True)
        raise
