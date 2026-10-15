# Groundbreakers engineering diagram standard

**All six architecture diagrams** use the approved Groundbreakers Digital midnight/indigo, lavender and off-white visual language. The 01 Control Plane diagram was the approved design benchmark. Semantic states remain visible through labels, arrows, shape and distinct muted border colours.

| Token | Hex | Purpose |
| --- | --- | --- |
| Midnight | `#10101D` | SVG background |
| Charcoal | `#181825` | Containers and boundaries |
| Graphite | `#2A2A3D` | Execution/system nodes |
| Elevated slate | `#2E2E45` | Controls and statuses |
| Lavender | `#B8B8FF` | Governing controls, connections |
| Off-white | `#F0F0FF` | Text |
| Teal | `#81C5B0` | Verified facts/receipts |
| Amber | `#E3BC7F` | Decisions requiring review |
| Rose | `#D98EA3` | Exceptions/blocked activity |

**Fonts:** JetBrains Mono for section titles where available; IBM Plex Sans for readable node text, with system fallbacks. The first heading was intentionally wrapped to prevent clipping. No colours imply additional security guarantees or verified deployments.

## Regeneration

From the repository root, after `npm ci`:

```sh
python docs/diagrams/render_groundbreakers_all.py
python scripts/repo_check.py
```

The original repository-wide `npm run diagrams` script remains in place because it is part of the verified source fingerprint. It renders the old neutral CLI defaults; **use the branded renderer above for published SVGs**. Both invoke the same pinned Mermaid CLI. The branded script regenerates all six SVGs **and** their integrity hashes together, without modifying runtime or verified source files.
