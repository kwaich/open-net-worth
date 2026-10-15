import { readFile, writeFile } from 'node:fs/promises'
import sharp from 'sharp'

// brand-source/ holds the owner's two exports of the mark (a faceted white disc on a black
// tile): logo.svg (rounded tile) and logo-square.svg (square tile). Everything else is derived.
const src = new URL('./brand-source/', import.meta.url)
const app = new URL('../../frontend/public/', import.meta.url)
const docs = new URL('../', import.meta.url)
const desktop = new URL('../../desktop/resources/', import.meta.url)
const svgFile = (url, text) => writeFile(url, text.endsWith('\n') ? text : `${text}\n`)
const rounded = await readFile(new URL('logo.svg', src), 'utf8')
const square = await readFile(new URL('logo-square.svg', src), 'utf8')

const FACETS = /(<path id="facet-cuts"[^>]* d=")([^"]+)"/
const facetData = rounded.match(FACETS)[2]
if (!facetData || !square.includes(facetData)) throw new Error('The two exports disagree on the facet cuts.')
// Sub-0.01 precision is invisible at 1024 units and halves the inline copies.
const trim = (d) => d.replace(/-?\d+\.\d+/g, (n) => String(Math.round(Number(n) * 100) / 100))
const swap = (svg) => svg.replaceAll('#000000', '@@').replaceAll('#ffffff', '#000000').replaceAll('@@', '#ffffff')
// The owner's cuts are hairlines (~14 units of 768): under 1px at 20-32px, so a header mark or
// favicon melts into grey, worst on phones. Small-size files widen every cut by BOLD units.
const BOLD = 12 // bake-mark.py widens the stand-alone mark by the same amount
const black = (svg) => svg.replaceAll('fill="#ffffff"', 'fill="#000000"')

// The mark standing alone: the disc with the widened cuts already subtracted (bake-mark.py), so it
// sits on any background. Plain geometry, never a <mask>: WebKit draws a masked SVG <img> at 1x,
// which blurred the mark on every iPhone. 768 units wide (the disc at scale 1.2), cropped so a
// 20px sidebar slot keeps the full weight.
const markPath = (await readFile(new URL('logo-mark.d', src), 'utf8')).trim()
const mark =
  '<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1080" viewBox="128 128 768 768" role="img">' +
  '<title>Open Net Worth</title>' +
  `<g transform="translate(512 512) scale(1.2)"><path class="mark" fill="#ffffff" fill-rule="evenodd" d="${markPath}"/></g></svg>`

const light = swap(rounded)
const bold = rounded.replace('stroke="none"', `stroke="#000000" stroke-width="${BOLD}" stroke-linejoin="round"`)
const favicon = bold
  .replace('<rect ', '<style>@media(prefers-color-scheme:light){.background{fill:#fff}.mark{fill:#000}.cut{fill:#fff;stroke:#fff}}</style><rect class="background" ')
  .replace('<circle ', '<circle class="mark" ')
  .replace('<g id="facets" ', '<g id="facets" class="cut" ')
// The docs tab icon is the bare mark, no tile: browsers draw favicons inside their own tile, so
// a tiled icon read as a box in a box (owner, 2026-10-03). White on dark, black on light.
const docsFavicon = mark.replace('<g ', '<style>@media(prefers-color-scheme:light){.mark{fill:#000}}</style><g ')

await svgFile(new URL('logo-light.svg', app), light)
await svgFile(new URL('logo-mark.svg', app), mark)
await svgFile(new URL('favicon.svg', app), favicon)
await svgFile(new URL('src/assets/logo.svg', docs), mark)
await svgFile(new URL('src/assets/logo-light.svg', docs), black(mark))
await svgFile(new URL('public/favicon.svg', docs), docsFavicon)
await svgFile(new URL('logo.svg', app), rounded)
await svgFile(new URL('app-icon.svg', desktop), rounded)

// iOS and Android apply their own masks; their install icons need the opaque square tile. The
// disc (radius 384) sits inside Android's central maskable safe circle (radius 409.6).
for (const size of [192, 512]) {
  await sharp(Buffer.from(rounded)).resize(size, size).png()
    .toFile(new URL(`icons/icon-${size}.png`, app).pathname)
  await sharp(Buffer.from(square)).resize(size, size).png()
    .toFile(new URL(`icons/icon-maskable-${size}.png`, app).pathname)
}
await sharp(Buffer.from(square)).resize(180, 180).png()
  .toFile(new URL('icons/apple-touch-icon.png', app).pathname)

async function makeIco() {
  const sizes = [16, 32, 48, 64]
  const images = await Promise.all(sizes.map((size) =>
    sharp(Buffer.from(bold)).resize(size, size).png().toBuffer()))
  const header = Buffer.alloc(6 + 16 * sizes.length)
  header.writeUInt16LE(1, 2)
  header.writeUInt16LE(sizes.length, 4)
  let offset = header.length
  images.forEach((image, i) => {
    const entry = 6 + i * 16
    header[entry] = sizes[i]
    header[entry + 1] = sizes[i]
    header.writeUInt16LE(1, entry + 4)
    header.writeUInt16LE(32, entry + 6)
    header.writeUInt32LE(image.length, entry + 8)
    header.writeUInt32LE(offset, entry + 12)
    offset += image.length
  })
  await writeFile(new URL('favicon.ico', app), Buffer.concat([header, ...images]))
}

await makeIco()
