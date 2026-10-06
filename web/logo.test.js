import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";

describe("static production Prometheus logo", () => {
  beforeAll(async () => { await import("./logo.js"); });
  beforeEach(() => { document.body.innerHTML = ""; });

  it("retains the normal triangle and dot without an eye", () => {
    const logo = document.createElement("prometheus-logo");
    document.body.appendChild(logo);
    expect(logo.querySelector("path").getAttribute("d")).toBe("M20 4 36 33H4L20 4Z");
    expect(logo.querySelector("circle").getAttribute("r")).toBe("4.5");
    expect(logo.querySelector("svg").children).toHaveLength(2);
    expect(logo.querySelector("svg").getAttribute("aria-hidden")).toBe("true");
    expect(logo.querySelector(".brand-wordmark").textContent).toBe("Prometheus");
  });

  it("does not duplicate the compact logo when reconnected", () => {
    const logo = document.createElement("prometheus-logo");
    logo.setAttribute("variant", "compact");
    document.body.appendChild(logo);
    logo.remove();
    document.body.appendChild(logo);
    expect(logo.querySelectorAll("svg")).toHaveLength(1);
    expect(logo.querySelector(".brand-wordmark")).toBeNull();
  });

  it("preserves a supplied logo asset", () => {
    const logo = document.createElement("prometheus-logo");
    logo.setAttribute("asset", "/final-logo.svg");
    document.body.appendChild(logo);
    expect(logo.querySelector("img").getAttribute("src")).toBe("/final-logo.svg");
    expect(logo.querySelector("svg")).toBeNull();
  });

  it("ships no eye graphics, scanning styles, or entry hooks", () => {
    const css = readFileSync(resolve(process.cwd(), "web/styles.css"), "utf8");
    const js = readFileSync(resolve(process.cwd(), "web/app.js"), "utf8");
    const logo = readFileSync(resolve(process.cwd(), "web/logo.js"), "utf8");
    for (const source of [css, js, logo]) {
      expect(source).not.toMatch(/eye-scan|logo-eye|logo-pupil|logo-entering/);
    }
    expect(css).toContain("--gold: #ffbd59");
    expect(css).toContain("--violet-bright: #c6baff");
  });
});
