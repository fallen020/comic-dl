# Supported Sites

The 30 built-in scrapers shipped with comic-dl. The live registry — including
any third-party plugins — is shown by `comic-dl --list-sources`.

## Sites

| Site | Domain | URL pattern | Chapters | Series |
| :--- | :----- | :---------- | :------- | :----- |
| **Asura Scans** | `asurascans.com` | `/comics/{series}/` | — | Yes |
| **Asura Scans** | `asurascans.com` | `/comics/{series}/chapter/{n}` | Yes | — |
| **DivaScans** | `divascans.org` | `/series/comic/{slug}/` | — | Yes |
| **DivaScans** | `divascans.org` | `/series/comic/{slug}/chapter/{n}` | Yes | — |
| **E-Hentai** | `e-hentai.org` | `/g/{gid}/{token}/` | Yes | — |
| **Thunderscans** | `en-thunderscans.com` | `/comics/{slug}/` | — | Yes |
| **Thunderscans** | `en-thunderscans.com` | `/{slug}-chapter-{n}/` | Yes | — |
| **FlameComics** | `flamecomics.xyz` | `/series/{id}/` | — | Yes |
| **FlameComics** | `flamecomics.xyz` | `/series/{id}/{token}/` | Yes | — |
| **FSIComics** | `fsicomics.com` | `/all-porn-comics/...` | — | Yes |
| **FSIComics** | `fsicomics.com` | `/{comic-slug}/` | Yes | — |
| **GEDE Comix** | `gedecomix.com` | `/porncomic/{series}/` | — | Yes |
| **GEDE Comix** | `gedecomix.com` | `/porncomic/{series}/{chapter}/` | Yes | — |
| **GenzToons** | `genztoons.org` | `/series/{slug}/` | — | Yes |
| **GenzToons** | `genztoons.org` | `/chapter/{uid}/` | Yes | — |
| **HD Porn Comics** | `hdporncomics.com` | `/{slug}-sex-comic/` | Yes | — |
| **HiveToons** | `hivetoons.org` | `/series/{slug}/` | — | Yes |
| **HiveToons** | `hivetoons.org` | `/series/{slug}/chapter-{n}/` | Yes | — |
| **IMHentai** | `imhentai.xxx` | `/gallery/{id}/` | — | — |
| **IMHentai** | `imhentai.xxx` | `/view/{id}/{n}/` | Yes | — |
| **Kagane** | `kagane.to` | `/series/{id}/` | — | Yes |
| **Kagane** | `kagane.to` | `/series/{id}/reader/{book}` | Yes | — |
| **Kingofshojo** | `kingofshojo.com` | `/{slug}-chapter-{n}/` | Yes | — |
| **KodokuStudio** | `kodokustudio.com` | `/manhua/{slug}/` | — | Yes |
| **KodokuStudio** | `kodokustudio.com` | `/manhua/{slug}/capitulo-{n}/` | Yes | — |
| **LGBTics** | `lgbtics.com` | `/comic/{slug}/` | — | Yes |
| **LGBTics** | `lgbtics.com` | `/comic/{slug}/{chapter}/` | Yes | — |
| **MangaDex** | `mangadex.org` | `/title/{manga-uuid} or /manga/{manga-uuid}` | — | Yes |
| **MangaDex** | `mangadex.org` | `/chapter/{chapter-uuid}` | Yes | — |
| **ManhuaTo** | `manhuato.com` | `/manhua/{slug}/` | — | Yes |
| **ManhuaTo** | `manhuato.com` | `/manhua/{slug}/chapter-{n}-ch{id}` | Yes | — |
| **ManhwaTop** | `manhwatop.com` | `/manga/{slug}/` | — | Yes |
| **ManhwaTop** | `manhwatop.com` | `/manga/{slug}/chapter-{n}/` | Yes | — |
| **Manhwaz** | `manhwaz.com` | `/webtoon/{slug}` | — | Yes |
| **Manhwaz** | `manhwaz.com` | `/webtoon/{slug}/chapter-{n}` | Yes | — |
| **Nyx Scans** | `nyxscans.com` | `/series/{slug}/` | — | Yes |
| **Nyx Scans** | `nyxscans.com` | `/series/{slug}/chapter-{n}` | Yes | — |
| **Pawchive** | `pawchive.pw` | `/{service}/user/{id}/post/{id}/` | Yes | — |
| **QiScans** | `qimanga.com` | `/series/{slug}` | — | Yes |
| **QiScans** | `qimanga.com` | `/series/{slug}/chapter-{n}` | Yes | — |
| **StoneScape** | `stonescape.xyz` | `/series/{slug}` | — | Yes |
| **StoneScape** | `stonescape.xyz` | `/series/{slug}/ch-{n}` | Yes | — |
| **Tapas** | `tapas.io` | `/series/{slug}` | — | Yes |
| **Tapas** | `tapas.io` | `/episode/{id}` | Yes | — |
| **Toonily** | `toonily.com` | `/serie/{slug}/` | — | Yes |
| **Toonily** | `toonily.com` | `/serie/{slug}/chapter-{n}/` | Yes | — |
| **ToonVerse** | `toonverse.net` | `/series/{slug}/` | — | Yes |
| **ToonVerse** | `toonverse.net` | `/read/{slug}/{n}` | Yes | — |
| **ValirScans** | `valirscans.org` | `/series/comic/{slug}/` | — | Yes |
| **ValirScans** | `valirscans.org` | `/series/comic/{slug}/chapter/{n}` | Yes | — |
| **Vortex Scans** | `vortexscans.org` | `/series/{slug}/` | — | Yes |
| **Vortex Scans** | `vortexscans.org` | `/series/{slug}/chapter-{n}` | Yes | — |
| **WEBTOON** | `webtoons.com` | `/{lang}/{category}/{title}/list?title_no={id}` | — | Yes |
| **WEBTOON** | `webtoons.com` | `/{lang}/{category}/{title}/ep-{n}/viewer?title_no={id}&episode_no={n}` | Yes | — |
| **WeebCentral** | `weebcentral.com` | `/series/{id}/{slug}` | — | Yes |
| **WeebCentral** | `weebcentral.com` | `/chapters/{id}` | Yes | — |

