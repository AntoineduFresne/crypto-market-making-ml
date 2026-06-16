#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import math
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


URL = "https://ml-crypto-task-data.s3.eu-central-2.amazonaws.com/clean_market_data_20260201_20260430.tar.zst"
SHA256 = "1e1772bfc66aca4a3c08a9330247ccad3af8c40c50f5b7f53204ef1efb0a5eae"
ARCHIVE_NAME = "clean_market_data_20260201_20260430.tar.zst"


@dataclass(frozen=True)
class RemoteFile:
    size: int | None
    accepts_ranges: bool


@dataclass(frozen=True)
class Chunk:
    index: int
    start: int
    end: int

    @property
    def size(self) -> int:
        return self.end - self.start + 1


class Progress:
    def __init__(self, total: int, initial: int = 0) -> None:
        self.total = total
        self.initial = initial
        self.done = initial
        self.start_time = time.monotonic()
        self.last_print = 0.0
        self.last_line_len = 0
        self.lock = threading.Lock()

    def add(self, amount: int) -> None:
        with self.lock:
            self.done += amount
            now = time.monotonic()
            if now - self.last_print >= 1.0 or self.done >= self.total:
                self.last_print = now
                self.print_locked(now)

    def print_locked(self, now: float) -> None:
        elapsed = max(now - self.start_time, 1e-6)
        run_done = max(self.done - self.initial, 0)
        speed = run_done / elapsed
        remaining = max(self.total - self.done, 0)
        eta = remaining / speed if speed > 0 else math.inf
        pct = self.done / self.total * 100.0 if self.total else 100.0
        line = (
            f"downloaded {self.done / 1_000_000_000:.2f}/"
            f"{self.total / 1_000_000_000:.2f} GB "
            f"({pct:5.1f}%) {speed / 1_000_000:.1f} MB/s "
            f"eta {format_seconds(eta)}"
        )
        padding = " " * max(self.last_line_len - len(line), 0)
        print(f"\r{line}{padding}", end="", flush=True)
        self.last_line_len = len(line)

    def finish(self) -> None:
        with self.lock:
            self.print_locked(time.monotonic())
        print(flush=True)


class ActiveResponses:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._responses: set[object] = set()

    def add(self, response: object) -> None:
        with self._lock:
            self._responses.add(response)

    def discard(self, response: object) -> None:
        with self._lock:
            self._responses.discard(response)

    def close_all(self) -> None:
        with self._lock:
            responses = list(self._responses)
        for response in responses:
            try:
                response.close()
            except Exception:
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download, verify, and unpack the public market data archive."
    )
    parser.add_argument(
        "data_dir",
        nargs="?",
        default="data",
        type=Path,
        help="Directory where the archive and market_data folder are stored.",
    )
    parser.add_argument("--url", default=URL)
    parser.add_argument("--sha256", default=SHA256)
    parser.add_argument("--archive-name", default=ARCHIVE_NAME)
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Parallel HTTP range requests. Default is 8.",
    )
    parser.add_argument(
        "--chunk-size-mb",
        type=int,
        default=64,
        help="Download chunk size in MiB. Default is 64.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=5,
        help="Retries per chunk. Default is 5.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP timeout in seconds. Default is 60.",
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Download and verify the archive, but do not unpack it.",
    )
    parser.add_argument(
        "--keep-archive",
        action="store_true",
        help="Keep the compressed archive after successful unpacking.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Redownload even when the existing archive hash is valid.",
    )
    return parser.parse_args()


