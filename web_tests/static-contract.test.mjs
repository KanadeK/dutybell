import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const staticRoot = new URL("../src/dutybell/static/", import.meta.url);

test("author styles cannot override application hidden views", async () => {
  const [styles, application, document] = await Promise.all([
    readFile(new URL("styles.css", staticRoot), "utf8"),
    readFile(new URL("app.js", staticRoot), "utf8"),
    readFile(new URL("index.html", staticRoot), "utf8"),
  ]);

  assert.match(styles, /\[hidden\]\s*\{\s*display:\s*none\s*!important;\s*\}/);
  assert.match(application, /welcomeView\.hidden\s*=\s*true/);
  assert.match(application, /function openPrivateLink\(parsed\)/);
  assert.doesNotMatch(application, /state\.actor\s*\|\|\s*"household"/);
  assert.match(document, /id="welcomeView"/);
  assert.match(document, /id="roomView"[^>]*\shidden(?:\s|>)/);
});