## Per-site features

| Feature | Asura Scans | DivaScans | E-Hentai | Thunderscans | FlameComics | FSIComics | GEDE Comix | GenzToons | HD Porn Comics | HiveToons | IMHentai | Kagane | Kingofshojo | KodokuStudio | LGBTics | MangaDex | ManhuaTo | ManhwaTop | Manhwaz | Nyx Scans | Pawchive | QiScans | StoneScape | Tapas | Toonily | ToonVerse | ValirScans | Vortex Scans | WEBTOON | WeebCentral |
| :------ | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- | :------- |
| Individual posts/chapters | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Series chapter listing | Yes | Yes | — | Yes | Yes | Yes | Yes | Yes | — | Yes | — | Yes | — | Yes | Yes | Yes | Yes | Yes | Yes | Yes | — | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Image dedup (SHA-256) | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Download resume | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Concurrent downloads | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Chapter title from tags | — | — | Yes | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | Yes | — |

## Site notes

- **Pawchive** — Archives content from Patreon, SubscribeStar, Gumroad, Fantia,
  and DLSite. Posts marked "Previews only" download low-resolution thumbnails
  with a warning.

- **E-Hentai** — Requires both the gallery ID and token from the URL. Tags
  determine the series title. Relative `/s/...` links are resolved to absolute
  URLs. The `/s/` image-page fetches run at an internal 2 req/s rate matching
  the default.

- **WEBTOON** — Series URLs (`/list?title_no=...`) scrape all chapters. Chapter
  URLs need both `title_no` and `episode_no` query parameters.

- **FlameComics** — Chapter token is a hex string. Images are embedded in the
  HTML.

- **FSIComics** — Any non-reserved path is a chapter. Chapter URLs with a
  `-chapter-` marker in the slug are grouped under a
  `Series Title – Artist` folder.

- **GEDE Comix** — A Madara/WordPress site. Series pages list chapters under
  `.listing-chapters_wrap`. Chapter pages expose pages directly (no AJAX paging).

- **Asura Scans** — An Astro site. Series pages fully server-render the chapter
  list, so every chapter is captured in one request (no pagination). Chapter
  images come from ``cdn.asurascans.com`` (any ``*.asurascans.com`` host is
  accepted). Pages marked `Premium` are locked/paid and raise an error.

- **Kagane** — Requires a `cf_clearance` cookie obtained via the webview solver
  (`--solver auto`). Chapter images are unlocked through the site's
  DRM API with an integrity token.

