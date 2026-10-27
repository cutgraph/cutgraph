import { describe, expect, it } from "vitest";
import { parseRoute, routeSearch, type Route } from "../src/v3/router";

describe("v3 router params", () => {
  it("defaults to Home", () => {
    expect(parseRoute("")).toEqual({ view: "home" });
    expect(parseRoute("?view=nonsense")).toEqual({ view: "home" });
  });

  it("opens legacy video links in Story", () => {
    expect(parseRoute("?project=talk-cam-a")).toEqual({ view: "story", video: "talk-cam-a" });
    expect(parseRoute("?view=project&ws=garden&project=x")).toEqual({ view: "story", video: "x" });
    expect(parseRoute("?project=a%2Fb")).toEqual({ view: "story", video: "a/b" });
    expect(parseRoute("?project=")).toEqual({ view: "home" });
  });

  it("reads view, workspace and video", () => {
    expect(parseRoute("?view=project&ws=garden")).toEqual({ view: "project", ws: "garden" });
    expect(parseRoute("?view=story&ws=garden&video=herb-care")).toEqual({ view: "story", ws: "garden", video: "herb-care" });
    expect(parseRoute("?view=library")).toEqual({ view: "library" });
    expect(parseRoute("?view=settings")).toEqual({ view: "settings" });
  });

  it("falls back to Home when a project view names no workspace", () => {
    expect(parseRoute("?view=project")).toEqual({ view: "home" });
    expect(parseRoute("?view=project&ws=")).toEqual({ view: "home" });
  });

  it("round-trips every route through the query string", () => {
    const routes: Route[] = [
      { view: "home" },
      { view: "project", ws: "Kitchen equipment" },
      { view: "story", ws: "garden", video: "a/b" },
      { view: "library" },
      { view: "settings" },
    ];
    for (const route of routes) expect(parseRoute(routeSearch(route))).toEqual(route);
    expect(routeSearch({ view: "home" })).toBe("");
    expect(routeSearch({ view: "project", ws: "garden" })).toBe("?view=project&ws=garden");
  });
});
