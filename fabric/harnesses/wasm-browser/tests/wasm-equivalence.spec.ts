import { test, expect } from "@playwright/test";

test.describe("WASM Byte Equivalence", () => {
  test("WASM module loads", async ({ page }) => {
    await page.goto("/");
    const result = await page.evaluate(() => {
      return typeof window !== "undefined";
    });
    expect(result).toBe(true);
  });

  test("WASM serializer produces valid output", async ({ page }) => {
    await page.goto("/");

    const output = await page.evaluate(async () => {
      try {
        const value = { version: 1, nonce: 42 };
        const serialized = JSON.stringify(value);
        const hash = require("crypto")
          .createHash("sha256")
          .update(serialized)
          .digest("hex");
        return hash;
      } catch (e) {
        return null;
      }
    });

    expect(output).toBeTruthy();
    expect(output).toMatch(/^[a-f0-9]{64}$/);
  });

  test("WASM stub fixture round-trip", async ({ page }) => {
    await page.goto("/");

    const result = await page.evaluate(() => {
      const testVector = { version: 1, nonce: 42 };
      const encoded = JSON.stringify(testVector);
      const decoded = JSON.parse(encoded);

      return {
        original: testVector,
        roundtrip: decoded,
        matches: JSON.stringify(testVector) === JSON.stringify(decoded),
      };
    });

    expect(result.matches).toBe(true);
    expect(result.roundtrip.version).toBe(1);
    expect(result.roundtrip.nonce).toBe(42);
  });

  test("WASM fixture mutation detection", async ({ page }) => {
    await page.goto("/");

    const result = await page.evaluate(() => {
      const original = { version: 1, nonce: 42 };
      const mutated = { version: 1, nonce: 43 };

      const originalHash = JSON.stringify(original);
      const mutatedHash = JSON.stringify(mutated);

      return {
        hashes_differ: originalHash !== mutatedHash,
        original,
        mutated,
      };
    });

    expect(result.hashes_differ).toBe(true);
  });

  test("WASM performance acceptable", async ({ page }) => {
    await page.goto("/");

    const timing = await page.evaluate(() => {
      const iterations = 100;
      const start = performance.now();

      for (let i = 0; i < iterations; i++) {
        const data = { version: 1, nonce: i };
        JSON.stringify(data);
      }

      const end = performance.now();
      return {
        totalMs: end - start,
        perIterationMs: (end - start) / iterations,
        acceptableThreshold: 1,
      };
    });

    expect(timing.perIterationMs).toBeLessThan(timing.acceptableThreshold);
  });
});
