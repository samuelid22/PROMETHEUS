import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";

describe("presentation-only Prometheus eye logo", () => {
  beforeAll(async () => { await import("./logo.js"); });
  beforeEach(() => { document.body.innerHTML = ""; });

  it("retains the original triangle and gold dot with a decorative eye", () => {
    const logo = document.createElement("prometheus-logo");
    document.body.appendChild(logo);
    expect(logo.querySelector(".logo-triangle").getAttribute("d")).toBe("M20 4 36 33H4L20 4Z");
    expect(logo.querySelector(".logo-dot").getAttribute("r")).toBe("4.5");
    expect(logo.querySelector("svg").getAttribute("aria-hidden")).toBe("true");
    expect(logo.querySelector(".logo-pupil circle")).not.toBeNull();
    expect(logo.querySelector(".brand-wordmark").textContent).toBe("Prometheus");
  });

  it("does not duplicate the logo when reconnected", () => {
    const logo = document.createElement("prometheus-logo");
    logo.setAttribute("variant", "compact");
    document.body.appendChild(logo);
    logo.remove();
    document.body.appendChild(logo);
    expect(logo.querySelectorAll("svg")).toHaveLength(1);
    expect(logo.querySelector(".brand-wordmark")).toBeNull();
  });

  it("preserves supplied logo assets rather than modifying their colors", () => {
    const logo = document.createElement("prometheus-logo");
    logo.setAttribute("asset", "/final-logo.svg");
    document.body.appendChild(logo);
    expect(logo.querySelector("img").getAttribute("src")).toBe("/final-logo.svg");
    expect(logo.querySelector("svg")).toBeNull();
  });

  it("keeps logo colors and provides reduced-motion and processing-error safeguards", () => {
    const css = readFileSync(resolve(process.cwd(), "web/styles.css"), "utf8");
    expect(css).toContain("--gold: #ffbd59");
    expect(css).toContain("--violet-bright: #c6baff");
    expect(css).toContain(".logo-eye-outline { stroke: var(--gold)");
    expect(css).toContain('body[data-screen="processing"]:not(:has(#job-error:not(.hidden)))');
    expect(css).toContain(".logo-pupil, .initialization-dots i, .process-mark { animation: none !important; }");
  });
});
