# Editing architecture.drawio Programmatically

The `architecture.drawio` file is ~900KB because it embeds SVG icons as URL-encoded data URIs in cell `style` attributes. Three cells alone account for ~870KB of embedded image data (`RarJMW7DFZvwC3-4OA5Q-2`, `0A8BIggRliRz0gP7DopN-16`, `RarJMW7DFZvwC3-4OA5Q-3`). This makes it too large to read into context or pass to MCP tools directly.

## Approach

1. **Parse with Python's `xml.etree.ElementTree`** — it handles the large file fine.
2. **Inspect cells** by iterating `root.iter('mxCell')` and filtering by `id`. Truncate or skip base64/URL-encoded image data in styles when printing.
3. **Modify** geometry (`mxGeometry` child), style attributes, values, and edges programmatically.
4. **Write back** with `tree.write(path, xml_declaration=True, encoding='unicode')`.
5. **Preview via draw.io MCP** — strip embedded images (replace `image=data:image/svg+xml,...` with a tiny placeholder SVG), reducing from ~900KB to ~42KB, then pass the stripped XML to `mcp__drawio__open_drawio_xml`. The real file on disk keeps the original images.

## Key Scripts

### Inspect all cells (truncating images)

```python
import xml.etree.ElementTree as ET

tree = ET.parse('architecture.drawio')
root = tree.getroot()

for cell in root.iter('mxCell'):
    cid = cell.get('id', '')
    style = cell.get('style', '')
    # Truncate embedded images
    if len(style) > 500:
        style = style[:100] + '[TRUNCATED...]'
    val = cell.get('value', '')[:100]
    parent = cell.get('parent', '')
    src = cell.get('source', '')
    tgt = cell.get('target', '')
    geo = cell.find('mxGeometry')
    geo_str = ''
    if geo is not None:
        geo_str = f"x={geo.get('x','')},y={geo.get('y','')},w={geo.get('width','')},h={geo.get('height','')}"
    print(f"ID={cid} | parent={parent} | src={src} | tgt={tgt} | geo={geo_str} | val={val}")
```

### Modify cells

```python
import re

def set_style(cell, key, val):
    style = cell.get('style', '')
    if f'{key}=' in style:
        style = re.sub(f'{key}=[^;]*', f'{key}={val}', style)
    else:
        style = style.rstrip(';') + f';{key}={val};'
    cell.set('style', style)

def set_geo(cell, **kwargs):
    geo = cell.find('mxGeometry')
    for k, v in kwargs.items():
        geo.set(k, str(v))
```

### Strip images for MCP preview

```python
import re

content = open('architecture.drawio').read()
placeholder = "data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A//www.w3.org/2000/svg%22%20width%3D%2248%22%20height%3D%2248%22%3E%3Crect%20fill%3D%22%23ddd%22%20width%3D%2248%22%20height%3D%2248%22/%3E%3C/svg%3E"

# Parse, replace styles with len > 5000
tree = ET.parse('architecture.drawio')
for cell in tree.getroot().iter('mxCell'):
    style = cell.get('style', '')
    if len(style) > 5000:
        style = re.sub(r'image=data:image[^;]+', f'image={placeholder}', style)
        cell.set('style', style)

tree.write('/tmp/preview.drawio', xml_declaration=True, encoding='unicode')
# Then pass content of /tmp/preview.drawio to mcp__drawio__open_drawio_xml
```

## Rendering a preview WITHOUT the draw.io MCP (self-service render loop)

If the draw.io MCP isn't available and `draw.io.app` can't export (its Electron CLI
needs a GUI session — from an agent shell `--export` prints `Error: Export failed`
and writes nothing), you can still SEE your edits. This loop was proven out in the
v3 routing pass — **use it; never ship a blind edit again:**

1. **Strip shadows** from a copy (`shadow="1"`→`shadow="0"` on the model + `shadow=1;`
   in cell styles). Shadows crash the headless exporter. **Real images can stay** — the
   SVG exporter + Chrome handle them fine (only the docker *PNG* exporter crashes on them).
