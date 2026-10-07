import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
const charts = readFileSync(new URL("../src/components/AnalyticsCharts.css", import.meta.url), "utf8");

function palette(selector: string): Record<string, string> {
  const start = styles.indexOf(`${selector} {`);
  const block = styles.slice(start, styles.indexOf("}", start) + 1);
  return Object.fromEntries([...block.matchAll(/(--[\w-]+):\s*([^;]+);/g)]
    .map((match) => [match[1]!, match[2]!.trim()]));
}

const dark = palette(":root");
const light = palette(':root[data-theme="light"]');

function luminance(hex: string): number {
  if (!/^#[\da-f]{6}$/i.test(hex)) throw new Error(`Expected opaque RGB color: ${hex}`);
  const channels = [1, 3, 5].map((offset) => {
    const value = parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
  });
  return channels[0]! * .2126 + channels[1]! * .7152 + channels[2]! * .0722;
}

function contrast(a: string, b: string): number {
  const values = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (values[0]! + .05) / (values[1]! + .05);
}

describe("dashboard theme palettes", () => {
  it("defines the same semantic color tokens in both palettes", () => {
    expect(Object.keys(light).sort()).toEqual(Object.keys(dark).sort());
    expect(Object.keys(dark).length).toBeGreaterThan(50);
    const used = [...`${styles}\n${charts}`.matchAll(/var\((--[\w-]+)\)/g)].map((match) => match[1]!);
    for (const token of used) expect(dark[token], token).toBeDefined();
  });

  it("keeps application and chart colors in the palettes instead of fixed dark surfaces", () => {
    const themedStyles = styles.replace(/:root(?:\[data-theme="light"\])?\s*\{[^}]+\}/g, "");
    expect(`${themedStyles}\n${charts}`).not.toMatch(/#[\da-f]{3,8}\b|rgba?\(/i);
  });

  for (const [name, tokens] of Object.entries({ dark, light })) {
    it(`${name} normal-size text and state labels meet 4.5:1 contrast`, () => {
      const pairs = [
        ...["--bg", "--surface", "--surface-subtle", "--chrome-sidebar", "--chrome-topbar", "--thead-bg", "--row-hover", "--input-bg", "--control-bg", "--code-bg"]
          .flatMap((background) => ["--text", "--strong", "--muted", "--faint"].map((foreground) => [foreground, background])),
        ...["danger", "warning", "success", "info", "neutral", "primary"].map((tone) => [`--${tone}`, `--${tone}-soft`]),
        ...["critical", "high", "medium", "low"].map((severity) => [`--severity-${severity}-text`, `--severity-${severity}-bg`]),
        ["--button-primary-text", "--button-primary-bg"], ["--button-primary-text", "--button-primary-hover"],
        ["--selection-text", "--selection-bg"],
      ];
      for (const [foreground, background] of pairs) {
        expect(contrast(tokens[foreground!]!, tokens[background!]!), `${name}: ${foreground} on ${background}`).toBeGreaterThanOrEqual(4.5);
      }
    });
  }
});
