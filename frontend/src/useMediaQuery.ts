import { useSyncExternalStore } from "react";

/** Reads a CSS media query from React, without ever tearing.
 *
 * `useSyncExternalStore` and not `useState` + `useEffect`: the first render
 * must already know whether this is a phone. A layout that mounts wide and
 * corrects itself one frame later makes the feed jump under the reader's
 * thumb, and on this product the reader is looking for an earthquake.
 *
 * The subscribe/getSnapshot pair is created outside the component for a
 * reason that has already cost this codebase a blank page (lesson 1): under
 * React 19, a snapshot function that returns a fresh value on every call
 * sends `useSyncExternalStore` into an infinite loop. Booleans are compared
 * by value, so this one is safe -- but the cache keeps the subscribe
 * identity stable too, which is the other half of the same trap.
 */
const cache = new Map<
  string,
  { subscribe: (cb: () => void) => () => void; get: () => boolean }
>();

function entry(query: string) {
  const hit = cache.get(query);
  if (hit) return hit;
  const made = {
    subscribe: (onChange: () => void) => {
      // matchMedia is absent in jsdom and in very old browsers: the product
      // must render, not crash, so we fall back to "not a phone".
      const mql = window.matchMedia?.(query);
      if (!mql) return () => {};
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    get: () => window.matchMedia?.(query)?.matches ?? false,
  };
  cache.set(query, made);
  return made;
}

export function useMediaQuery(query: string): boolean {
  const { subscribe, get } = entry(query);
  // the server snapshot is the third argument: without it, any future SSR or
  // prerender of this page throws instead of assuming a wide screen
  return useSyncExternalStore(subscribe, get, () => false);
}

/** The single breakpoint of the product, and it must stay identical to the one
 * in styles.css -- the two are checked against each other by
 * `tools/responsive.py`, because when they disagreed the result was a page
 * where the CSS had switched to the phone layout while the components still
 * believed they were on a desktop: in landscape the filter panel stayed
 * expanded at 242 px and pushed every event below the fold.
 *
 * Narrow OR short. A phone held sideways is 844 px wide and 390 px tall: too
 * wide to be caught by a width rule, far too short for two columns. */
export const PHONE = "(max-width: 820px), (max-height: 560px)";

export function useIsPhone(): boolean {
  return useMediaQuery(PHONE);
}
