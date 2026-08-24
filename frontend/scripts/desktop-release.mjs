import { mkdir, readFile, writeFile } from 'node:fs/promises';

const [command, rawVersion] = process.argv.slice(2);
const version = rawVersion?.replace(/^v/, '');

if (!['prepare', 'manifest'].includes(command) || !/^\d+\.\d+\.\d+$/.test(version || '')) {
  throw new Error('Usage: node scripts/desktop-release.mjs <prepare|manifest> <X.Y.Z>');
}

async function readJson(path) {
  return JSON.parse(await readFile(path, 'utf8'));
}

async function writeJson(path, value) {
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`);
}

if (command === 'prepare') {
  const config = await readJson('neutralino.config.json');
  const packageJson = await readJson('package.json');
  const packageLock = await readJson('package-lock.json');

  config.version = version;
  packageJson.version = version;
  packageLock.version = version;
  packageLock.packages[''].version = version;

  await Promise.all([
    writeJson('neutralino.config.json', config),
    writeJson('package.json', packageJson),
    writeJson('package-lock.json', packageLock)
  ]);
}

if (command === 'manifest') {
  const repository = process.env.GITHUB_REPOSITORY || 'jonasheschl/locus';
  const tag = `v${version}`;
  const manifest = {
    applicationId: 'at.jonas.locus',
    version,
    resourcesURL: `https://raw.githubusercontent.com/${repository}/desktop-updates/${tag}/resources.neu`
  };

  await mkdir('desktop-dist', { recursive: true });
  await writeJson('desktop-dist/update.json', manifest);
}
