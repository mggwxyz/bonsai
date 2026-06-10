import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  docs: [
    'intro',
    {
      type: 'category',
      label: 'Getting Started',
      collapsed: false,
      items: ['install', 'quickstart'],
    },
    {
      type: 'category',
      label: 'Guides',
      collapsed: false,
      items: [
        'configuration',
        'worktrees',
        'running-apps',
        'urls-and-ports',
        'workspace-views',
        'shell-integration',
      ],
    },
    {
      type: 'category',
      label: 'Reference',
      collapsed: false,
      items: ['commands', 'troubleshooting'],
    },
  ],
};

export default sidebars;
