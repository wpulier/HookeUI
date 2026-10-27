# Synthetic default-settings example

`example-default-grid.png` is a generated 384 × 384 RGB grid, released under Apache-2.0 with this capability. It contains no specimen or user data. Its size accommodates the default 256-pixel crop and the largest declared 384-pixel crop. The installation check exercises defaults; the existing leaf crop exercises size 128. Neither example establishes microscopy accuracy.

Generated with NumPy/OpenCV: `y, x = np.indices((384, 384))`; stack `(x % 128) * 2`, `(y % 128) * 2`, and `((x // 16 + y // 16) % 2) * 220` as uint8 channels, then `cv2.imwrite`.
