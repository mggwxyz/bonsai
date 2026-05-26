import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const config: Config = {
  title: 'Bonsai',
  tagline: 'Parallel git worktrees with ports, env files, and local HTTPS URLs.',
  favicon: 'img/bonsai.svg',

  future: {
    v4: true,
  },

  url: 'https://mggwxyz.github.io',
  baseUrl: '/bonsai/',

  organizationName: 'mggwxyz',
  projectName: 'bonsai',

  onBrokenLinks: 'throw',
  markdown: {
    hooks: {
      onBrokenMarkdownLinks: 'warn',
    },
  },

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          editUrl:
            'https://github.com/mggwxyz/bonsai/tree/main/docs-site/',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    colorMode: {
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: 'Bonsai',
      logo: {
        alt: 'Bonsai logo',
        src: 'img/bonsai.svg',
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'docs',
          position: 'left',
          label: 'Docs',
        },
        {
          href: 'https://github.com/mggwxyz/bonsai',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            {
              label: 'Quickstart',
              to: '/docs/intro',
            },
            {
              label: 'Commands',
              to: '/docs/commands',
            },
          ],
        },
        {
          title: 'Project',
          items: [
            {
              label: 'GitHub',
              href: 'https://github.com/mggwxyz/bonsai',
            },
            {
              label: 'Issues',
              href: 'https://github.com/mggwxyz/bonsai/issues',
            },
          ],
        },
        {
          title: 'Install',
          items: [
            {
              label: 'Homebrew',
              to: '/docs/install',
            },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} Michael. Built with Docusaurus.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
