# Self-hosted web fonts

Vendored so the site makes **zero third-party requests** (no calls to
`fonts.googleapis.com` / `fonts.gstatic.com`). Each font keeps its own
licence. The full SIL Open Font License text and each family's copyright
notice are kept in the `*-OFL.txt` files beside this one, as the licence
requires.

| Family | Weights | Subsets | License |
|---|---|---|---|
| Inter | 400, 500, 600, 700, 800 | latin, latin-ext | [SIL Open Font License 1.1](https://openfontlicense.org) |
| JetBrains Mono | 400, 500 | latin, latin-ext | [SIL Open Font License 1.1](https://openfontlicense.org) |

Source: Google Fonts. To refresh, re-download the `woff2` files for the same
families and weights and keep `fonts.css` in sync.
