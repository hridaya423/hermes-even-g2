import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import JSZip from "jszip";

const zip = new JSZip();
const reproducibleDate = new Date(0);
const fileOptions = {date: reproducibleDate, createFolders: false};
zip.file("app.json", await readFile("app.json"), fileOptions);
zip.file("index.html", await readFile("dist/index.html"), fileOptions);
const html = (await readFile("dist/index.html", "utf8"));
for (const match of html.matchAll(/(?:src|href)="\.\/([^\"]+)"/g)) {
  zip.file(match[1], await readFile(join("dist", match[1])), fileOptions);
}
await writeFile("HermesG2.ehpk", await zip.generateAsync({type: "nodebuffer", compression: "DEFLATE"}));
