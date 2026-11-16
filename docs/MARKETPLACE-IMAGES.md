# MARKETPLACE IMAGES — written spec and render instructions

This is the specification for the artwork and listing text used when ezCHAIN appears in a
server provider's marketplace. It is a written plan only. No image has been generated or drawn
for it: the visual source of truth is the owner's design export, and artwork must be rendered
from that file in a real browser engine, not recreated from a description. If the design export
is missing, rendering is blocked and the owner must supply it; a stand-in is not acceptable.

## 1. What the image offers

The owner's hosting decision of 2026-11-11 is that live trading runs on the user's own server
with the user's own keys. ezCHAIN never holds, stores or signs with those keys and never gets
access to the user's server. A marketplace image therefore advertises an installer, not a
custodial service: it runs the official installer at first boot from a pinned release whose
checksum is verified, and it ships no prebuilt secret. The full reasoning and the free-offer
boundary are in [FUNDING.md](FUNDING.md); the operating guide is
[SELF-HOSTING.md](SELF-HOSTING.md).

The machine image build itself — choosing a base image, running the installer at first boot,
pinning a tagged release and checking its checksum — is infrastructure code and is not part of
this document. This spec covers the listing artwork and the words beside it.

## 2. Provider list and phasing

| Phase | Marketplace | Scope |
|---|---|---|
| 1 | Hetzner Cloud, DigitalOcean | one image set each, listed with the text in §5 |
| 2 | Vultr, Linode (Akamai), OVHcloud | same set, re-rendered only if the provider requires a different size |
| 3 | AWS EC2, Google Cloud Marketplace, Azure Marketplace | same set plus any provider-specific listing fields |

The provider order is a rollout plan, not a claim that any listing exists. Hetzner and
DigitalOcean are first because they are common self-hosting targets for a single-node stack;
no performance or liquidity ranking was measured. Each marketplace's current upload rules must
be checked before export. The sizes below are this project's chosen spec, not a statement of
what any provider currently accepts.

## 3. Image set

One set is shared by all providers. The source of each image is a file exported by the owner
from the design tool into `design/marketplace/`; the rendered outputs live in exactly that
tree, in `design/marketplace/export/`, so there is one source for every artifact.

| Id | Size | Format | What it shows | Design source |
|---|---|---|---|---|
| `hero` | 1280 × 720 | PNG | The workstation dashboard at desktop width with the PAPER badge visible, and the title "Self-hosted paper trading workstation". No PnL figure, no return, no live-mode badge. | `design/marketplace/listing-hero.dc.html` |
| `installer` | 1280 × 720 | PNG | The first-boot installer at the checksum-verification step, with the pinned release tag visible. The terminal text is copied from a real, recorded install run; nothing in the frame is invented. | `design/marketplace/installer-first-boot.dc.html` |
| `badge` | 600 × 400 | PNG | Three plain labels: "PAPER mode", "Your keys, your server", "AGPL-3.0". No logos beyond the exported brand mark. | `design/marketplace/listing-badge.dc.html` |
| `mark` | 512 × 512 | PNG, transparent | The brand mark, exported from the existing design source. No new variant, palette or size is invented. | exported by the owner from the design file; see [BRIEF.md](../design/BRIEF.md) |

Every image carries the mode label from the design; a screenshot that could be read as live
trading is rejected. The dashboard and installer frames are renders of design files, not
captures hand-assembled from prose.

## 4. Exact render instructions

Prerequisites: a clean checkout, `pnpm install` at the repository root, Playwright's Chromium
installed for `@ezchain/web`, and the four design files exported by the owner into
`design/marketplace/`. Run from the repository root. Each command renders the design's own
file in a real browser engine at the exact target size; no pixel loop, canvas painting or
local drawing code is involved.

```bash
# hero — 1280 x 720
pnpm --filter @ezchain/web exec playwright screenshot \
  --browser chromium --viewport-size="1280,720" --wait-for-timeout=500 \
  "file://$(pwd)/design/marketplace/listing-hero.dc.html" \
  design/marketplace/export/hero-1280x720.png

# installer — 1280 x 720
pnpm --filter @ezchain/web exec playwright screenshot \
  --browser chromium --viewport-size="1280,720" --wait-for-timeout=500 \
  "file://$(pwd)/design/marketplace/installer-first-boot.dc.html" \
  design/marketplace/export/installer-1280x720.png

# badge — 600 x 400
pnpm --filter @ezchain/web exec playwright screenshot \
  --browser chromium --viewport-size="600,400" --wait-for-timeout=500 \
  "file://$(pwd)/design/marketplace/listing-badge.dc.html" \
  design/marketplace/export/badge-600x400.png
```

The mark is an export from the design file, not a screenshot of a page. After rendering, check
each file's pixel dimensions and confirm the four files are the only new artifacts under
`design/marketplace/export/`. If a provider needs a different size, re-render from the same
design file at that viewport; plain resize or format conversion is allowed, but changing the
composition in code is not. Do not commit a generated image that did not come from this path.

## 5. Listing text

Use the same words on every provider; only the marketplace's own fields change.

> **ezCHAIN — self-hosted paper trading workstation**
>
> Runs the official installer at first boot from a pinned, checksum-verified release. The image
> ships no keys, funds or prebuilt secret. Live trading stays on your server with your keys;
> ezCHAIN never holds them and never gets access to your machine. Paper mode, the risk rails,
> the kill switch and the ledger are free and are never gated.
>
> Read the [self-hosting guide](https://github.com/0xJ1nn/ezchain/blob/main/docs/SELF-HOSTING.md)
> for the trust model, the firewall, backups and what has been tested.

No claim about returns, profit, safety, speed, throughput or "best" appears in a title,
screenshot or description. A badge that says "verified" would need a recorded check behind it;
none is claimed here. The document records the intent to publish; it is not proof that a
marketplace listing exists.

## 6. What the docs field did not do

No image was generated, edited or drawn, no machine image was built, and no marketplace
listing was opened. Those are respectively a design export owned by the maintainer, an
infrastructure change and a public action. This file is the spec the other fields will read.