2. **Export to SVG via the headless docker image** (this is the reliable exporter;
   the docker *PNG* exporter is GPU-flaky and often fails with `Empty export data`):
   ```bash
   docker run --rm -v "$PWD":/data rlespinasse/drawio-desktop-headless \
     -x -f svg -o /data/preview.svg /data/preview.drawio
   ```
3. **Force a white background** in the SVG (replace `background: transparent` and
   `color-scheme: light dark`, and inject a white `<rect>` as the first child) — else
   pale-filled cards render dark/invisible.
4. **Rasterize with headless Google Chrome** — this is the renderer that faithfully
   draws the HTML labels (`foreignObject`). `qlmanage` works but **silently drops the
   later `foreignObject` labels** (QuickLook caps them), so boxes render with no text —
   don't trust it for text. Chrome:
   ```bash
   CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
   "$CHROME" --headless --disable-gpu --hide-scrollbars --force-device-scale-factor=2 \
     --window-size=2400,1090 --screenshot=/tmp/full.png "file:///tmp/full.svg"
   ```
   Set `--window-size` to the SVG's aspect ratio. Then `Read /tmp/full.png`.
5. **To zoom a region:** do NOT crop by changing the SVG `viewBox` — that breaks
   `foreignObject` positioning in Chrome (text disappears). Instead wrap the *inline*
   SVG in an HTML file inside a scaled/translated `<div>` and screenshot that:
   ```html
   <div style="transform:scale(3.4) translate(-882px,-262px);transform-origin:0 0">…inline SVG…</div>
   ```
   `<img src=svg>` does NOT render foreignObject — the SVG must be inlined in the HTML.

## Gotchas that cost a full blind-shipped revision

- **New vertex cells MUST carry `vertex="1"`.** If you `ET.SubElement` a shape cell
  and forget it, draw.io still resolves edges that target it (endpoints land in the
  right place) but **never draws the box** — you get arrows pointing into empty space.
  Edges likewise need `edge="1"`. This silently passed every geometry/overlap check
  and only showed up on render.
- **`shadow="1"` on `<mxGraphModel>` crashes the headless renderer** (`Export failed`).
  Strip it (and per-cell `shadow=1`) for previews only; keep it in the real file.
- **Pale fills vanish on a transparent/dark preview.** The exported SVG carries
  `color-scheme: light dark`; render on a forced white background or near-white cards
  (`#F0FDFA`, `#EEF2FF`) look empty.

### Verify horizontal line alignment

The diagram's key constraint is that DB read/write arrows stay perfectly horizontal. This depends on absolute Y positions matching between connected cells. Use this script to verify:

```python
def abs_center_y(cell_id, cells):
    """Get absolute center Y of a cell, considering parent container."""
    cell = cells[cell_id]
    geo = cell.find('mxGeometry')
    y = float(geo.get('y', '0'))
    h = float(geo.get('height', '0'))
    center = y + h/2
    parent = cell.get('parent', '')
    if parent and parent not in ('0', '1') and parent in cells:
        pgeo = cells[parent].find('mxGeometry')
        if pgeo is not None:
            center += float(pgeo.get('y', '0'))
    return center

# Critical alignments to check:
# usersMod ↔ pgUser (both exitY/entryY=0.5)
# searchMod exitY=0.208 ↔ pgList center
# searchMod exitY=0.792 ↔ pgBerlin center
# ingestListings ↔ pgList (both center Y)
# ingestBerlin ↔ pgBerlin (both center Y)
# listSrc ↔ ingestListings
# bod ↔ ingestBerlin
```

## Layout Invariants

When moving zones within backend/pgBox/ingestionBox, all three containers must be adjusted in sync:

| Zone | backendBox relative Y | pgBox relative Y | ingestionBox relative Y |
|------|----------------------|-------------------|------------------------|
| User data row | usersMod: y=30 | pgUser: y=30 | (none) |
| Middle data row | chatMod/searchMod: y=110 | pgList: y=120 | ingestListings: y=120 |
| Bottom data row | (searchMod spans both) | pgBerlin: y=260 | ingestBerlin: y=260 |

The searchMod exit proportions (0.208 and 0.792) were calculated for height=240 and these specific y-offsets. If you change searchMod's height, recalculate the exit proportions.
