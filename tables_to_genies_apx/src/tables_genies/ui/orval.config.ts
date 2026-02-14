import { defineConfig } from 'orval';

export default defineConfig({
  api: {
    input: 'http://localhost:8000/openapi.json',
    output: {
      mode: 'tags-split',
      target: './lib/api.ts',
      client: 'react-query',
      baseUrl: '/api',
      override: {
        mutator: {
          path: './lib/axios-instance.ts',
          name: 'customInstance',
        },
      },
    },
  },
});
