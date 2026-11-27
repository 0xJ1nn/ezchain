# Third-party notices — shipped assets

This register names every font, icon, image and mark that ezCHAIN ships, with its source, its licence and the notice that has to travel with it. It is the one place to check what is in the product: the font licence texts, the icon provenance, the screenshots and the project's own marks are all recorded here.

A file without a recorded source does not ship. If artwork arrives with no origin that can be named, it is either given one here or replaced with artwork whose source is in the repository. The project's own name and logo are covered separately in [`TRADEMARKS.md`](../TRADEMARKS.md).

This register covers shipped assets only. The licences of the packages the product depends on are inventoried by `tools/check-licences.py` and described in [`DEPENDENCIES.md` §7](DEPENDENCIES.md#7-licence-inventory); an asset is a file that travels with a build, while a dependency is code the build resolves from a lock.

## Fonts

The web app bundles four font files and the two licence texts that must accompany them. No font is fetched at runtime.

| Asset | Source | Licence | Attribution duty |
|---|---|---|---|
| `apps/web/app/fonts/inter-latin-var.woff2` | Inter Latin variable, upstream `github.com/rsms/inter`; copyright 2016 The Inter Project Authors | SIL Open Font License 1.1, text in `apps/web/app/fonts/OFL-Inter.txt` | Ship `OFL-Inter.txt` with the font. The bundled notice declares no reserved font name. |
| `apps/web/app/fonts/ibm-plex-mono-400.woff2` | IBM Plex Mono Regular, upstream `github.com/IBM/plex`; copyright 2017 IBM Corp. | SIL Open Font License 1.1, text in `apps/web/app/fonts/OFL-IBM-Plex.txt` | Ship `OFL-IBM-Plex.txt` with the font. |
| `apps/web/app/fonts/ibm-plex-mono-500.woff2` | IBM Plex Mono Medium, upstream `github.com/IBM/plex`; copyright 2017 IBM Corp. | SIL Open Font License 1.1, text in `apps/web/app/fonts/OFL-IBM-Plex.txt` | Ship `OFL-IBM-Plex.txt` with the font. |
| `apps/web/app/fonts/ibm-plex-mono-600.woff2` | IBM Plex Mono SemiBold, upstream `github.com/IBM/plex`; copyright 2017 IBM Corp. | SIL Open Font License 1.1, text in `apps/web/app/fonts/OFL-IBM-Plex.txt` | Ship `OFL-IBM-Plex.txt` with the font. "Plex" is a reserved font name, so a modified build must not use it. |
| `apps/web/app/fonts/OFL-Inter.txt` | Copy of the upstream SIL OFL 1.1 text for Inter | SIL Open Font License 1.1 | This file is the attribution; keep it beside the font. |
| `apps/web/app/fonts/OFL-IBM-Plex.txt` | Copy of the upstream SIL OFL 1.1 text for IBM Plex | SIL Open Font License 1.1 | This file is the attribution; keep it beside the fonts. |

## Icons and marks

The icon files used by the product's interface and by the design system are listed together. The paths under `design/brand/` are the private design-system sources; the product ships the same files, so their provenance belongs in this register. The third-party files are copied unchanged; their upstream licences and the notices those licences require are recorded below.

| Asset | Source | Licence | Attribution duty |
|---|---|---|---|
| `apps/web/app/icon.svg` | The ezCHAIN app mark, drawn by the project as the vector source for the web icon and the desktop icon | AGPL-3.0-or-later as part of the app; the mark is a project trademark | None beyond `NOTICE` and `TRADEMARKS.md`. |
| `apps/desktop/resources/icon.png` | Rendered from `apps/web/app/icon.svg`, the project's own app mark, at 512 × 512. The original file had no recorded origin, so it was replaced with a render whose source is in the repository; regeneration is described below. | AGPL-3.0-or-later as project artwork; the mark is a project trademark | None beyond `NOTICE` and `TRADEMARKS.md`. |
| `design/brand/icons/ion.svg` | Ionicons 7.4.0, outline and filled sets, paths unchanged; upstream `github.com/ionic-team/ionicons` | MIT, copyright 2015–present Ionic | Keep the MIT copyright and permission notice with copies of the file. |
| `design/brand/icons/chains/*.svg` — 12 files | web3icons core 4.0.59, branded set, files unchanged; upstream `github.com/0xa3k5/web3icons` | MIT, copyright 2024 0xa3k5 | Keep the MIT copyright and permission notice with copies. The chain marks are third-party trademarks. |
| `design/brand/icons/wallets/*.svg` — 8 files | web3icons core 4.0.59, branded set, files unchanged; upstream `github.com/0xa3k5/web3icons` | MIT, copyright 2024 0xa3k5 | Keep the MIT copyright and permission notice with copies. The wallet marks are third-party trademarks. |
| `design/brand/icons/tokens/*.svg` — 10 files | web3icons core 4.0.59, branded set, files unchanged; upstream `github.com/0xa3k5/web3icons` | MIT, copyright 2024 0xa3k5 | Keep the MIT copyright and permission notice with copies. The token marks are third-party trademarks. |
| `design/brand/icons.svg` | The project's own 1.75-stroke line-icon set, used with `<use href="icons.svg#id">` | Apache-2.0 as part of the design system, under `NOTICE` | None beyond `NOTICE`. |

### Third-party marks

The chain, token and wallet icons are **third-party trademarks**. They belong to the projects they identify, not to ezCHAIN. The MIT licence covers the SVG files; it grants no right in the marks themselves. The marks appear so that a person can tell which chain, token or wallet a row refers to, and their presence does not imply endorsement, affiliation or approval by the mark owner. Neither the app's AGPL licence nor a commercial licence grants permission to use those marks; anyone redistributing the product must make its own arrangements with each owner. The ezCHAIN name and logo are the project's own marks and follow [`TRADEMARKS.md`](../TRADEMARKS.md).

### Regenerating the desktop icon

The desktop icon is a render of the vector icon the project already ships, so its origin can be checked rather than asserted. From the repository root:

```bash
magick -background none -density 1152 apps/web/app/icon.svg -resize 512x512 -depth 8 -strip apps/desktop/resources/icon.png
```

The result is a 512 × 512 RGBA PNG with transparent corners, matching the icon that the desktop app loads for its window and tray.

## Screenshots

Every image under `docs/evidence/` is a capture of an ezCHAIN build, not third-party artwork. The evidence notes record how and when each was taken.

| Asset | Source | Licence | Attribution duty |
|---|---|---|---|
| `docs/evidence/desktop/linux-dashboard.png` | Screenshot of the native desktop app, recorded in `docs/evidence/desktop/linux-app.md` | Project output, AGPL-3.0-or-later | None. |
| `docs/evidence/m1-ui/dashboard-mobile-390.png` | Chrome capture of the web app at 390 × 844, recorded in `docs/evidence/m1-ui/README.md` | Project output, AGPL-3.0-or-later | None. |
| `docs/evidence/m1-ui/dashboard-wireframe-vs-implementation.png` | Chrome comparison of the project's wireframe and the web app at 1440 px, recorded in `docs/evidence/m1-ui/README.md` | Project output, AGPL-3.0-or-later | None. |
| `docs/evidence/m1-ui/markets-mobile-390.png` | Chrome capture of the web app at 390 × 844, recorded in `docs/evidence/m1-ui/README.md` | Project output, AGPL-3.0-or-later | None. |
| `docs/evidence/m1-ui/markets-wireframe-vs-implementation.png` | Chrome comparison of the project's wireframe and the web app at 1440 px, recorded in `docs/evidence/m1-ui/README.md` | Project output, AGPL-3.0-or-later | None. |
| `docs/evidence/m1-ui/positions-mobile-390.png` | Chrome capture of the web app at 390 × 844, recorded in `docs/evidence/m1-ui/README.md` | Project output, AGPL-3.0-or-later | None. |
| `docs/evidence/m1-ui/positions-wireframe-vs-implementation.png` | Chrome comparison of the project's wireframe and the web app at 1440 px, recorded in `docs/evidence/m1-ui/README.md` | Project output, AGPL-3.0-or-later | None. |
| `docs/evidence/m1-ui/token-mobile-390.png` | Chrome capture of the web app at 390 × 844, recorded in `docs/evidence/m1-ui/README.md` | Project output, AGPL-3.0-or-later | None. |
| `docs/evidence/m1-ui/token-scam-1440.png` | Chrome capture of the fail-closed token page at 1440 px, recorded in `docs/evidence/m1-ui/README.md` | Project output, AGPL-3.0-or-later | None. |
| `docs/evidence/m1-ui/token-wireframe-vs-implementation.png` | Chrome comparison of the project's wireframe and the web app at 1440 px, recorded in `docs/evidence/m1-ui/README.md` | Project output, AGPL-3.0-or-later | None. |

## MIT notices carried for the icon files

The MIT licence requires the copyright notice and the permission notice to be included in copies of the files. Both notices are reproduced here so that the register discharges that duty for the icon sets.

### Ionicons

```text
The MIT License (MIT)

Copyright (c) 2015-present Ionic (http://ionic.io/)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

### web3icons

```text
MIT License

Copyright (c) 2024 0xa3k5

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Keeping the register complete

A change that adds a font, icon, image or mark adds a row here in the same change. The row names the file, where it came from, the licence that covers it and the notice that has to travel with it. An asset whose origin cannot be named is replaced rather than shipped with a guessed provenance.
