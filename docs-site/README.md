# Bonsai Docs Site

This is the Docusaurus site for Bonsai documentation.

## Development

```bash
npm install
npm run generate:cli
npm start
```

`generate:cli` writes `docs/commands.md` from the Typer app in `src/bonsai/cli.py`.

## Build

```bash
npm run build
```

`npm run build` runs `generate:cli` first, then builds the static site. The
production build is written to `build/`.

## Deploy

GitHub Pages deployment is handled by the repository's `Docs` workflow.
