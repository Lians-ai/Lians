const fs = require('fs');
const path = require('path');
const webpack = require('webpack');

const root = path.resolve(__dirname, '..');
const config = {
  ...require(path.join(root, 'webpack.config.cjs')),
  context: root,
  mode: 'production',
};

const hostProvidedPackages = [
  '@grafana',
  'react',
  'react-dom',
  'dompurify',
  'immutable',
  'lodash',
  'uuid',
  '@opentelemetry',
  'js-cookie',
  'react-router',
  'react-router-dom',
];

function flattenModules(modules = []) {
  return modules.flatMap((module) => [
    module,
    ...flattenModules(module.modules),
    ...flattenModules(module.children),
  ]);
}

function packageFromNodeModules(modulePath) {
  const normalized = modulePath.replaceAll('\\', '/');
  const marker = '/node_modules/';
  const markerIndex = normalized.lastIndexOf(marker);
  if (markerIndex === -1) {
    return null;
  }

  const segments = normalized.slice(markerIndex + marker.length).split('/');
  return segments[0].startsWith('@')
    ? `${segments[0]}/${segments[1]}`
    : segments[0];
}

function isHostProvided(packageName) {
  return hostProvidedPackages.some(
    (hostPackage) =>
      packageName === hostPackage || packageName.startsWith(`${hostPackage}/`),
  );
}

webpack(config, (error, stats) => {
  if (error) {
    console.error(error.stack || error);
    process.exitCode = 1;
    return;
  }

  const output = stats.toString({
    all: false,
    assets: true,
    colors: process.stdout.isTTY,
    errors: true,
    timings: true,
    warnings: true,
  });
  if (output) {
    console.log(output);
  }

  if (stats.hasErrors()) {
    process.exitCode = 1;
    return;
  }

  const details = stats.toJson({
    all: false,
    assets: true,
    errors: true,
    modules: true,
    nestedModules: true,
    warnings: true,
  });
  const modules = flattenModules(details.modules);
  const bundledHostModules = modules
    .map((module) => module.nameForCondition || module.identifier || '')
    .map((modulePath) => ({
      modulePath,
      packageName: packageFromNodeModules(modulePath),
    }))
    .filter(
      ({ packageName }) => packageName && isHostProvided(packageName),
    );

  if (bundledHostModules.length > 0) {
    console.error('Host-provided or audited libraries were bundled unexpectedly:');
    for (const { modulePath } of bundledHostModules) {
      console.error(`- ${modulePath}`);
    }
    process.exitCode = 1;
    return;
  }

  const moduleAsset = path.join(root, 'dist', 'module.js');
  const source = fs.readFileSync(moduleAsset, 'utf8');
  const requiredExternals = [
    '@grafana/data',
    '@grafana/ui',
    'react/jsx-runtime',
  ];
  const missingExternals = requiredExternals.filter(
    (external) => !source.includes(external),
  );
  if (missingExternals.length > 0) {
    console.error(
      `Generated module is missing expected SystemJS externals: ${missingExternals.join(', ')}`,
    );
    process.exitCode = 1;
    return;
  }

  const moduleAssetStats = fs.statSync(moduleAsset);
  console.log(
    `Bundle inspection passed: ${modules.length} module records, ` +
      `${moduleAssetStats.size} byte module.js, no host-provided package sources bundled.`,
  );
});
