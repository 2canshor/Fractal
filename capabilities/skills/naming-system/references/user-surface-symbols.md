# Select User-Surface Symbol

This is a required sub-step of the existing Blueprint element `^ Naming System`. It is not a separate Blueprint element, System Review Flow, user-facing Action, or Command.

## Trigger and completion

Run this sub-step before registering any new capability as a user-facing `Action` or `Command`, or when changing that entry's meaning. Hidden reusable dots do not receive a user-surface symbol.

The sub-step is complete only when all of the following are true:

1. Naming System has classified the entry as an `Action` or `Command` after the source and overlap check.
2. The semantic job has been reduced to the smallest visual idea without copying a provider logo or another entry's symbol.
3. Current local SF Symbols metadata has been searched without GUI control where possible.
4. The selected Apple-owned identifier exists, is unique, and uses the required container: circle for an `Action`, square for a `Command`.
5. The canonical policy records the rationale, search terms, and rejected alternatives.
6. The deterministic renderer produces checked-in small and large PNG assets plus a hash manifest.
7. The actual PNGs are inspected at 16, 20, 24, and 32 px in light and dark appearances.
8. `agents/openai.yaml`, generated user-surface metadata, registry hashes, tests, and adapter projections agree.
9. An isolated Codex App Server calls `plugin/installed` before forced `skills/list` and returns non-null `iconSmall` and `iconLarge` paths for the candidate.
10. Live UI success remains unclaimed until the approved System Version path installs the candidate and the real Skills view is inspected.

## Exact method used for the current surface

The current implementation was established on 2026-08-24 with local `SF Symbols beta 7.0`. No GUI or computer control was needed.

### 1. Read the canonical user surface

Read the live `user-surface-policy.json` and separate the entries by `interface_type`. The current surface had six Actions and four Commands. Existing symbol identifiers were treated as unavailable for reuse.

### 2. Read Apple metadata directly

The installed app supplied the authoritative local discovery material:

- `Contents/Info.plist` for the SF Symbols app version;
- `Contents/Resources/Metadata/name_availability.plist` for exact identifiers and introduction versions;
- `Contents/Resources/Metadata/symbol_search.plist` for semantic search terms;
- `Contents/Resources/Fonts/SFSymbolsFallback.otf` for the bundled symbol fallback source.

Use metadata search for discovery, not filename guessing. A bounded command equivalent to the one used is:

```sh
python3 - "$SF_SYMBOLS_APP" automate repeat clock match align adjust slider perspective <<'PY'
import plistlib
import sys
from pathlib import Path

app = Path(sys.argv[1])
queries = [value.lower() for value in sys.argv[2:]]
metadata = app / "Contents" / "Resources" / "Metadata"
with (metadata / "name_availability.plist").open("rb") as source:
    available = plistlib.load(source)["symbols"]
with (metadata / "symbol_search.plist").open("rb") as source:
    search = plistlib.load(source)
for name in sorted(available):
    terms = [str(value).lower() for value in search.get(name, [])]
    if any(query in name.lower() or any(query in term for term in terms) for query in queries):
        print(name, available[name], ",".join(terms), sep="\t")
PY
```

### 3. Compare semantic candidates

Search from the user job, not the Skill's English label alone. Record at least one rejected valid alternative so the decision is reviewable. The current decisions were:

