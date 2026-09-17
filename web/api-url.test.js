import { describe, expect, it } from "vitest";

import { apiUrl } from "./api-url.js";

describe("production API URL configuration", () => {
  it("keeps local development calls relative when no public API is configured", () => {
    expect(apiUrl("/api/health", "")).toBe("/api/health");
  });

  it("targets the configured backend origin for production API and media paths", () => {
    expect(apiUrl("/api/jobs/job-1/result", "https://prometheus-api.onrender.com/")).toBe(
      "https://prometheus-api.onrender.com/api/jobs/job-1/result"
    );
  });

  it("rejects non-HTTP(S) API origins", () => {
    expect(() => apiUrl("/api/health", "ftp://invalid.example")).toThrow("HTTP(S)");
  });
});
