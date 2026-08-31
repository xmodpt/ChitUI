# ChitUI Theming Guide

A complete guide to creating, styling and **re-arranging** the ChitUI interface
with custom themes.

---

## Table of Contents

1. [How themes work](#1-how-themes-work)
2. [Anatomy of a theme](#2-anatomy-of-a-theme)
3. [The manifest: theme.json](#3-the-manifest-themejson)
4. [Styling: variables first](#4-styling-variables-first)
5. [Component class reference](#5-component-class-reference)
6. [Light & dark mode](#6-light--dark-mode)
7. [Using images and fonts](#7-using-images-and-fonts)
8. [Moving, resizing and re-arranging elements](#8-moving-resizing-and-re-arranging-elements)
9. [What themes can NOT do](#9-what-themes-can-not-do)
10. [Workflow: build, test, iterate](#10-workflow-build-test-iterate)
11. [Packaging & installing](#11-packaging--installing)

---

## 1. How themes work

A ChitUI theme is a **pure CSS overlay**. Every page loads stylesheets in this
order:

```
bootstrap.min.css          Bootstrap 5.3
chitui.css                 ChitUI's default look ("ChitUI Default Theme")
/themes/active.css         <- YOUR THEME (empty when default is active)
```

Because your CSS loads **last**, anything you write wins. You never copy or
replace the default stylesheet — you only override the parts you want to
change. Switching back to the default theme is always one click away, so a
broken theme can never lock you out.

Themes are installed to `data/themes/<id>/` (one folder per theme) and
managed in **Settings → Appearance**.

## 2. Anatomy of a theme

```
my_theme/
├── theme.json        required - manifest
├── theme.css         required - your stylesheet
├── preview.png       optional - screenshot for the theme picker (~800x500)
└── img/              optional - images / fonts used by theme.css
    └── background.png
```

Download a fully documented starter from
**Settings → Appearance → Starter Template**.

## 3. The manifest: theme.json

```json
{
  "id": "my_theme",
  "name": "My Theme",
  "version": "1.0.0",
  "author": "Your Name",
  "description": "Shown in the theme picker.",
  "css": "theme.css",
  "preview": "preview.png"
}
```

| Field | Required | Notes |
|---|---|---|
| `id` | yes | 2–50 chars, lowercase `a-z 0-9 - _`. Becomes the folder name. `default` is reserved. |
| `name` | yes | Display name |
| `version` | no | Shown on the card |
| `author` | no | Shown on the card |
| `description` | no | Shown on the card |
| `css` | no | Stylesheet filename, defaults to `theme.css` |
| `preview` | no | Preview image filename, defaults to `preview.png` if present |

## 4. Styling: variables first

Both Bootstrap and ChitUI's components read their colors from CSS variables,
so overriding variables restyles the whole app at once. Reach for variables
first; write component rules only for what variables can't express.

**ChitUI variables** (defined on `:root`):

| Variable | Controls |
|---|---|
| `--accent-color` / `--accent-hover` | Brand color: buttons, links, active states |
| `--success-color` | "Good" states: printing, online, gauges |
| `--warning-color` | Warnings |
| `--info-color` | Informational highlights |
| `--sidebar-width` | Width of the printers sidebar (default `280px`) |
| `--header-height` | Height of the top bar (default `60px`) |

**Bootstrap variables** (set them inside a `[data-bs-theme="..."]` block):

| Variable | Controls |
|---|---|
| `--bs-body-bg` | Page background |
| `--bs-body-color` | Default text |
| `--bs-secondary-bg` | Cards, inputs |
| `--bs-tertiary-bg` | Hovers, subtle surfaces |
| `--bs-border-color` | All borders and separators |
| `--bs-secondary-color` | Muted text |
| `--bs-emphasis-color` | High-contrast text |
| `--bs-link-color` | Links |

## 5. Component class reference

Use your browser DevTools (`F12` → inspect) to find anything not listed here.

| Area | Classes |
|---|---|
| Layout | `.app-header` `.app-logo` `.app-sidebar` `.app-content` `.sidebar-overlay` `.mobile-menu-toggle` |
| Sidebar | `.sidebar-section` `.sidebar-section-title` `.printer-card` (`.active`) `.printer-card-header` `.printer-name` `.printer-icon` `.printer-status-badge` `.status-online` `.status-offline` ... |
| Hero | `.printer-preview` `.printer-preview-left` `.printer-preview-right` `.printer-icon-placeholder` |
| Cards | `.dashboard-card` `.camera-card` `.card-title` `.print-info-card` `.print-info-item/-label/-value` |
| Print state | `.print-status-badge` `.print-status-printing/-idle/-error` `.print-thumbnail` `.print-camera-container` |
| Gauges | `.circular-gauge` `.gauge-bg` `.gauge-progress` (+ `.warning` `.danger`) `.gauge-percentage` `.gauge-label` |
| Files | `.file-manager-container` `.file-manager-toolbar` `#fileManagerTable` `.fileOption` `.file-expand-btn` `.file-details-row` `.upload-area` |
| Controls | `.btn-accent` `.btn-icon` `.custom-tabs` `.settings-nav` `.progress-enhanced` `.data-table` |

## 6. Light & dark mode

ChitUI has a color-mode switcher, so a user can be in either mode at any
time. Define your palette twice and set `color-scheme` so native controls
match:

```css
[data-bs-theme="light"] { color-scheme: light; --bs-body-bg: #f2f4f7; /* ... */ }
[data-bs-theme="dark"]  { color-scheme: dark;  --bs-body-bg: #16181d; /* ... */ }
```

Rules that only use variables (layout, radii, component styling) go outside
those blocks and work in both modes automatically.

## 7. Using images and fonts

Put files anywhere inside your theme folder. The URL prefix
`/themes/active/assets/` always maps to the **active** theme's root folder:

```css
/* file at img/bg.png inside your theme folder */
body { background-image: url("/themes/active/assets/img/bg.png"); }

/* custom font at fonts/Inter.woff2 */
@font-face {
  font-family: "Inter";
  src: url("/themes/active/assets/fonts/Inter.woff2") format("woff2");
}
body { font-family: "Inter", sans-serif; }
```

Served file types: `css png jpg jpeg gif webp svg ico woff woff2 ttf otf`.

## 8. Moving, resizing and re-arranging elements

**Yes — elements can be moved and re-arranged**, within the limits of CSS.
The main content area is a stack of sibling `.row` blocks and Bootstrap rows
are flexbox containers, which makes the layout very re-arrangeable. The key
techniques:

### 8.1 Resize the frame

`--sidebar-width` and `--header-height` are used consistently by the header,
sidebar *and* the content margins, so resizing is one line each:

```css
:root {
  --sidebar-width: 340px;   /* wider printer list  */
  --header-height: 48px;    /* slimmer top bar     */
}
```

### 8.2 Reorder whole page sections

The dashboard sections are direct children of `.app-content`, in this order:

```
1. hero row        (.printer-preview - printer image + name)
2. info row        (Printer Information + Camera Stream)
3. controls row    (#printControlsRow - only visible while printing)
4. files row       (Files card + upload)
```

Turn `.app-content` into a flex column and reorder with `order`:

```css
.app-content { display: flex; flex-direction: column; }

.app-content > .row:has(.printer-preview) { order: 3; }  /* hero to bottom  */
.app-content > #printControlsRow          { order: 1; }  /* controls first  */
```

*(Unlisted siblings keep `order: 0`, so they sort before anything you push
to 1+. Give every section an explicit order if you want full control.)*

### 8.3 Swap columns inside a row

Bootstrap `.row`s are flex containers, so columns accept `order` too. The
info row holds Printer Information (left, wide) and Camera (right, narrow).
To put the camera on the left:

```css
.row:has(.camera-card) > div:first-child { order: 2; }
```

`:has()` works in all modern browsers (Chrome/Edge 105+, Safari 15.4+,
Firefox 121+) and lets you target rows/columns that have no unique class of
their own.

### 8.4 Move fixed elements to another edge

The header and sidebar are `position: fixed`, so they can be pinned to any
edge. Full recipe — **sidebar on the right**:

```css
/* flip the panel itself */
.app-sidebar {
  left: auto;
  right: 0;
  border-right: none;
  border-left: 1px solid var(--bs-border-color);
  transform: translateX(100%);              /* hide towards the right */
}
.app-sidebar.show,
.app-sidebar.pinned { transform: translateX(0); }

/* content must now clear the RIGHT edge when pinned */
.app-content.sidebar-pinned {
  margin-left: 0;
  margin-right: var(--sidebar-width);
}
```

The same idea moves the header to the bottom (`top: auto; bottom: 0;` plus
swapping the content's `margin-top` for `margin-bottom`) — just remember the
sidebar's `top` is tied to `--header-height`, so set `.app-sidebar { top: 0 }`
if the header no longer sits above it.

### 8.5 Re-flow a container into a grid

Any list-like container can be re-flowed. Example — printer cards in two
columns (nice with a wider sidebar):

```css
:root { --sidebar-width: 420px; }

.sidebar-section.scrollable > div {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
}
.printer-card { margin-bottom: 0; }
```

### 8.6 Hide what you don't use

```css
/* no camera? hide the card and let the info card take the full row */
.row:has(.camera-card) > div:last-child  { display: none; }
.row:has(.camera-card) > div:first-child { width: 100%; }

/* hide the big hero printer image, keep the name */
.printer-preview-left { display: none; }
```

### 8.7 Ground rules for re-arranging

- `order` only reorders **siblings inside the same flex/grid container** —
  you cannot move the camera card into the sidebar, for example.
- Keyboard/tab order and screen-reader order follow the HTML, not your CSS
  order. Fine for personal themes; keep it in mind for shared ones.
- Re-check mobile. ChitUI's breakpoints are 576 / 768 / 992 px; wrap
  desktop-only re-arrangements in `@media (min-width: 992px) { ... }` so
  phones keep the stock stacked layout.
- `#printControlsRow` appears only during a print — test your layout with a
  printer both idle and printing (the fake printer emulator is perfect for
  this).

## 9. What themes can NOT do

- Add, remove or rename HTML elements, text or icons (CSS `content` tricks
  aside).
- Change behavior — themes cannot ship JavaScript. `.js` files inside a
  theme are ignored and never served.
- Move an element into a different parent container (see 8.7).
- Restyle native browser chrome beyond what `color-scheme` / scrollbar
  pseudo-elements allow.

If you need new elements or behavior, that's a **plugin**, not a theme —
and a plugin and a theme can be designed to work together.

## 10. Workflow: build, test, iterate

1. Download the starter (**Settings → Appearance → Starter Template**).
2. Edit `theme.json` (pick a unique `id`) and `theme.css`.
3. Zip, upload, apply.
4. From then on, edit the installed copy directly on the Pi at
   `data/themes/<id>/theme.css` and hard-refresh the browser
   (`Ctrl+Shift+R`) — no re-upload needed while iterating.
5. Use DevTools constantly: inspect an element, prototype the rule in the
   Styles panel, then paste it into `theme.css`.
6. Test: light **and** dark mode, mobile width, sidebar pinned/unpinned,
   a printer idle **and** printing.
7. When you're happy, take a screenshot as `preview.png` and re-zip for
   distribution.

## 11. Packaging & installing

```
my_theme.zip
└── my_theme/          (or files directly at the zip root)
    ├── theme.json
    ├── theme.css
    ├── preview.png
    └── img/...
```

- Install via **Settings → Appearance → Upload** (max 20 MB zip / 50 MB
  unpacked).
- Re-uploading a zip with the same `id` **updates** the installed theme.
- Deleting the active theme automatically reverts to ChitUI Default.
- The theme also skins the login and change-password pages.
