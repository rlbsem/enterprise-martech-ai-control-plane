# Groundbreakers engineering diagram pilot

The **first** architecture SVG uses the Groundbreakers Digital colour and typography language. The other five SVGs remain in the original neutral theme until this pilot is reviewed and accepted.

**Design tokens:** midnight `#10101D`, charcoal-indigo `#181825`, graphite `#2A2A3D`, elevated slate `#2E2E45`, neutral border `#7C7CAF`, lavender `#B8B8FF`, off-white `#F0F0FF`. Later stateful diagrams may additionally use muted teal `#81C5B0` (supported), amber `#E3BC7F` (unknown) and rose `#D98EA3` (unsupported).

**Type:** JetBrains Mono for section headings where available; IBM Plex Sans for labels and explanatory text with system fallbacks. The first section heading has an intentional line break to prevent clipping while preserving its words.

After `npm ci`, regenerate the pilot reproducibly:

```bash
python docs/diagrams/render_groundbreakers_pilot.py
python scripts/repo_check.py
```

The repository's original `npm run diagrams` command remains the **neutral all-six renderer**. It may overwrite this pilot SVG; rerun the dedicated pilot command afterward until a portfolio-wide rollout is approved. Neither of these documentation scripts modifies executable control-plane runtime behavior.

Keep all six Mermaid source files and SVGs in the repository. Do not represent coloured nodes as stronger evidence than their documented control and recovery semantics.
