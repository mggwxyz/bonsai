const {execFileSync} = require('node:child_process');
const {mkdirSync, writeFileSync} = require('node:fs');
const path = require('node:path');

const repoRoot = path.resolve(__dirname, '..', '..');
const outputPath = path.resolve(__dirname, '..', 'docs', 'commands.md');

const cliDocs = execFileSync(
  'uv',
  [
    'run',
    'typer',
    '--app',
    'app',
    'src/bonsai/cli.py',
    'utils',
    'docs',
    '--name',
    'bonsai',
    '--title',
    'Command Reference',
  ],
  {
    cwd: repoRoot,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'inherit'],
  },
);

mkdirSync(path.dirname(outputPath), {recursive: true});
writeFileSync(
  outputPath,
  `---\ntitle: Command Reference\n---\n\n${cliDocs.trimEnd()}\n`,
  'utf8',
);
