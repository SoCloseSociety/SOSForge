/** The phone layout hangs off this hook: if it lies, the feed collapses. */
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PHONE, useIsPhone, useMediaQuery } from "../useMediaQuery";

type Listener = () => void;

function mockMatchMedia(matches: boolean) {
  const listeners = new Set<Listener>();
  const state = { matches };
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    media: query,
    get matches() {
      return state.matches;
    },
    addEventListener: (_: string, cb: Listener) => listeners.add(cb),
    removeEventListener: (_: string, cb: Listener) => listeners.delete(cb),
  })) as unknown as typeof window.matchMedia;
  return {
    set(next: boolean) {
      state.matches = next;
      listeners.forEach((cb) => cb());
    },
    listenerCount: () => listeners.size,
  };
}

afterEach(() => {
  // @ts-expect-error -- restoring the jsdom default (absent)
  delete window.matchMedia;
});

describe("useMediaQuery", () => {
  it("knows on the FIRST render whether this is a phone", () => {
    mockMatchMedia(true);
    const { result } = renderHook(() => useIsPhone());
    // not "false then true one frame later": a layout that corrects itself
    // after mounting makes the feed jump under the reader's thumb
    expect(result.current).toBe(true);
  });

  it("follows a rotation", () => {
    const mql = mockMatchMedia(false);
    const { result } = renderHook(() => useIsPhone());
    expect(result.current).toBe(false);

    act(() => mql.set(true));

    expect(result.current).toBe(true);
  });

  it("unsubscribes on unmount", () => {
    const mql = mockMatchMedia(true);
    const { unmount } = renderHook(() => useMediaQuery(PHONE));
    expect(mql.listenerCount()).toBe(1);
    unmount();
    expect(mql.listenerCount()).toBe(0);
  });

  it("renders instead of crashing where matchMedia does not exist", () => {
    // jsdom, old browsers, some in-app webviews
    const { result } = renderHook(() => useIsPhone());
    expect(result.current).toBe(false);
  });

  it("does not loop under React 19", () => {
    // Lesson 1, in its useSyncExternalStore form: a snapshot that returns a
    // fresh value every call never converges and the component never mounts.
    mockMatchMedia(true);
    let renders = 0;
    renderHook(() => {
      renders++;
      return useIsPhone();
    });
    expect(renders).toBeLessThan(5);
  });
});