| Entry | Search terms | Selected | Alternatives considered | Reason |
|---|---|---|---|---|
| `assess` | assess, balance, two-sided, arrow | `arrow.left.arrow.right.square.fill` | `scale.3d`, `arrow.left.and.right.square.fill` | Two opposing arrows show independent Case For and Case Against. |
| `automate` | automate, repeat, clock | `repeat.circle.fill` | `clock.circle`, `arrow.trianglehead.2.clockwise.rotate.90.circle` | A repeat loop describes recurring or triggered work. |
| `complete` | complete, checkmark | `checkmark.square.fill` | `checkmark.seal`, `inset.filled.circle` | A checkmark communicates completion without adding another lifecycle metaphor. |
| `create` | create, plus, new | `plus.circle.fill` | `plus.diamond.fill`, `document.badge.plus` | Plus is the smallest object-neutral creation idea. |
| `edit` | edit, pencil, adjust | `pencil.circle.fill` | `square.and.pencil.circle.fill`, `slider.horizontal.2.square` | Pencil communicates changing an existing object; sliders are reserved for Match. |
| `match` | match, align, adjust, slider, perspective, reality | `slider.horizontal.2.square` | `equal.square.fill`, `perspective`, `scope`, `align.horizontal.center.fill` | Two sliders show Plan dimensions being adjusted to reality; equals falsely implies they already match. |
| `publish` | publish, send, paperplane | `paperplane.circle.fill` | `square.and.arrow.up.circle`, `paperplane.circle` | Paper plane shows outward delivery across send, release, and publish cases. |
| `research` | research, search, magnifyingglass | `magnifyingglass.circle.fill` | `doc.text.magnifyingglass`, `sparkle.magnifyingglass` | Magnifying glass stays evidence-source neutral. |
| `review` | review, inspect, eye, checkmark | `eye.circle.fill` | `checkmark.circle.fill`, `scope`, `viewfinder.circle.fill` | Eye communicates inspection without claiming approval or completion. |
| `version` | version, branch, refresh, clockwise | `arrow.clockwise.square.fill` | `arrow.trianglehead.branch`, `square.stack.3d.up`, `v.square` | Clockwise lifecycle movement shows applying a new exact System Version batch. |

### 4. Enforce the kind-level visual grammar

- Every Action uses a circle-contained symbol.
- Every Command uses a square-contained symbol.
- Fill is optional when the semantic symbol exists only as an outline.
- Every selected identifier must be unique across the visible surface.
- Colour reinforces the class but never owns the distinction: Actions use blue; Commands use purple.

### 5. Record the selection in canonical policy

Each entry records the exact identifier and the evidence needed to understand the choice:

```json
{
  "symbol": {
    "system": "sf-symbols",
    "name": "slider.horizontal.2.square",
    "selection": {
      "rationale": "Two sliders show Project Plan dimensions being adjusted to current reality.",
      "search_terms": ["match", "align", "adjust", "slider", "perspective", "reality"],
      "alternatives_considered": [
        "equal.square.fill",
        "perspective",
        "scope",
        "align.horizontal.center.fill"
      ]
    }
  }
}
```

### 6. Render without computer control

Run the maintained AppKit renderer against canonical policy and local SF Symbols metadata:

```sh
xcrun swift capabilities/skills/render_sf_symbol_assets.swift \
  --policy "$FRACTAL_PRIVATE_ROOT/system/components/user-surface-policy.json" \
  --skills-root capabilities/skills \
  --sf-symbols-app "$SF_SYMBOLS_APP" \
  --manifest src/fractal/data/user-surface-symbols.json \
  --contact-sheet-dir "$SYMBOL_QA_ROOT"
```

The renderer writes 400 px and 800 px RGBA PNGs, a deterministic manifest, and temporary light/dark contact sheets. It fails if an identifier or alternative is unknown, a symbol is duplicated, the container conflicts with the interface type, or the selection record is incomplete.

### 7. Inspect the rendered result, not the prose decision

Inspect both contact sheets at 16, 20, 24, and 32 px. During the current run, `slider.horizontal.2.square` initially used white slider glyphs. They disappeared on a light background because the symbol is outlined. The correction kept the stock symbol, rejected a custom filled background, and rendered outlined Commands monochrome purple. The final Match sliders are visible in both appearances.

### 8. Bind UI metadata and hashes

Add `icon_small` and `icon_large` relative paths to the entry's `agents/openai.yaml`. Regenerate the private component registry and user surface through the governed CLI. Validate PNG type, size, alpha, local path, SHA-256, unique asset digests, interface class, and exact manifest agreement.

### 9. Verify staged platform parsing

Build the adapters twice and compare their tree manifests. Smoke all supported platforms without calling this live execution evidence. For Codex, use an isolated `CODEX_HOME`, call `plugin/installed`, then forced `skills/list`, and require the candidate entries to return enabled with non-null icon paths that resolve to real files.

### 10. Preserve the activation boundary

Source, assets, metadata, registry, tests, adapter smoke, and isolated parsing prove only a staged candidate. Installation, exact visible-surface audit, fresh-session activation, restore proof, and publication remain separate `/version` authority gates.
