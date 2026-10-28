// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import starlightLinksValidator from 'starlight-links-validator';
import starlightLlmsTxt from 'starlight-llms-txt';
import starlightPageActions from 'starlight-page-actions';

export default defineConfig({
	site: 'https://docs.damlforge.noders.services',
	integrations: [
		starlight({
			title: 'DamlForge',
			description: 'Write Daml smart contracts for Canton in Python syntax.',
			social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/noders-team/damlforge' }],
			sidebar: [{ label: 'User guide', items: [{ autogenerate: { directory: 'guide' } }] }],
			customCss: ['./src/styles/custom.css'],
			plugins: [
				starlightLinksValidator(),
				// rawContent builds llms-full.txt from the Markdown source; the rendered HTML adds
				// "Section titled" anchor text to every heading.
				starlightLlmsTxt({ rawContent: true }),
				// No baseUrl: with one, this plugin writes its own llms.txt over starlight-llms-txt's.
				starlightPageActions(),
			],
		}),
	],
});
