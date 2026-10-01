// Rollup build for the almanac frontend — DESIGN.md §17.
//
// D68 picks Rollup over Vite because the output is a small, fixed set of ESM
// files at predictable URLs, and those URLs are string constants in
// `custom_components/almanac/const.py`. Anything that chose its own filenames
// would make the Python side guess.
//
// Two configurations, not one with two entries. A single build would let Rollup
// hoist code shared by the panel and the card into a common chunk, and the card
// entry would then import that chunk on every frontend page — which is the one
// thing D70 forbids. Duplicating the shared code across the two bundles is the
// price of the stub staying a stub, and it is the right way round: the panel is
// fetched when a user opens it, the card bundle is fetched always.
//
// D129 (§17.1): chunk filenames carry a content hash, because the entry URLs'
// `?v=&m=` query cannot cache-bust a file it does not name.

import { fileURLToPath } from "node:url";

import nodeResolve from "@rollup/plugin-node-resolve";
import terser from "@rollup/plugin-terser";
import typescript from "@rollup/plugin-typescript";

const SRC = fileURLToPath(
  new URL("custom_components/almanac/frontend/src/", import.meta.url),
);
const DIST = fileURLToPath(
  new URL("custom_components/almanac/frontend/dist/", import.meta.url),
);

const plugins = () => [
  nodeResolve(),
  typescript({
    tsconfig: "./tsconfig.json",
    noEmitOnError: true,
    compilerOptions: { noEmit: false, declaration: false, sourceMap: false },
  }),
  terser({ format: { comments: false } }),
];

/** One bundle: one entry file, its own chunks, no sharing with the other. */
const bundle = (entry, name) => ({
  input: `${SRC}${entry}`,
  output: {
    dir: DIST,
    format: "es",
    entryFileNames: `${name}.js`,
    chunkFileNames: "chunks/[name]-[hash].js",
    sourcemap: false,
  },
  plugins: plugins(),
  // Rollup warns on `this` in the Lit output and on circular imports inside
  // lit-html, neither of which is actionable here. Everything else is an error
  // worth seeing, so the filter names the two rather than silencing warnings.
  onwarn(warning, warn) {
    if (warning.code === "THIS_IS_UNDEFINED") return;
    if (warning.code === "CIRCULAR_DEPENDENCY") return;
    warn(warning);
  },
});

export default [
  bundle("card.ts", "almanac-card"),
  bundle("panel.ts", "almanac-panel"),
];
