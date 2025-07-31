# UnderwaterViz

This repository periodically captures still frames from the **Scripps Pier**
underwater camera via a GitHub Actions workflow. It stores the images
inside a `snapshots/` directory and publishes a simple GitHub Pages site
to explore them.

## Snapshot layout

Starting from 2025‑07‑31 snapshots are stored in a nested
year/month/day hierarchy:

```
snapshots/
└─ 2025/
   └─ 07/
      └─ 31/
         ├─ 00.png
         ├─ 01.png
         └─ …
```

Prior snapshots used a flat `YYYY‑MM‑DD` directory (e.g. `snapshots/2025-07-30/13.png`).
If you have existing data in the old layout you can migrate it by running

```bash
./port_snapshots.sh
```

The script will move all files to the new hierarchy and remove the old
directories. It is safe to run multiple times – directories that already
conform to the new layout are skipped.

## Capturing snapshots

A GitHub Actions workflow (`.github/workflows/snapshot.yml`) runs at the top
of every hour. It calls `grab_snapshot.sh`, which uses `ffmpeg` to grab a
single frame from the HDOnTap stream and writes it into the appropriate
directory based on the current UTC time. To use a different timezone,
export the `TZ` environment variable before running the script.

## Viewing snapshots

The contents of the `docs/` directory are deployed to GitHub Pages via
`.github/workflows/pages.yml`. The site provides a simple explorer that
lets you drill down by year, month and day and view the images in a
responsive grid. You can enable GitHub Pages in the repository settings
and point it at the `gh-pages` branch. Once enabled the site will be
available at `https://<username>.github.io/<repository>/`.

## Development notes

* Ensure `ffmpeg` is installed on the runner or your local system.
* The GitHub API is used client‑side to list directories; unauthenticated
requests are rate limited. If you exceed the limit you may need to
authenticate or wait for the limit to reset.
