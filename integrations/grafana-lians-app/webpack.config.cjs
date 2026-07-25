const path = require('path');
const CopyPlugin = require('copy-webpack-plugin');

module.exports = {
  entry: './src/module.tsx',
  output: {
    path: path.resolve(__dirname, 'dist'),
    filename: 'module.js',
    library: { type: 'system' },
    clean: true,
  },
  devtool: 'source-map',
  externals: [
    'react',
    'react-dom',
    'react/jsx-runtime',
    'react/jsx-dev-runtime',
    /^@grafana\/.*/,
  ],
  resolve: { extensions: ['.ts', '.tsx', '.js'] },
  module: {
    rules: [
      {
        test: /\.[jt]sx?$/,
        exclude: /node_modules/,
        use: {
          loader: 'swc-loader',
          options: {
            jsc: {
              parser: { syntax: 'typescript', tsx: true },
              transform: { react: { runtime: 'automatic' } },
              target: 'es2022',
            },
          },
        },
      },
    ],
  },
  plugins: [
    new CopyPlugin({
      patterns: [
        { from: 'src/plugin.json', to: 'plugin.json' },
        { from: 'src/img', to: 'img' },
        { from: 'src/dashboards', to: 'dashboards' },
        { from: 'README.md', to: 'README.md' },
      ],
    }),
  ],
};
