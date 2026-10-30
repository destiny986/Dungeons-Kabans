# Vendored runtime assets

No external CDN is contacted at runtime. No Node build is required.

## HTMX 2.0.11
Source: https://github.com/bigskysoftware/htmx/tree/v2.0.11/dist
Downloaded dist/htmx.min.js from the official tagged release source.
License: Zero-Clause BSD, see HTMX-LICENSE.txt.

## GridStack 14.0.0
Source: https://github.com/gridstack/gridstack.js/releases/tag/v14.0.0
Official distribution: https://registry.npmjs.org/gridstack/-/gridstack-14.0.0.tgz
Files extracted: package/dist/gridstack-all.js and package/dist/gridstack.min.css.
Local packaging adjustment: removed only the trailing `//# sourceMappingURL=gridstack-all.js.map` debug directive because the optional map is not vendored. Runtime JavaScript is unchanged. This avoids a missing-file failure during WhiteNoise manifest post-processing.
License: MIT, see GRIDSTACK-LICENSE.txt.

## SHA-256

d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717  htmx.min.js
6a580028bd63af2eaace1e8b46133a5e95806759cf6e758c66ed9c72454c2f2c  gridstack-all.js
74efc82159c5e8d28cb2cebd9f912be9d728bb07fa83896a6b62f4a843207126  gridstack.min.css
