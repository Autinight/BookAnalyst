# BookAnalyst icon

Generated with the built-in image_gen tool. The selected opaque PNG is stored in
`src/bookanalyst/static/bookanalyst-icon.png`; `bookanalyst.ico` is its Windows
format export with 16, 24, 32, 48, 64, 128 and 256 pixel sizes.
The sidebar applies rounded corners in CSS.

## macOS icon

The macOS desktop uses `src/bookanalyst/static/bookanalyst-macos.icns`.
Its source is `bookanalyst-macos.png`, edited with the built-in image_gen tool
to add a continuous rounded square silhouette and transparent outer margins.
The existing forest green, ivory book and brass bookmark design is retained.
The Windows icon and sidebar artwork continue to use the original assets.
The first macOS edit introduced a partially transparent patch at the top of the
green tile. The replacement removes that patch; the top background's color and
opacity have been checked in the PNG and in every exported ICNS size.

The `.icns` export contains sizes from 32 to 1024 pixels. For a macOS `.app`
launcher, copy it into `Contents/Resources/bookanalyst-macos.icns` and set
`CFBundleIconFile` to `bookanalyst-macos.icns` in `Contents/Info.plist`.
The desktop entry point also selects this asset for the running Dock icon.
Reopen the application after updating its icon.
The installed local launcher uses the corrected resource name
`bookanalyst-macos-v2.icns` to avoid reusing the first edit's cached Finder icon.

### macOS edit prompt

Use case: precise-object-edit. Input image is the EDIT TARGET: the current BookAnalyst macOS rounded-square icon. Fix the visibly blotchy dark green patch at the TOP CENTER of the green tile and all uneven green shading. Replace the ENTIRE green tile background with ONE SINGLE COMPLETELY FLAT SOLID FOREST GREEN #285c4d, every green background pixel identical RGB and fully opaque alpha 255. This is a flat vector-style brand icon, absolutely no gradients, no texture, no noise, no shading, no lighting, no smudges, no dark patches, no partial transparency INSIDE the green tile. Keep the current exact rounded-square silhouette, even transparent outer margins, centered ivory open book with B-shaped right page, and brass bookmark. Preserve all book and bookmark contours and placement. Use uniformly flat ivory and flat brass fills for these shapes too. Outside the rounded square must have actual alpha 0 transparency. Crisp antialiased silhouette and crisp book contours. Square application icon, straight-on, no perspective, no additional shapes, no shadows, no shine, no text. The central correction is an opaque, perfectly uniform green background with absolutely no upper color blocks.

## Final generation prompt

Use case: logo-brand. Generate a NEW square opaque application icon for BookAnalyst. An elegant bold ivory open book, with the right-hand page subtly shaped like capital B and one small muted brass bookmark. Flat minimal geometric artwork, refined academic book publisher aesthetic, thick clean shapes readable at 16px. The icon occupies 75 percent of the canvas. The background MUST be a single perfectly solid forest green #285c4d covering the ENTIRE image edge to edge including all four corners. Absolutely NO transparency anywhere: every pixel must be opaque. Square image with square corners. No rounded tile, no outer margin, no transparent cutout, no shadows, no gradients, no texture, no cloudy areas, no shine, no distressed effects, no lettering or text, no extra objects. Only ivory book and small gold bookmark on a uniform fully opaque green square.

## Titlebar styling

The native Windows 11 caption uses the workbench background and text colors via
[DwmSetWindowAttribute](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute).
Earlier Windows versions keep the system titlebar colors.
