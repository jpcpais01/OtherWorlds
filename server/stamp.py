"""Frame-ID "barcode" used to measure true end-to-end latency over WebRTC.

The phone paints BITS black/white squares along the bottom-left edge of each
frame it sends. The server reads the ID from the camera frame and paints the
same code onto the restyled frame, so the phone can match what it shows to
when it was captured. Must stay identical to stamp code in client/app.js.
"""
import numpy as np

BITS = 12


def block_size(width):
    return max(4, width // 32)


def read(img):
    h, w = img.shape[:2]
    b = block_size(w)
    y = h - b // 2
    xs = np.arange(BITS) * b + b // 2
    lum = img[y - 1:y + 1, :, :].mean(axis=(0, 2))[xs]
    return int(sum(1 << i for i, v in enumerate(lum) if v > 128))


def write(img, frame_id):
    h, w = img.shape[:2]
    b = block_size(w)
    for i in range(BITS):
        img[h - b:h, i * b:(i + 1) * b] = 255 if (frame_id >> i) & 1 else 0
    return img
