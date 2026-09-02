import subprocess
import sys
import time


mode = sys.argv[1]

if mode == "normal":
    print(
        'RECLIP_PROGRESS {"status":"downloading","downloaded_bytes":25,"total_bytes":100}',
        flush=True,
    )
    time.sleep(0.05)
    print(
        'RECLIP_PROGRESS {"status":"finished","downloaded_bytes":100,"total_bytes":100}',
        flush=True,
    )
elif mode == "silent":
    time.sleep(30)
elif mode == "fail":
    print("ERROR: controlled test failure", file=sys.stderr, flush=True)
    sys.exit(2)
elif mode == "flood":
    for index in range(10_000):
        print(f"diagnostic line {index}", flush=True)
elif mode == "spawn-child":
    marker = sys.argv[2]
    subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import pathlib, sys, time; time.sleep(30); pathlib.Path(sys.argv[1]).write_text('alive')",
            marker,
        ]
    )
    time.sleep(30)
else:
    raise SystemExit(f"unknown test mode: {mode}")
