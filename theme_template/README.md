# ChitUI Theme Template

This folder is a ready-to-edit starting point for building your own ChitUI theme.

A theme is a **pure-CSS skin**: it is loaded after ChitUI's default stylesheet
and overrides whatever you want to change. No Python, no JavaScript, no risk of
breaking the app - if a theme looks wrong, just switch back to
**ChitUI Default Theme** in Settings → Appearance.

## Folder contents

| File | Purpose |
|---|---|
| `theme.json` | Manifest: id, name, version, author, description |
| `theme.css` | Your stylesheet. Fully documented - open it and read the header |
| `preview.png` | *(optional)* Screenshot shown in the theme picker (~800×500 px recommended) |
| `img/` | *(optional)* Images and fonts referenced from your CSS |

## Building a theme, step by step

1. **Rename the theme.** Edit `theme.json`:
   - `id`: lowercase letters, numbers, `-` and `_` only (e.g. `midnight_teal`).
     This becomes the folder name on the server. `default` is reserved.
   - `name`, `author`, `description`, `version`: shown in the theme picker.

2. **Write CSS.** Open `theme.css`. The header documents every ChitUI CSS
   variable and component class, and the file already contains a
   **light + dark mode scaffold** - ChitUI has a color-mode switcher, so
   define your palette in both `[data-bs-theme="light"]` and
   `[data-bs-theme="dark"]` blocks (they're pre-filled with commented
   suggestions). The quickest wins:
   - Override `--accent-color` / `--accent-hover` to re-brand the app.
   - Override Bootstrap variables (`--bs-body-bg`, `--bs-secondary-bg`,
     `--bs-border-color`, ...) inside `[data-bs-theme="dark"]` to change all
     backgrounds and surfaces at once.
   - Use your browser's DevTools (F12 → Inspect) on a running ChitUI to find
     the class of any element you want to restyle.

3. **Use your own images (optional).** Drop them anywhere in the theme folder
   and reference them with the stable URL that always points at the active
   theme. The `/themes/active/assets/` prefix maps to your theme's root folder:
   ```css
   /* file at   img/background.png   in your theme folder: */
   body { background-image: url("/themes/active/assets/img/background.png"); }
   ```

4. **Add a preview (optional).** Save a screenshot as `preview.png` in the
   theme root so users see it in the picker.

5. **Package it.** ZIP the theme so the ZIP contains either the files at its
   root, or one single folder with the files inside:
   ```
   my_theme.zip
   └── my_theme/
       ├── theme.json
       ├── theme.css
       ├── preview.png
       └── img/...
   ```
   On Linux/macOS: `zip -r my_theme.zip my_theme/`

6. **Install & apply.** In ChitUI: **Settings → Appearance → Upload Theme**,
   pick the ZIP, then press **Apply** on your theme's card. Uploading a ZIP
   with the same `id` again updates the installed theme - handy while iterating.

For the full guide - including **moving, resizing and re-arranging elements**
with pure CSS - see [THEMING.md](../THEMING.md) in the ChitUI repository.

## Iterating quickly

While designing, you can also edit the installed copy directly on the Pi at
`data/themes/<your_id>/theme.css` and just refresh the browser
(`Ctrl+Shift+R` for a hard refresh) - no re-upload needed.

## Rules & limits

- `theme.json` **must** contain a valid `id` and a `name`.
- The CSS file referenced by `css` (default `theme.css`) must exist.
- Max ZIP size: 20 MB (50 MB unpacked).
- Only static assets are served: css, png, jpg, jpeg, gif, webp, svg, ico,
  woff, woff2, ttf, otf. JavaScript files are ignored and never served.
- The theme also styles the **login** and **change-password** pages, since
  they load the active theme CSS too.
