#!/usr/bin/env python3
"""Build a V2RayN subscription from nodes that pass real proxy benchmarks."""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml


UPSTREAM_REPO = "Au1rxx/free-vpn-subscriptions"
UPSTREAM_FILES = {
    "clash": "output/clash.yaml",
    "v2ray": "output/v2ray-base64.txt",
}
SUPPORTED_SCHEMES = {"vmess", "vless", "trojan", "ss", "hy2", "hysteria2", "tuic"}
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
MAX_LATENCY_MS = 400
MIN_SPEED_BYTES_PER_SECOND = 2 * 1024 * 1024
SPEED_TEST_BYTES = 4 * 1024 * 1024
CONTROLLER_URL = "http://127.0.0.1:9090"
LOCAL_PROXY_URL = "http://127.0.0.1:7890"
SPEED_TEST_URL = "https://speed.cloudflare.com/__down"


def fetch_bytes(url: str, max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "raw.githubusercontent.com",
        "api.github.com",
    }:
        raise ValueError(f"Refusing untrusted URL: {url}")

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github.raw+json",
            "User-Agent": "v2ray-speed-sub/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError(f"Response exceeds {max_bytes} bytes: {url}")
    return payload


def fetch_upstream(kind: str) -> bytes:
    path = UPSTREAM_FILES[kind]
    urls = [
        f"https://raw.githubusercontent.com/{UPSTREAM_REPO}/main/{path}",
        f"https://api.github.com/repos/{UPSTREAM_REPO}/contents/{path}",
    ]
    errors: list[str] = []
    for url in urls:
        try:
            return fetch_bytes(url)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError("All upstream endpoints failed:\n" + "\n".join(errors))


def decode_subscription(payload: bytes) -> list[str]:
    text = payload.decode("utf-8-sig").strip()
    if "://" not in text:
        compact = "".join(text.split())
        compact += "=" * (-len(compact) % 4)
        text = base64.b64decode(compact, validate=True).decode("utf-8")

    links: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or len(line) > 8192 or "://" not in line:
            continue
        scheme = line.split("://", 1)[0].lower()
        if scheme not in SUPPORTED_SCHEMES or line in seen:
            continue
        seen.add(line)
        links.append(line)
    if not links:
        raise ValueError("Upstream subscription contains no supported links")
    return links


def atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(data, encoding="utf-8")
    os.replace(temporary, path)


def prepare(work_dir: Path) -> int:
    clash = yaml.safe_load(fetch_upstream("clash").decode("utf-8"))
    links = decode_subscription(fetch_upstream("v2ray"))
    proxies = clash.get("proxies") if isinstance(clash, dict) else None
    if not isinstance(proxies, list) or not proxies:
        raise ValueError("Upstream Clash file has no proxies")
    if len(proxies) != len(links):
        raise ValueError(f"Proxy/link count mismatch: {len(proxies)} != {len(links)}")

    names = [proxy.get("name") for proxy in proxies if isinstance(proxy, dict)]
    if len(names) != len(proxies) or any(not isinstance(name, str) for name in names):
        raise ValueError("Every proxy must have a string name")
    if len(set(names)) != len(names):
        raise ValueError("Proxy names must be unique")
    if not any(group.get("name") == "select" for group in clash.get("proxy-groups", [])):
        raise ValueError("Upstream Clash file has no 'select' group")

    clash.update(
        {
            "mixed-port": 7890,
            "external-controller": "0.0.0.0:9090",
            "secret": "",
            "allow-lan": True,
            "mode": "rule",
            "log-level": "warning",
            "rules": ["MATCH,select"],
        }
    )
    mapping = [{"name": name, "link": link} for name, link in zip(names, links)]
    atomic_write(work_dir / "config.yaml", yaml.safe_dump(clash, allow_unicode=True, sort_keys=False))
    atomic_write(work_dir / "nodes.json", json.dumps(mapping, ensure_ascii=False, indent=2))
    return len(mapping)


