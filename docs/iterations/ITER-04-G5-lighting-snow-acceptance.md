# G5 bright-moon and snow-surface iteration

Status: implementation ready for a new remote render; **not visually accepted yet**.
The prior G5 evidence under `runs/evidence/iter04_lighting_6629bde_01` remains an
honest rejected/partial baseline and must not be relabelled.

## Bright cool moonlight

The six controlled lighting/weather profiles continue to emit one byte-identical
Complementary Unbound sidecar. Noon, golden hour, midnight, rain, clear-snow and
snowfall therefore remain single-axis renderer comparisons. There is no global
exposure override: the stronger response is confined to Unbound's night lighting,
night atmosphere, night light-shaft and moon-phase controls.

The option domains were checked directly against
`ComplementaryUnbound_r5.8.1.zip` (SHA-256
`bb89b1fc54687d4147a837fb2e3c3f7261a13bee51819761e9b6a91cb7915965`):

- `shaders/lib/common.glsl` enumerates `LIGHT_NIGHT_{R,G,B}` and
  `ATM_NIGHT_{R,G,B}` from `0.50` through `2.00`, and their intensity controls
  through `2.00`.
- The same source enumerates `LIGHTSHAFT_NIGHT_I` through `200`.
- It exposes `MOON_PHASE_INF_LIGHT`, `MOON_PHASE_INF_ATMOSPHERE`,
  `MOON_PHASE_INF_REFLECTION`, and phase multipliers through `2.00`.
- `shaders/lib/colors/moonPhaseInfluence.glsl` applies those multipliers only as
  daylight visibility falls, including terrain lighting and atmosphere paths.

Selected values preserve a cool channel ordering and phase ordering:

| Control | Selected |
|---|---:|
| `LIGHT_NIGHT_R/G/B/I` | `0.80 / 1.30 / 1.80 / 2.00` |
| `ATM_NIGHT_R/G/B/I` | `0.80 / 1.20 / 1.60 / 1.80` |
| `LIGHTSHAFT_NIGHT_I` | `200` |
| full / partial / dark moon | `1.50 / 1.30 / 1.10` |

The next G5 review must verify visible cool directional shaping on the floor and
facades without making midnight read as daylight or allowing emissive blocks to
remain the dominant illumination.

## Snow accumulation scene variant

Both snow endpoints declare the same `snow_accumulation_v1` scene variant and the
same ordered `scene.additions` payload. The additions execute after the canonical
base scene and before capture:

- all showcase walking surfaces at absolute `y=63` are replaced in-place with
  `minecraft:snow_block`;
- both reflecting-basin water surfaces at absolute `y=63` are replaced in-place
  with `minecraft:ice`;
- every mutation is a structured `fill ... replace ...` entry with a named region,
  retained in `manifest.profile.config.world_state.scene` and
  `manifest.world.state.scene`;
- additions have the same `[0, 64, 0]` origin as the base scene, are individually
  bounded by the server fill limit, and add twelve required mutation receipts;
- no block is added above the existing surface, so the route height and the
  canonical 429-cell obstacle footprint are unchanged.

The clear-snow and snowfall pair therefore differs only in `weather`; the scene
variant, ordered additions, biome, time, renderer and action contract remain
byte-identical/static pair invariants. Ordinary noon, golden-hour, midnight and
rain profiles do not carry this variant.

The next grid must confirm visible accumulation in all four standard moments:
material close-up, scene wide, frozen-water view and motion.

## Snow particle limitation

Unbound 5.8.1 exposes weather opacity, general close-particle reduction, rain style
and improved-rain controls, but no snowflake size or snow-only particle-scale
control. Changing the shared opacity or general particle control would also alter
the already accepted rain endpoint. This iteration therefore does not claim to fix
flake geometry: oversized vanilla snow sprites remain a known visual limitation to
review after the accumulated-surface rerender.
