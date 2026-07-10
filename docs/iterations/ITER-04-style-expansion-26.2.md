# ITER-04 public style expansion for Minecraft Java 26.2

Snapshot: 2026-07-10. This audit expands the prompt-conditioned V2V style axis without
weakening the runtime or rights gates. It is a configuration and source-integrity result, not a
runtime-visual acceptance result.

## Executable look-dev subset

Three public, materially different, full-world candidates have an exact `26.2` Modrinth version,
a primary ZIP with SHA-512, and exactly one root `pack.mcmeta`. Their source bytes were downloaded
to an ephemeral local audit directory, matched Modrinth's SHA-512, passed the archive safety gate,
and materialized for Java resource format 88. Vanilla 8x8 and Fantasy are byte-preserving;
MS Painted omits the required scalar `pack_format`, so the existing deterministic normalizer
rewrites only its metadata to exact 88 and records a distinct effective SHA-256. No source ZIP was
modified or committed, and no remote machine was used.

| Profile | Visual role | Official source | Audited version / file | License / access |
|---|---|---|---|---|
| `lookdev_style_retro_vanilla_8x8_1080p` | complete 8x8 retro control | [Vanilla 8x8](https://modrinth.com/resourcepack/8x8-textures) | `26.2`, `Chaos Cubed.zip`, SHA-512 `3f5bc6ba…aa761f2` | CC-BY-NC-SA-4.0 / free |
| `lookdev_style_fantasy_legacy_32x_1080p` | complete 32x medieval/fantasy Legacy Console treatment | [Fantasy Texture Pack](https://modrinth.com/resourcepack/fantasy-texture-pack) | `1.0.14`, `Fantasy Texture Pack.zip`, SHA-512 `4bf0b82f…20e1c9` | ARR / free |
| `lookdev_style_ms_painted_128x_1080p` | extreme twenty-colour mouse-painted cartoon | [MS Painted](https://modrinth.com/resourcepack/ms-painted) | `v2.47`, `MS Painted for 26.1-26.2 (v2.47).zip`, SHA-512 `bb088ade…5709481` | ARR / free |

The exact API fields, full hashes, sizes, declared versions, archive inspection, and rejection
records are in
`docs/evidence/resourcepack_style_expansion_26_2.json`. Each asset uses an exact filename pattern;
the installer still requires Modrinth to return a version that explicitly includes `26.2`, requires
SHA-512, records the selected version/hash/size, and verifies those receipts while materializing.

Run only this candidate subset with the existing batch driver:

```bash
BATCH_PROFILES_FILE=configs/profile_subsets/lookdev_style_expansion_26_2.txt \
  scripts/lookdev_render_batch.sh --print-profiles
```

All three profiles inherit the same 1920×1080 profile, compatibility-mod superset, fixed world,
camera pose, and `lookdev_showcase_60s` batch route as the accepted style grid. They deliberately
use no shader: none advertises PBR material channels, and the first decision should isolate texture
art direction. An Unbound cross can be added only after the no-shader runtime and visual gates pass;
it must be a separate lighting-axis profile, not a replacement for this aligned comparison.

## Rejected as executable full-material profiles

| Requested family / candidate | 26.2 finding | Decision |
|---|---|---|
| retro / [F8thful](https://modrinth.com/resourcepack/f8thful) | filtered Modrinth versions are empty; latest 13.0 declares only 1.21.9–1.21.11 | keep research-only; use the independently sourced Vanilla 8x8 candidate |
| sci-fi / [Genesis](https://modrinth.com/resourcepack/genesis_pack) | free 64x version 2.2 stops at 1.21.5; higher tiers are paid and do not establish 26.2 or ML rights | no profile and no compatibility override |
| sci-fi / [Cyberpunk Edgerunners](https://modrinth.com/resourcepack/cyberpunk-edgerunners) | exact 26.2 exists, but the author calls it GUI-only; audio/fonts/menu are overlays | do not present it as world material |
| medieval / [Conquest_](https://modrinth.com/resourcepack/conquest_) | filtered Modrinth versions are empty; latest is 1.21.11 | no profile |
| hand-painted / [Painted Pixels](https://modrinth.com/resourcepack/painted-pixels-pack) | exact 26.2 exists, but the author calls it work in progress and lists only blocks/environment | retain as future partial-coverage research, not a full-material grid column |
| dark/horror / [Backrooms Retextured](https://modrinth.com/resourcepack/backrooms-retextured) | exact 26.2, but explicitly replaces selected map-building blocks | no full-material profile |
| dark/horror / [Drakon](https://modrinth.com/resourcepack/drakon) | exact 26.2, but its author lists a partial environment/block/item recolour | no full-material profile |
| dark/horror / [The Night of the Living Pumpkins](https://modrinth.com/resourcepack/the-night-of-the-living-pumpkins) | exact 26.2 seasonal audio/block/entity/environment/item subset | no full-material profile |
| accessibility / [High Contrast Extended](https://modrinth.com/resourcepack/high-contrast-extended) | exact 26.2 and CC0, but explicitly a GUI extension | catalogued as `ui_only`; not a material/style prompt target |
| medieval / [Excalibur](https://www.curseforge.com/minecraft/texture-packs/excalibur) and dark/horror / [Dark Fantasy Visuals](https://www.curseforge.com/minecraft/texture-packs/dark-fantasy-java-resource-pack) | official CurseForge-only sources are outside the Modrinth-only fail-closed downloader | no unofficial mirror, manual ZIP, or compatibility claim |

The exclusions are intentional coverage honesty. UI, item, sky, seasonal, or map-making overlays may
later become separate prompt-edit axes, but they cannot count as a full material lineage.

## Training eligibility

Every accepted profile remains `research_only` in the license catalog. Public access, a successful
hash audit, and eventual runtime acceptance do not establish permission to train or redistribute a
dataset. The CC and ARR fields record upstream terms; the catalog continues to require explicit ML
permission (and written authorization where applicable) before any candidate becomes publishable
training data.
