#!/usr/bin/env python3
"""Check the shipped receiver, including an encrypted loopback SRT frame.

Synthetic packaging evidence, not physical camera qualification. No camera is opened.
"""
import argparse
import socket
import subprocess
import time


def require_protocols(text):
    sections = {'Input': set(), 'Output': set()}
    current = None
    for line in text.splitlines():
        if line.rstrip(':') in sections:
            current = line.rstrip(':')
        elif current and line.strip():
            sections[current].add(line.strip())
    for direction in sections:
        if 'srt' not in sections[direction]:
            raise ValueError(f'Packaged FFmpeg is missing {direction.lower()} SRT support; Blackmagic cannot work')


def verify(binary, smoke=False):
    protocols = subprocess.run([binary, '-hide_banner', '-protocols'], capture_output=True, text=True, check=True, timeout=10)
    require_protocols(protocols.stdout)
    if not smoke:
        return
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    # A public, synthetic test key. Never reads saved camera or provider configuration.
    query = 'passphrase=hooke-loopback-test&pbkeylen=16&latency=20000'
    receiver = subprocess.Popen([binary, '-nostdin', '-v', 'error', '-i',
        f'srt://127.0.0.1:{port}?mode=listener&{query}', '-frames:v', '1',
        '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sender = None
    try:
        time.sleep(0.2)
        sender = subprocess.Popen([binary, '-nostdin', '-v', 'error', '-re', '-f', 'lavfi',
            '-i', 'color=c=red:s=32x32:r=15', '-t', '6', '-c:v', 'mpeg2video', '-f', 'mpegts',
            f'srt://127.0.0.1:{port}?mode=caller&{query}'], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        data, error = receiver.communicate(timeout=20)
        if receiver.returncode or len(data) != 32 * 32 * 3:
            raise ValueError(f'Packaged encrypted SRT reception failed ({len(data)} bytes): {error.decode(errors="replace")[:200]}')
        if not all(data[i] >= 200 and data[i+1] < 50 and data[i+2] < 50 for i in range(0, len(data), 3)):
            raise ValueError('Packaged SRT frame does not match the synthetic red fixture')
    finally:
        for process in (sender, receiver):
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary')
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    verify(args.binary, args.smoke)
    print('Packaged SRT receiver verified' + (' with encrypted synthetic frame' if args.smoke else ''))