def format_seconds(value: float) -> str:
    if not math.isfinite(value):
        return "--:--"
    value = int(max(value, 0))
    hours, rem = divmod(value, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def head_remote(url: str, timeout: float) -> RemoteFile:
    request = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        size_header = response.headers.get("Content-Length")
        size = int(size_header) if size_header else None
        accepts_ranges = response.headers.get("Accept-Ranges", "").lower() == "bytes"
    return RemoteFile(size=size, accepts_ranges=accepts_ranges)


def split_chunks(size: int, chunk_size: int) -> list[Chunk]:
    chunks = []
    for index, start in enumerate(range(0, size, chunk_size)):
        end = min(start + chunk_size - 1, size - 1)
        chunks.append(Chunk(index=index, start=start, end=end))
    return chunks


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def archive_is_valid(path: Path, expected_sha256: str) -> bool:
    if not path.exists():
        return False
    print(f"checking existing archive hash: {path}", flush=True)
    actual = sha256_file(path)
    if actual == expected_sha256:
        print("archive already downloaded and hash is valid", flush=True)
        return True
    print(f"existing archive hash mismatch: {actual}", flush=True)
    return False


def parts_dir_for_archive(path: Path) -> Path:
    return path.parent / ".download_parts" / path.name


def unpack_marker_path(data_dir: Path, expected_sha256: str) -> Path:
    return data_dir / f".market_data_unpacked_{expected_sha256[:16]}"


def market_data_is_unpacked(data_dir: Path, expected_sha256: str) -> bool:
    return (data_dir / "market_data").is_dir() and unpack_marker_path(
        data_dir, expected_sha256
    ).exists()


def mark_market_data_unpacked(data_dir: Path, expected_sha256: str) -> None:
    marker = unpack_marker_path(data_dir, expected_sha256)
    marker.write_text(f"sha256={expected_sha256}\n")


def download_single_stream(
    *,
    url: str,
    dst: Path,
    expected_size: int | None,
    timeout: float,
) -> None:
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    request = urllib.request.Request(url)
    downloaded = 0
    progress = Progress(expected_size or 0)
    with urllib.request.urlopen(request, timeout=timeout) as response, tmp.open("wb") as out:
        while True:
            block = response.read(8 * 1024 * 1024)
            if not block:
                break
            out.write(block)
            downloaded += len(block)
            if expected_size:
                progress.add(len(block))
            else:
                print(f"\rdownloaded {downloaded / 1_000_000_000:.2f} GB", end="", flush=True)
    print(flush=True)
    tmp.replace(dst)


def download_chunk(
    *,
    url: str,
    chunk: Chunk,
    part_path: Path,
    timeout: float,
    retries: int,
    progress: Progress,
    stop_event: threading.Event,
    active_responses: ActiveResponses,
) -> None:
    if part_path.exists() and part_path.stat().st_size == chunk.size:
        return

    tmp_path = part_path.with_suffix(".tmp")
    for attempt in range(1, retries + 1):
        if stop_event.is_set():
            raise InterruptedError("download interrupted")
        bytes_written = 0
        try:
            if tmp_path.exists():
                tmp_path.unlink()
            request = urllib.request.Request(
                url, headers={"Range": f"bytes={chunk.start}-{chunk.end}"}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                active_responses.add(response)
                try:
                    if response.status != 206:
                        raise RuntimeError(
                            f"expected HTTP 206 for chunk {chunk.index}, got {response.status}"
                        )
                    with tmp_path.open("wb") as out:
                        while True:
                            if stop_event.is_set():
                                raise InterruptedError("download interrupted")
                            block = response.read(1024 * 1024)
                            if not block:
                                break
                            out.write(block)
                            bytes_written += len(block)
                            progress.add(len(block))
                finally:
                    active_responses.discard(response)
            if bytes_written != chunk.size:
                raise RuntimeError(
                    f"chunk {chunk.index} expected {chunk.size} bytes, got {bytes_written}"
                )
            tmp_path.replace(part_path)
            return
        except Exception:
            if bytes_written:
                progress.add(-bytes_written)
            if stop_event.is_set():
                raise
            if attempt == retries:
                raise
            if stop_event.wait(min(2 ** attempt, 30)):
                raise InterruptedError("download interrupted")


def download_parallel(
    *,
    url: str,
    dst: Path,
    size: int,
    workers: int,
    chunk_size: int,
    timeout: float,
    retries: int,
) -> None:
    chunks = split_chunks(size, chunk_size)
    parts_dir = parts_dir_for_archive(dst)
    parts_dir.mkdir(parents=True, exist_ok=True)
    for tmp_part in parts_dir.glob("*.tmp"):
        tmp_part.unlink()

    existing_bytes = 0
    pending: list[Chunk] = []
    for chunk in chunks:
        part_path = parts_dir / f"{chunk.index:06d}.part"
        if part_path.exists() and part_path.stat().st_size == chunk.size:
            existing_bytes += chunk.size
        else:
            pending.append(chunk)

    if existing_bytes:
        completed_chunks = len(chunks) - len(pending)
        print(
            f"resuming from {existing_bytes / 1_000_000_000:.2f} GB "
            f"({completed_chunks}/{len(chunks)} chunks complete)",
            flush=True,
        )
    print(
        f"downloading {size / 1_000_000_000:.2f} GB using "
        f"{workers} workers, {chunk_size // (1024 * 1024)} MiB chunks",
        flush=True,
    )
    progress = Progress(size, initial=existing_bytes)
    progress.add(0)
    stop_event = threading.Event()
    active_responses = ActiveResponses()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    futures = []
    try:
        futures = [
            executor.submit(
                download_chunk,
                url=url,
                chunk=chunk,
                part_path=parts_dir / f"{chunk.index:06d}.part",
                timeout=timeout,
                retries=retries,
                progress=progress,
                stop_event=stop_event,
                active_responses=active_responses,
            )
            for chunk in pending
        ]
        for future in concurrent.futures.as_completed(futures):
            future.result()
    except KeyboardInterrupt:
        stop_event.set()
        active_responses.close_all()
        for future in futures:
            future.cancel()
        print("\nstopping active downloads...", file=sys.stderr, flush=True)
        print(
            f"completed chunks are saved in {parts_dir}; rerun the same command to resume",
            file=sys.stderr,
            flush=True,
        )
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    progress.finish()

    tmp = dst.with_suffix(dst.suffix + ".assembling")
    print(f"assembling archive: {dst}", flush=True)
    with tmp.open("wb") as out:
        for chunk in chunks:
            part_path = parts_dir / f"{chunk.index:06d}.part"
            if part_path.stat().st_size != chunk.size:
                raise RuntimeError(f"bad chunk size after download: {part_path}")
            with part_path.open("rb") as part:
                shutil.copyfileobj(part, out, length=8 * 1024 * 1024)
    tmp.replace(dst)
    shutil.rmtree(parts_dir)


def download_archive(
    *,
    url: str,
    dst: Path,
    expected_sha256: str,
    workers: int,
    chunk_size: int,
    timeout: float,
    retries: int,
    force: bool,
) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not force and archive_is_valid(dst, expected_sha256):
        parts_dir = parts_dir_for_archive(dst)
        if parts_dir.exists():
            shutil.rmtree(parts_dir)
        return
    if dst.exists():
        print(f"removing invalid or incomplete archive: {dst}", flush=True)
        dst.unlink()

    remote = head_remote(url, timeout)
    if remote.size and remote.accepts_ranges and workers > 1:
        download_parallel(
            url=url,
            dst=dst,
            size=remote.size,
            workers=workers,
            chunk_size=chunk_size,
            timeout=timeout,
            retries=retries,
        )
    else:
        print("server does not support ranged downloads; using one stream", flush=True)
        download_single_stream(
            url=url,
            dst=dst,
            expected_size=remote.size,
            timeout=timeout,
        )

    print("checking downloaded archive hash", flush=True)
    actual_sha256 = sha256_file(dst)
    if actual_sha256 != expected_sha256:
        raise RuntimeError(
            f"SHA-256 mismatch for {dst}: expected {expected_sha256}, got {actual_sha256}"
        )
    print("archive hash is valid", flush=True)


def require_cmd(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"missing required command for unpacking: {name}")


def unpack_archive(archive_path: Path, data_dir: Path) -> None:
    require_cmd("zstd")
    require_cmd("tar")
    print(f"unpacking to {data_dir / 'market_data'}", flush=True)
    zstd_proc = subprocess.Popen(
        ["zstd", "--long=31", "-dc", str(archive_path)],
        stdout=subprocess.PIPE,
    )
    assert zstd_proc.stdout is not None
    try:
        tar_proc = subprocess.run(
            ["tar", "-xf", "-", "-C", str(data_dir)],
            stdin=zstd_proc.stdout,
        )
    finally:
        zstd_proc.stdout.close()
    zstd_returncode = zstd_proc.wait()
    if zstd_returncode != 0:
        raise subprocess.CalledProcessError(zstd_returncode, "zstd")
    if tar_proc.returncode != 0:
        raise subprocess.CalledProcessError(tar_proc.returncode, "tar")


def main() -> int:
    args = parse_args()
    if args.workers <= 0:
        raise ValueError("--workers must be positive")
    if args.chunk_size_mb <= 0:
        raise ValueError("--chunk-size-mb must be positive")
    if args.retries <= 0:
        raise ValueError("--retries must be positive")

    archive_path = args.data_dir / args.archive_name
    if (
        not args.force
        and not args.download_only
        and market_data_is_unpacked(args.data_dir, args.sha256)
    ):
        print(f"market data already unpacked: {args.data_dir / 'market_data'}", flush=True)
        print("done", flush=True)
        return 0

    download_archive(
        url=args.url,
        dst=archive_path,
        expected_sha256=args.sha256,
        workers=args.workers,
        chunk_size=args.chunk_size_mb * 1024 * 1024,
        timeout=args.timeout,
        retries=args.retries,
        force=args.force,
    )
    if not args.download_only:
        unpack_archive(archive_path, args.data_dir)
        mark_market_data_unpacked(args.data_dir, args.sha256)
        if not args.keep_archive and archive_path.exists():
            print(f"removing compressed archive: {archive_path}", flush=True)
            archive_path.unlink()
    print("done", flush=True)
    print(f"market data path: {args.data_dir / 'market_data'}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        raise SystemExit(130)
    except (OSError, urllib.error.URLError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
