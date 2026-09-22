# Publishing the Niko VS Code extension

Everything below is a manual step for a human — nothing here is automated,
and no credentials are stored anywhere in this repo.

## Prerequisites

1. A Microsoft account.
2. A publisher on the Visual Studio Marketplace:
   - Go to https://marketplace.visualstudio.com/manage and sign in.
   - Create a publisher with ID **`niko-lang`** (must match the
     `"publisher"` field in `package.json`, already set).
   - Or from a terminal: `npx vsce create-publisher niko-lang`
     (opens a browser sign-in; follow the prompts).
3. A Personal Access Token (PAT):
   - Go to https://dev.azure.com → User settings → Personal access tokens
     → New Token.
   - Organization: **all accessible organizations**.
   - Scope: **Marketplace → Manage**.
   - Copy the token now — it is shown once. Keep it somewhere safe
     (password manager); it is never committed to this repo.

## Publish

From `editors/vscode/` (a fresh `niko-<version>.vsix` is built first —
see the build notes in the extension README):

```sh
cd editors/vscode
npx vsce publish -p <your-PAT>
```

This packages the extension and uploads it to the Marketplace under the
`niko-lang` publisher. Alternatively, package without publishing and
upload by hand:

```sh
npx vsce package        # produces niko-<version>.vsix
```

then open https://marketplace.visualstudio.com/manage → `niko-lang` →
"New extension" → "Upload" and pick the `.vsix`.

## Verify the listing

1. Open https://marketplace.visualstudio.com/items?itemName=niko-lang.niko
   and confirm the version number, description, and README render.
2. In a clean VS Code (no local install): Extensions view → search
   "Niko" → Install → open a `.niko` file and confirm:
   - syntax highlighting applies,
   - the Niko language server starts (Output → "Niko Language Server",
     no spawn errors — needs Python 3 with `niko2` importable),
   - F5 "Debug current Niko file" launches and stops at a breakpoint.

## Notes

- The extension id is `niko-lang.niko`; keep `name: niko` and
  `publisher: niko-lang` in `package.json` stable across releases or the
  listing splits.
- The packaged `.vsix` is committed to `editors/vscode/` as
  `niko-<version>.vsix` so the release zips and git history stay in sync;
  the old versioned vsix is removed when a new one is built.
- Marketplace stats/reviews appear under the publisher dashboard a few
  minutes after publish.
