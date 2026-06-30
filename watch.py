#!/usr/bin/env python3
"""Usage: uv run watch.py <job_id>  OR  uv run watch.py --upload <file>"""
import sys
import time
import json
import urllib.request
import urllib.parse

BASE = "http://localhost:6001"
INTERVAL = 10


def poll(job_id: str) -> None:
    print(f"Watching job {job_id} (checking every {INTERVAL}s) ...\n")
    while True:
        with urllib.request.urlopen(f"{BASE}/status/{job_id}") as r:
            data = json.loads(r.read())
        status = data.get("status", "unknown")
        stage = data.get("stage", "")
        model = data.get("model", "")
        planner = data.get("planner_model", "")

        def short(m: str) -> str:
            return m.split("/")[-1] if m else ""

        parts = [f"  {status}"]
        if stage:
            parts.append(f"[{stage}]")
        if stage == "planning" and planner:
            parts.append(f"planner={short(planner)}")
        elif model:
            parts.append(f"writer={short(model)}")
            if planner:
                parts.append(f"planner={short(planner)}")
        print("  ".join(parts))
        if status in ("success", "failed"):
            print()
            print(json.dumps(data, indent=2))
            if status == "success":
                filename = f"{job_id}.mp4"
                print(f"\nDownload: curl {BASE}/video/{filename} -o output.mp4")
            break
        time.sleep(INTERVAL)


def upload(path: str) -> str:
    import urllib.request
    import mimetypes
    boundary = "----FormBoundary"
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        file_data = f.read()
    filename = path.split("/")[-1]
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{BASE}/generate",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
    print(f"Queued: {json.dumps(data, indent=2)}\n")
    return data["job_id"]


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--upload":
        job_id = upload(sys.argv[2])
        poll(job_id)
    elif len(sys.argv) == 2:
        poll(sys.argv[1])
    else:
        print("Usage:")
        print("  uv run watch.py <job_id>              # watch an existing job")
        print("  uv run watch.py --upload <file>       # upload + watch in one command")
        sys.exit(1)
