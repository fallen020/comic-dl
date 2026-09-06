# Metadata

comic-dl writes `ComicInfo.xml` in two places:

- **Inside each chapter archive** — chapter-level facts (series name, chapter
  title, number, volume, page count, source URL) plus the shared series
  metadata (summary, creators, genres, publisher, status, reading direction).
- **Beside the archives** (`<Series Title>/ComicInfo.xml`) — the same shared
  series metadata, written from the most recently scraped chapter.

Writing the series facts into every archive keeps a single-archive copy or an
archive re-upload consistent with the folder-level file. Comic readers that
support ComicInfo.xml read the folder-level file when present and fall back to
the first archive's copy.

`ComicInfo.xml` is the standard metadata format used by
Calibre, Komga, Kavita, and other comic readers.

## Field reference

| ComicInfo field | Location | Source |
| :-------------- | :------- | :----- |
| `<Series>` | both | Series title from site metadata |
| `<Title>` | both | Chapter title in archives; series name in folder-level file. Placeholder chapter titles (e.g. `Chapter 5`) fall back to the series title |
| `<Number>` | archive | Chapter number, or first number found in the title |
| `<Volume>` | archive | Volume number (when the source provides one) |
| `<PageCount>` | archive | Number of pages archived |
| `<Pages>` | archive | Ordered page list; the first page is typed `FrontCover` |
| `<Year>` | both | Publication year (when a site exposes it) |
| `<Summary>` | both | Chapter/series description |
| `<Web>` | both | Normalized source URL |
| `<Genre>` | both | Genres, comma-joined |
| `<Writer>` | both | Writers/authors, comma-joined |
| `<Artist>` | both | Artists/credits, comma-joined |
| `<Colorist>` | both | Colorists/inkers, comma-joined (when a site exposes them) |
| `<Publisher>` | both | Publisher (when the source provides one) |
| `<Status>` | both | Series status, normalized — see below |
| `<ty:PublishingStatusTachiyomi>` | both | Tachiyomi-compatible status extension, mirrors `<Status>` |
| `<LanguageISO>` | both | Language code from the source page |
| `<Manga>` | both | Reading direction (see below) |
| `<CommunityRating>` | both | Rating on a 0–10 scale |

!!! note
    The ComicInfo schema forbids repeating elements. comic-dl writes exactly
    one `<Writer>` and one `<Artist>` element, with multiple values
    comma-separated.

## Reading direction

| Direction | ComicInfo value |
| :-------- | :-------------- |
| Right-to-left | `YesAndRightToLeft` |
| Left-to-right | `No` |
| Unknown | Element omitted |

Japanese manga and doujinshi read right-to-left. Korean manhwa, Chinese
manhua, WEBTOON, and western comics read left-to-right. When a site does not
expose a direction, the element is omitted.

## Comic status

Sites phrase a series' release state differently, so comic-dl normalizes it to
a fixed vocabulary before writing it:

| Raw status example | Normalized value |
| :----------------- | :--------------- |
| `ongoing`, `in progress`, `currently publishing` | `Ongoing` |
| `completed`, `complete`, `finished`, `ended` | `Completed` |
| `hiatus`, `on-hiatus`, `paused` | `On hiatus` |
| `cancelled`, `canceled` | `Cancelled` |
| `licensed` | `Licensed` |
| `publishing finished` | `Publishing finished` |

Unrecognized values are omitted entirely: neither `<Status>` nor the extension
element is written, so readers never see a bogus "Unknown" state.

The normalized value is written twice: the standard `<Status>` element and the
`ty:PublishingStatusTachiyomi` extension that Tachiyomi-compatible readers
recognize (its namespace, `http://www.w3.org/2001/XMLSchema`, is the Tachiyomi
`core-metadata` convention). Tachiyomi-compatible readers ignore the plain
`<Status>` element; readers such as Kavita, Komga, and ComicTagger can use
either.

## Per-site mapping

| Site | Reading direction | Language | Writers | Artists | Genres | Publisher | Rating |
| :--- | :---------------- | :------- | :------ | :------ | :----- | :-------- | :----- |
| WEBTOON | `ltr` | URL locale | `prop:...:author` | `prop:...:artist` | Category class | — | — |
| e-hentai | `rtl` for manga/doujinshi | `language:` tag | Tags/brackets | `artist:` tags | Tag list + category | — | 0–5 × 2 |
| Pawchive | *omitted* | `<html lang>` | — | `author` meta | — | — | — |
| FlameComics | `rtl` when type is manga | JSON `language` | JSON authors | JSON artists | JSON genres | — | — |
| FSIComics | `ltr` | Page language | — | JSON-LD credits | Page genre | JSON-LD publisher | — |
| GEDE Comix | `ltr` | `<html lang>` | — | `Artist(s)` | `Genre(s)` + `Tag(s)` | — | — |
| Asura Scans | `ltr` | `<html lang>` | JSON-LD author | JSON-LD illustrator | JSON-LD genre | — | 0–10 |
| Kagane | varies | Site metadata | Staff field | Staff field | Genre list | — | Rating field |
| MangaDex | varies | API language | Authors | Artists | Tag list | — | — |
| Toonily | varies | `<html lang>` | — | Artists | Genres | Publisher | — |
| Manhwaz | varies | `<html lang>` | — | Authors | Genres | — | — |
| KodokuStudio | `ltr` | `<html lang>` | — | — | — | — | — |

## Series vs archive level

The series-level `ComicInfo.xml` is written from the most recently scraped
chapter's metadata (each chapter page repeats the series facts). If no chapter
was scraped, it is written from the series listing alone.
