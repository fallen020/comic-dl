# Supported Sites

The 12 built-in scrapers shipped with comic-dl. The live registry — including
any third-party plugins — is shown by `comic-dl --list-sources`.

## Sites

| Site | Domain | URL pattern | Chapters | Series |
| :--- | :----- | :---------- | :------- | :----- |
| **Pawchive** | `pawchive.pw` | `/{service}/user/{id}/post/{id}/` | Yes | — |
| **E-Hentai** | `e-hentai.org` | `/g/{gid}/{token}/` | Yes | — |
| **WEBTOON** | `www.webtoons.com` | `/{lang}/{category}/{title}/list?title_no={id}` | — | Yes |
| **WEBTOON** | `www.webtoons.com` | `/{lang}/{category}/{title}/ep-{n}/viewer?title_no={id}&episode_no={n}` | Yes | — |
| **FlameComics** | `flamecomics.xyz` | `/series/{id}/` | — | Yes |
| **FlameComics** | `flamecomics.xyz` | `/series/{id}/{token}/` | Yes | — |
| **FSIComics** | `fsicomics.com` | `/{comic-slug}/` | Yes | — |
| **FSIComics** | `fsicomics.com` | `/all-porn-comics/...` | — | Yes |
| **GEDE Comix** | `gedecomix.com` | `/porncomic/{series}/{chapter}/` | Yes | — |
| **GEDE Comix** | `gedecomix.com` | `/porncomic/{series}/` | — | Yes |
| **Asura Scans** | `asurascans.com` | `/comics/{series}/chapter/{n}` | Yes | — |
| **Asura Scans** | `asurascans.com` | `/comics/{series}/` | — | Yes |
| **Kagane** | `kagane.to` | `/series/{id}/reader/{book}` | Yes | — |
| **Kagane** | `kagane.to` | `/series/{id}/` | — | Yes |
| **MangaDex** | `mangadex.org` | `/chapter/{chapter-uuid}` | Yes | — |
| **MangaDex** | `mangadex.org` | `/title/{manga-uuid}` or `/manga/{manga-uuid}` | — | Yes |
| **Toonily** | `toonily.com` | `/serie/{slug}/chapter-{n}/` | Yes | — |
| **Toonily** | `toonily.com` | `/serie/{slug}/` | — | Yes |
| **Manhwaz** | `manhwaz.com` | `/webtoon/{slug}/chapter-{n}` | Yes | — |
| **Manhwaz** | `manhwaz.com` | `/webtoon/{slug}` | — | Yes |
| **KodokuStudio** | `kodokustudio.com` | `/manhua/{slug}/capitulo-{n}/` | Yes | — |
| **KodokuStudio** | `kodokustudio.com` | `/manhua/{slug}/` | — | Yes |

## Per-site features

| Feature | Pawchive | E-Hentai | WEBTOON | FlameComics | FSIComics | GEDE Comix | Asura Scans | Kagane | MangaDex | Toonily | Manhwaz | KodokuStudio |
| :------ | :------- | :------- | :------ | :---------- | :-------- | :--------- | :---------- | :----- | :------- | :------ | :------ | :---------- |
| Individual posts/chapters | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Series chapter listing | — | — | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Image dedup (SHA-256) | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Download resume | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Concurrent downloads | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Chapter title from tags | — | Yes | Yes | — | — | — | — | — | — | — | — | — |

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
  list. Chapter images come from `cdn.asurascans.com`. Pages marked `Premium`
  are locked/paid and raise an error.

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

## Adding more sites

Sites can be added without touching this repository. See
[Writing a Plugin](../usage/write-plugin.md).

## Trademarks

Site and platform names referenced by this project are trademarks or trade
names of their respective owners. comic-dl is an independent project and is
not affiliated with, endorsed by, or sponsored by any of the sites it supports.
