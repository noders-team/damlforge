// Copies docs/guide/*.md into the Starlight content folder. The text is not changed:
// the "# Title" line becomes frontmatter, and links are rewritten to site URLs.
import { mkdir, readdir, readFile, rm, writeFile } from 'node:fs/promises';

const REPO = 'https://github.com/noders-team/damlforge';
const guideDir = new URL('../../guide/', import.meta.url);
const outDir = new URL('../src/content/docs/guide/', import.meta.url);

const files = (await readdir(guideDir)).filter((f) => /^\d+-.+\.md$/.test(f)).sort();
const slugOf = (file) => file.replace(/^\d+-/, '').replace(/\.md$/, '');

function rewriteLink(target) {
	const [file, anchor] = target.split('#');
	if (files.includes(file)) return `/guide/${slugOf(file)}/${anchor ? `#${anchor}` : ''}`;
	// Links out of docs/guide/ go to GitHub, which redirects folder URLs from blob/ to tree/.
	if (file.startsWith('../../')) return `${REPO}/blob/main/${target.slice('../../'.length)}`;
	return target;
}

// Write only changed files and remove only stale ones: deleting the folder while `astro dev`
// runs drops the guide pages from its content store until a restart.
await mkdir(outDir, { recursive: true });
const written = new Set();

for (const file of files) {
	const [h1, ...rest] = (await readFile(new URL(file, guideDir), 'utf8')).split('\n');
	if (!h1.startsWith('# ')) throw new Error(`guide/${file}: the first line must be "# <title>"`);
	let inFence = false;
	const body = rest.map((line) => {
		if (line.trimStart().startsWith('```')) inFence = !inFence;
		return inFence ? line : line.replace(/\]\(([^)\s]+)\)/g, (_, t) => `](${rewriteLink(t)})`);
	});
	const frontmatter = [
		'---',
		`title: ${JSON.stringify(h1.slice(2))}`,
		`editUrl: ${REPO}/edit/main/docs/guide/${file}`,
		'sidebar:',
		`  order: ${parseInt(file, 10)}`,
		'---',
	];
	const target = new URL(`${slugOf(file)}.md`, outDir);
	const text = [...frontmatter, ...body].join('\n');
	if ((await readFile(target, 'utf8').catch(() => null)) !== text) await writeFile(target, text);
	written.add(`${slugOf(file)}.md`);
}
for (const stale of (await readdir(outDir)).filter((f) => !written.has(f))) await rm(new URL(stale, outDir));
console.log(`synced ${files.length} guide pages`);
