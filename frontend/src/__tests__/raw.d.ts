/** Local declaration for Vite's `?raw` imports (used by the i18n parity
 * test). The project does not include `vite/client` in its tsconfig and the
 * tests must not modify the build config. */
declare module '*?raw' {
  const content: string
  export default content
}
