import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import JSZip from "jszip";

const zip = new JSZip();
zip.file("app.json", await readFile("app.json"));
zip.file("index.html", await readFile("dist/index.html"));
const html = (await readFile("dist/index.html", "utf8"));
for (const match of html.matchAll(/(?:src|href)="\.\/([^\"]+)"/g)) zip.file(match[1], await readFile(join("dist", match[1])));
await writeFile("HermesG2.ehpk", await zip.generateAsync({type: "nodebuffer", compression: "DEFLATE"}));