- **MangaDex** — The largest manga aggregator. Uses the official public REST API
  (`api.mangadex.org`). Pages served from per-chapter CDN nodes. Chapters whose
  URL points at an external site are skipped from series listings.

- **Toonily** — A Madara/WordPress manhwa site. Page images live on
  `data.tnlycdn.com` which enforces a `Referer` check satisfied by comic-dl's
  per-download headers.

- **Manhwaz** — A Madara-style manhwa site. Chapter pages expose pages inside
  `.reading-content`. Images served from `cdn.manhwaz.com`.

- **KodokuStudio** — A Madara/WordPress site using Portuguese `capitulo-{n}`
  chapter URLs. Series pages render only the first/last chapter links, so the
  chapter list is loaded from the theme's `ajax/chapters` endpoint. Chapter
  images are served from WordPress's `i*.wp.com` CDN proxy. Series pages carry
  no cover or blurb, so those metadata fields stay empty rather than falling
  back to the site logo.

- **WeebCentral** — Server-rendered pages with HTMX image fragments. Chapter
  pages list no images inline; pages come from the chapter's `/images`
  endpoint (`reading_style=long_strip`). Chapter labels read
  `"<Type> <Number>"` (e.g. `Navigation 67.5`). Images are hotlink-protected
  and need the chapter page as `Referer`, supplied by comic-dl's per-download
  headers.

- **LGBTics** — A Madara/WordPress site. Series pages list chapters in
  `.version-chap .wp-manga-chapter`. Chapter images are lazy-loaded via
  `data-src` on the same domain. No AJAX pagination for chapters; all links
  render server-side. Images use WebP format.

- **Kingofshojo** — A Madara-style shoujo/romance site. Series pages load
  chapter lists dynamically via JavaScript and cannot be scraped for series
  metadata or chapter listings. Chapter pages work normally with plain
  `<img src>` inside `#readerarea`, served from `cdn.kingofshojo.com` and
  WordPress CDN (`i*.wp.com`). Chapter-only support.

- **ManhwaTop** — A large Madara/WordPress manhwa/manhua site (Solo Leveling,
  Nano Machine, Martial Peak). Standard Madara URL grammar. Chapter pages use
  lazy-loaded `data-src` on `c*.manhwatop.com` subdomains. The series page
  sits behind a Cloudflare challenge; the solver (`--solver auto`) or a
  `cf_clearance` cookie may be required for series scraping.

- **HiveToons** — An Astro manhwa site. Series pages server-render the full
  chapter list. Chapter titles come from the page's `Article` headline and
  page images live in `.comic-images-wrapper` on `storage.hivetoon.com`.
  The host serves slowly and drops parallel connections; interrupted runs
  resume where they left off, so rerun to complete them.

- **GenzToons** — A custom scanlation platform. Series pages statically render
  the chapter list in `#chapters` and the reader ships placeholder `img` tags
  whose `uid` attribute indexes real files on `cdn.meowing.org`. The mirror
  host `genztoons.net` is accepted. Plain HTTP passes Cloudflare, so no
  webview solver is needed.

- **QiScans** — An Angular SSR manga site. Series pages server-render the
  newest 30 chapters (`Showing 30 of N`); series scrapes cover that published
  subset, and older chapters remain reachable by direct URL. Reader images
  come from the `media.qimanhwa.com` host.

- **StoneScape** — A Vue SPA with no server-side HTML; all data comes from a
  plain JSON API (`/api/series/by-slug/{slug}`, `/…/chapters`,
  `/api/chapters/{id}/pages`). Public pages are served under `/pub/` on the
  same origin. Chapters marked locked (coins/subscription) or images served in
  the `protected` delivery mode raise an error.

- **Thunderscans** — A WordPress "mangareader"-theme site. Series pages
  statically render the full chapter list in `#chapterlist`. Chapter pages'
  `#readerarea` is JS-filled; page images are read from the page's
  `ts_reader.run({...})` JSON blob. Locked chapters have no blob and raise an
  error.

## Adding more sites

Sites can be added without touching this repository. See
[Writing a Plugin](../usage/write-plugin.md).

## Trademarks

Site and platform names referenced by this project are trademarks or trade
names of their respective owners. comic-dl is an independent project and is
not affiliated with, endorsed by, or sponsored by any of the sites it supports.
