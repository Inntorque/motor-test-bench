import {defineConfig} from 'vite';

export default defineConfig({
  root: 'site',
  publicDir: false,
  server: {host: '127.0.0.1', port: 5180, strictPort: true},
});
