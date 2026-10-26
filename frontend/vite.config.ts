import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';

// Editor tooling writes short-lived temp files into watched folders; ignore them
// so the dev watcher does not hold locks on files that are about to be replaced.
export default defineConfig({
  plugins: [tailwindcss()],
  server: { watch: { ignored: ['**/.*.tmpdir/**', '**/.*.tmp'] } },
});