def controller_request(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        CONTROLLER_URL + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


def wait_for_controller(timeout_seconds: int = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            controller_request("GET", "/version")
            return
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            time.sleep(1)
    raise RuntimeError("Mihomo controller did not become ready")


def measure_latency(name: str) -> int:
    encoded_name = urllib.parse.quote(name, safe="")
    query = urllib.parse.urlencode(
        {"timeout": 5000, "url": "https://www.gstatic.com/generate_204"}
    )
    result = controller_request("GET", f"/proxies/{encoded_name}/delay?{query}")
    return int(result["delay"])


def select_proxy(name: str) -> None:
    controller_request("PUT", "/proxies/select", {"name": name})


def measure_speed(test_bytes: int = SPEED_TEST_BYTES) -> tuple[float, int]:
    proxy_handler = urllib.request.ProxyHandler(
        {"http": LOCAL_PROXY_URL, "https": LOCAL_PROXY_URL}
    )
    opener = urllib.request.build_opener(proxy_handler)
    nonce = str(time.time_ns())
    url = SPEED_TEST_URL + "?" + urllib.parse.urlencode({"bytes": test_bytes, "nonce": nonce})
    request = urllib.request.Request(url, headers={"User-Agent": "v2ray-speed-sub/1.0"})
    started = time.monotonic()
    received = 0
    with opener.open(request, timeout=12) as response:
        while received < test_bytes:
            chunk = response.read(min(64 * 1024, test_bytes - received))
            if not chunk:
                break
            received += len(chunk)
    elapsed = max(time.monotonic() - started, 0.001)
    return received / elapsed, received


def qualifies(latency_ms: int, speed_bytes_per_second: float) -> bool:
    return latency_ms <= MAX_LATENCY_MS and speed_bytes_per_second >= MIN_SPEED_BYTES_PER_SECOND


def publish_results(mapping: list[dict[str, str]], results: list[dict[str, Any]], docs_dir: Path) -> int:
    links_by_name = {item["name"]: item["link"] for item in mapping}
    passed_links = [links_by_name[item["name"]] for item in results if item.get("passed")]
    if not passed_links:
        raise RuntimeError("No nodes passed; keeping the previous published subscription")

    content = "\n".join(passed_links) + "\n"
    subscription = base64.b64encode(content.encode("utf-8")).decode("ascii")
    status = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "max_latency_ms": MAX_LATENCY_MS,
        "min_speed_mib_per_second": 2,
        "tested": len(results),
        "passed": len(passed_links),
        "nodes": [item for item in results if item.get("passed")],
    }
    atomic_write(docs_dir / "sub.txt", subscription + "\n")
    atomic_write(docs_dir / "status.json", json.dumps(status, ensure_ascii=False, indent=2) + "\n")
    return len(passed_links)


def benchmark(work_dir: Path, docs_dir: Path) -> int:
    mapping = json.loads((work_dir / "nodes.json").read_text(encoding="utf-8"))
    wait_for_controller()
    results: list[dict[str, Any]] = []
    for index, item in enumerate(mapping, start=1):
        name = item["name"]
        result: dict[str, Any] = {"name": name, "passed": False}
        try:
            latency_ms = measure_latency(name)
            result["latency_ms"] = latency_ms
            if latency_ms > MAX_LATENCY_MS:
                result["reason"] = "latency"
            else:
                select_proxy(name)
                speed, received = measure_speed()
                result["speed_mib_per_second"] = round(speed / (1024 * 1024), 2)
                result["downloaded_bytes"] = received
                result["passed"] = qualifies(latency_ms, speed) and received >= SPEED_TEST_BYTES
                if not result["passed"]:
                    result["reason"] = "speed"
        except Exception as exc:  # A bad public node must not abort the complete run.
            result["reason"] = type(exc).__name__
        results.append(result)
        print(f"[{index}/{len(mapping)}] {name}: {json.dumps(result, ensure_ascii=False)}", flush=True)
    return publish_results(mapping, results, docs_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "benchmark"))
    parser.add_argument("--work-dir", type=Path, default=Path("work"))
    parser.add_argument("--docs-dir", type=Path, default=Path("docs"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "prepare":
        count = prepare(args.work_dir)
        print(f"Prepared {count} candidates")
    else:
        count = benchmark(args.work_dir, args.docs_dir)
        print(f"Published {count} qualified nodes")


if __name__ == "__main__":
    main()
