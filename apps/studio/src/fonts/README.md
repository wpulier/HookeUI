# Fonts, kept in the repository

The app loads its fonts from here, never from a font service, so opening it sends nothing to anyone.

Each font is a variable font from the Google Fonts repository (https://github.com/google/fonts), at commit
`66928797f271fed5ee428544e7229685e2d90127`, converted from its TTF to WOFF2 with fontTools 4.60.1 (`fonttools ttLib.woff2 compress`). Each is under the
SIL Open Font License 1.1: its licence is the `*-OFL.txt` file beside it.

| File | Source in google/fonts | Licence |
|---|---|---|
| `Inter-Variable.woff2` (sha256 `13b4cddc57045d40…`) | `ofl/inter/Inter[opsz,wght].ttf` | `Inter-OFL.txt` |
| `Newsreader-Italic-Variable.woff2` (sha256 `8920f06c4c116e91…`) | `ofl/newsreader/Newsreader-Italic[opsz,wght].ttf` | `Newsreader-OFL.txt` |
