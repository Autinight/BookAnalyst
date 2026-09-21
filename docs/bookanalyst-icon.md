# BookAnalyst icon

Generated with the built-in image_gen tool. The selected opaque PNG is stored in
`src/bookanalyst/static/bookanalyst-icon.png`; `bookanalyst.ico` is its Windows
format export with 16, 24, 32, 48, 64, 128 and 256 pixel sizes.
The sidebar applies rounded corners in CSS.

## Final generation prompt

Use case: logo-brand. Generate a NEW square opaque application icon for BookAnalyst. An elegant bold ivory open book, with the right-hand page subtly shaped like capital B and one small muted brass bookmark. Flat minimal geometric artwork, refined academic book publisher aesthetic, thick clean shapes readable at 16px. The icon occupies 75 percent of the canvas. The background MUST be a single perfectly solid forest green #285c4d covering the ENTIRE image edge to edge including all four corners. Absolutely NO transparency anywhere: every pixel must be opaque. Square image with square corners. No rounded tile, no outer margin, no transparent cutout, no shadows, no gradients, no texture, no cloudy areas, no shine, no distressed effects, no lettering or text, no extra objects. Only ivory book and small gold bookmark on a uniform fully opaque green square.

## Titlebar styling

The native Windows 11 caption uses the workbench background and text colors via
[DwmSetWindowAttribute](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute).
Earlier Windows versions keep the system titlebar colors.
