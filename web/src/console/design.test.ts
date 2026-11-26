import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// The console uses only the shared Cite tokens (DESIGN.md, D-098).
const read = (name: string) => readFileSync(resolve(import.meta.dirname, name), "utf8");
const tokensCss = read("cite-tokens.css");
const consoleCss = read("console.css");

function block(css: string, selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  const body = css.slice(start, css.indexOf("}", start));
  const values: Record<string, string> = {};
  for (const match of body.matchAll(/--cite-([\w-]+):\s*(#[0-9a-f]{6});/gi)) values[match[1]] = match[2];
  return values;
}

const THEMES = {
  light: block(tokensCss, ":root"),
  dark: { ...block(tokensCss, ":root"), ...block(tokensCss, ':root[data-theme="dark"]') },
};

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// [foreground, background] pairs that console.css puts together.
const TEXT_PAIRS = [
  ["text", "surface"],
  ["text", "canvas"],
  ["muted", "surface"],
  ["muted", "canvas"],
  ["muted", "rule"],
  ["link", "surface"],
  ["link", "canvas"],
  ["on-primary", "primary"],
  ["on-primary", "primary-hover"],
  ["on-primary", "danger-solid"],
  ["on-primary", "danger-solid-hover"],
  ["danger-text", "surface"],
  ["danger-text", "danger-bg"],
  ["success-text", "success-bg"],
  ["warning-text", "warning-bg"],
  ["info-text", "info-bg"],
  ["text", "info-bg"],
  ["neutral-text", "neutral-bg"],
];
const EDGE_PAIRS = [
  ["input-border", "surface"],
  ["input-border", "canvas"],
  ["focus", "surface"],
  ["focus", "canvas"],
  ["primary", "surface"],
  ["primary", "canvas"],
];

describe("console design tokens", () => {
  it("console.css types no color of its own", () => {
    const css = consoleCss.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(css).not.toMatch(/#[0-9a-f]{3,8}\b|\b(rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(/i);
  });

  it("console.css uses only tokens that exist", () => {
    const used = new Set([...consoleCss.matchAll(/var\(--([\w-]+)\)/g)].map((m) => m[1]));
    const defined = new Set([...tokensCss.matchAll(/(--cite-[\w-]+):/g)].map((m) => m[1].slice(2)));
    expect([...used].filter((name) => !defined.has(name))).toEqual([]);
  });

  for (const [theme, colors] of Object.entries(THEMES)) {
    it(`${theme}: text pairs reach 4.5:1 and control edges 3:1`, () => {
      const low = [
        ...TEXT_PAIRS.map(([fg, bg]) => [fg, bg, 4.5] as const),
        ...EDGE_PAIRS.map(([fg, bg]) => [fg, bg, 3] as const),
      ]
        .map(([fg, bg, min]) => ({ pair: `${fg} on ${bg}`, ratio: contrast(colors[fg], colors[bg]), min }))
        .filter((row) => !(row.ratio >= row.min));
      expect(low).toEqual([]);
    });
  }
});
