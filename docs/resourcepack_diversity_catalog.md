# Resource-pack diversity and ML-rights contract

`configs/resourcepack_catalog.yml` is the canonical candidate catalog for the material/style
axis. It answers three independent questions:

1. What visual lineage and style family does a pack represent?
2. Has the exact candidate only been researched, been configured, or been runtime-verified?
3. May captures made with it enter a redistributable ML training or held-out split?

These answers must never be collapsed. A public download, a paid subscription, successful game
loading, and permission to publish a showcase video are **not** ML-training authorization.

## Current result

The snapshot dated 2026-07-10 contains 47 candidates across 39 lineages and maps all 36 resource
pack keys present in `configs/asset_sets.yml`. It covers all nine required visual families:

| Family | Representative candidates |
|---|---|
| `realism_pbr` | Legendary RT, Patrix, Optimum Realism, PrettyRealistic, Yitalith |
| `architectural_cinematic` | ModernArch, Stylista, Luna HD |
| `vanilla_plus` | Faithful, Default HD, Reimagined, Natural |
| `handpainted_fantasy_medieval` | Ashen, Fantasy Texture Pack, Conquest, Excalibur |
| `cartoon_minimal` | Bare Bones, Quadral, Simplified, MS Painted |
| `retro_lowres` | Vanilla 8x8, F8thful 8x |
| `scifi_cyber` | Genesis |
| `dark_horror` | Dark Fantasy Visuals |
| `accessibility_high_contrast` | Minecraft built-in High Contrast and High Contrast Extended UI controls |

All 47 currently remain `research_only` for train and held-out publication because none of the
reviewed official pages explicitly grants ML-training use. This is a fail-closed rights status,
not a claim that authorization is impossible. Vanilla 8x8, Fantasy Texture Pack, and MS Painted
have exact upstream 26.2 files and fail-closed installer entries, but remain
`configured_not_runtime_verified`; source compatibility is not a runtime visual claim. Other
research candidates remain `research_only` or `compatibility_unknown`.

The exact 26.2 version/file/hash audit, executable profile subset, and honest overlay/partial-pack
rejections are documented in
[`ITER-04-style-expansion-26.2.md`](iterations/ITER-04-style-expansion-26.2.md).

## Are the highest-resolution packs paid?

Often, yes. The official [Patrix project](https://modrinth.com/resourcepack/patrix-32x) publishes
32x while directing users to 64x/128x/256x tiers; [ModernArch](https://modrinth.com/resourcepack/modernarch)
publishes 128x and offers higher tiers through its creator; and the official
[Luna HD site](https://www.lunahd.com/) lists a free 64x tier plus high-resolution Patreon tiers.
The catalog therefore represents public and paid resolutions as separate candidates with the
same `lineage_id`. A purchase proves lawful access to a copy under its terms; it does not by itself
permit dataset creation, ML training, or redistribution.

Other deliberately diverse official research links include
[Genesis](https://modrinth.com/resourcepack/genesis_pack),
[F8thful](https://modrinth.com/resourcepack/f8thful),
[Conquest](https://modrinth.com/resourcepack/conquest_),
[Excalibur](https://www.curseforge.com/minecraft/texture-packs/excalibur),
[MS Painted](https://www.planetminecraft.com/texture-pack/ms-painted/), and Minecraft's
[built-in High Contrast UI description](https://feedback.minecraft.net/hc/en-us/articles/13332060932621-Minecraft-Java-Edition-1-19-4-Pre-release-1).
These links are provenance references, not blanket permissions.

## Rights gate

Every candidate records:

- official source/provider/project URL and a creator contact URL when available;
- access tier (`free` or `paid`);
- SPDX identifier, `LicenseRef-*`, or literal `unknown`;
- ML-training permission and redistribution permission independently as `explicit`, `unknown`, or
  `denied`, each with its own evidence URL;
- written authorization status and evidence path;
- train and held-out publication eligibility.

`publishable_train` and `publishable_heldout` are rejected unless all of these are true:

1. the license is known;
2. ML-training permission is explicit and linked to evidence;
3. redistribution permission is explicit and linked to evidence;
4. the exact candidate is runtime-verified with versioned evidence;
5. paid or All-Rights-Reserved material has a complete written authorization override.

Open-source or Creative Commons redistribution terms are recorded as redistribution evidence,
but the catalog intentionally does not infer explicit ML permission from them. For example, the
[Excalibur project terms](https://www.curseforge.com/minecraft/texture-packs/excalibur) discuss
showcase-video use while restricting redistribution; its ML field therefore remains `unknown`.

## Required written authorization record

When public terms do not explicitly cover ML, request a signed or otherwise auditable written grant
from the actual rights holder. Store the private evidence outside the public repository and record
only its controlled relative evidence path. The grant must identify at least:

- rights-holder legal name and contact;
- candidate/project, all covered versions and resolutions, and their `lineage_id`;
- permission to capture, store, transform, and process rendered frames/video;
- explicit training, fine-tuning, validation, and held-out evaluation permission;
- whether derived datasets, clips, embeddings, model weights, and outputs may be redistributed;
- commercial/noncommercial scope, attribution, territory, duration, sublicensing, and revocation;
- signature/date and an immutable evidence hash in the private rights register.

After authorization, update `permissions.ml_training`, `permissions.redistribution`, and
`permissions.written_authorization` together. Never encode a verbal assumption as `explicit`.

## Lineage-safe splits

Different resolutions, versions, add-ons, and paid/public tiers from the same visual source keep one
`lineage_id`. `validate_lineage_split_assignments()` rejects assigning any one lineage across
train/validation/test. This prevents, for example, training on Patrix 32x and claiming a Patrix 256x
test as held-out generalization.

Coverage and rights gaps are available without network or asset downloads:

```python
from pathlib import Path

from mcdata.config import load_yaml
from mcdata.resourcepack_catalog import catalog_coverage_report, load_resourcepack_catalog

root = Path(".")
catalog = load_resourcepack_catalog(
    root / "configs/resourcepack_catalog.yml",
    asset_config_path=root / "configs/asset_sets.yml",
)
report = catalog_coverage_report(catalog, asset_config=load_yaml(root / "configs/asset_sets.yml"))
```

The report counts candidates by family, integration state, access tier, ML/redistribution status,
publishable coverage, lineage count, and configured-asset mapping gaps. It is deterministic and
does not contact upstream services.
