/** Declaration locale pour les imports `?raw` de Vite (utilises par le test de
 * parite i18n). Le projet n'inclut pas `vite/client` dans son tsconfig et les
 * tests ne doivent pas modifier la config du build. */
declare module '*?raw' {
  const content: string
  export default content
}
