"""Smoke test for the GPU worker process: point, box, tiny dot, and Cellpose cells."""
import asyncio
import time

from orchestrator.gpu_client import GpuClient


async def main() -> None:
    gpu = GpuClient()
    t = time.perf_counter()
    await gpu.start()
    print(f"started on {gpu.device} in {time.perf_counter() - t:.1f}s")
    jpeg = open("bench/frames/leaf-132922-090s.jpg", "rb").read()
    cases = [
        ("point on oval (new frame)", 90, dict(points=[[756, 456]])),
        ("2nd point, same frame", 90, dict(points=[[544, 530]])),
        ("box, new frame", 91, dict(box=[720, 410, 790, 500])),
        ("tiny dark dot", 90, dict(points=[[785, 125]], prefer_small=True)),
    ]
    for label, frame_id, kwargs in cases:
        t = time.perf_counter()
        r = await gpu.call("segment", frame_id=frame_id, jpeg=jpeg, **kwargs)
        pts = len(r["polygons"][0]) if r["polygons"] else 0
        print(f"{label:28s} roundtrip {1000 * (time.perf_counter() - t):4.0f}ms  model {r['ms']:5.1f}ms  area {r['area']:5d}px  vertices {pts:3d}  score {r['score']:.2f}")
    t = time.perf_counter()
    r = await gpu.call("cells", timeout=180, frame_id=90, jpeg=jpeg, crop=[420, 150, 530, 450])
    print(f"cells: {r['count']} outlines, model {r['ms']}ms (roundtrip {1000 * (time.perf_counter() - t):.0f}ms incl. first load)")
    t = time.perf_counter()
    r = await gpu.call("cells", timeout=180, frame_id=90, jpeg=jpeg, crop=[420, 150, 530, 450])
    print(f"cells again: {r['count']} outlines, model {r['ms']}ms")
    gpu.stop()


if __name__ == "__main__":
    asyncio.run(main())
