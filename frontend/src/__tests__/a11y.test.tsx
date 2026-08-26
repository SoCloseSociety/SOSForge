/** Accessibility properties that a real person depends on.
 *
 * This is an emergency product: it gets read one-handed, in a hurry, in
 * sunlight, sometimes by a screen reader. Each test here guards a failure that
 * was measured in the running app, not a checklist item.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Feed } from "../components/Feed";
import { makeEvent, NOW, resetStore } from "./helpers";
import { translate } from "../i18n";
import { LANGS } from "../i18n";

describe("the feed list is not a live region", () => {
  it("does not wrap the rows in aria-live", () => {
    /* Every row's age re-renders on the one-second tick. A live region around
     * them kept the polite queue permanently full of "14 s ago... 15 s ago",
     * and the new-event announcements it existed for never got through. */
    resetStore();
    const { container } = render(
      <Feed events={[makeEvent()]} now={NOW} emptyKey="filters.empty" />,
    );
    const list = container.querySelector(".feed");
    expect(list?.getAttribute("aria-live")).toBeNull();
    expect(list?.getAttribute("role")).not.toBe("feed");
  });

  it("announces an arrival exactly once, through a region of its own", async () => {
    resetStore();
    render(
      <Feed
        events={[makeEvent({ place: "Off Honshu" })]}
        now={NOW}
        emptyKey="filters.empty"
      />,
    );
    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("Off Honshu");
  });

  it("marks the open row as pressed, not selected", () => {
    /* aria-selected is only valid on option/tab/row/gridcell: on a button
     * every screen reader ignores it, so opening an event announced nothing. */
    resetStore();
    const { container } = render(
      <Feed events={[makeEvent()]} now={NOW} emptyKey="filters.empty" />,
    );
    const row = container.querySelector(".item");
    expect(row?.hasAttribute("aria-pressed")).toBe(true);
    expect(row?.hasAttribute("aria-selected")).toBe(false);
  });
});

describe("every language is complete", () => {
  it("has no key that falls back to English", () => {
    /* A missing key silently renders the English string, which looks like a
     * translation choice rather than a hole. */
    const englishKeys = Object.keys(
      (translate as unknown as { DICTS?: never }) && dictOf("en"),
    );
    for (const { code } of LANGS) {
      const missing = englishKeys.filter((key) => !(key in dictOf(code)));
      expect({ lang: code, missing }).toEqual({ lang: code, missing: [] });
    }
  });
});

/** Reads a dictionary through the public API: `translate` returns the key
 * itself when it is unknown, which is exactly the signal we want. */
function dictOf(lang: string): Record<string, string> {
  const probe = KEYS.reduce<Record<string, string>>((acc, key) => {
    const value = translate(lang as never, key);
    if (value !== key) acc[key] = value;
    return acc;
  }, {});
  return probe;
}

/** Every key the app actually asks for. Kept explicit so a new key that
 * nobody translated shows up here as a failure rather than as English text. */
const KEYS = [
  "app.title",
  "app.live",
  "app.reconnecting",
  "lang.picker",
  "filters.empty",
  "filters.empty.search",
  "filters.empty.window",
  "filters.empty.kinds",
  "filters.empty.magnitude",
  "filters.open",
  "filters.close",
  "footer.sources",
  "footer.source.up",
  "footer.source.down",
  "footer.clients",
  "a11y.newevent",
  "sev.info",
  "sev.minor",
  "sev.moderate",
  "sev.severe",
  "sev.extreme",
  "kind.earthquake",
  "kind.tsunami",
  "kind.volcano",
];

describe("feed text is data, never markup", () => {
  /* `instruction` and `description` come from nineteen public feeds we do not
   * control, and they reach the browser. Nothing strips `<` on the way in --
   * real alerts say "temperatures < 32F" -- so the ONLY thing standing between
   * a hostile feed and script execution is that React renders them as text
   * nodes. This test fails the day someone reaches for dangerouslySetInnerHTML.
   */
  it("renders an instruction containing markup inert", async () => {
    const { LivePanel } = await import("../components/LivePanel");
    resetStore();
    const hostile = '<img src=x onerror="alert(1)"> Move to higher ground now';
    const { container } = render(
      <LivePanel event={makeEvent({ instruction: hostile })} now={NOW} />,
    );

    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("Move to higher ground now");
    expect(container.textContent).toContain("<img");
  });

  it("keeps a less-than sign that a real alert actually uses", () => {
    resetStore();
    const { container } = render(
      <Feed
        events={[makeEvent({ description: "temperatures < 32F expected" })]}
        now={NOW}
        emptyKey="filters.empty"
      />,
    );
    expect(container.textContent).not.toContain("&lt;");
  });
});
