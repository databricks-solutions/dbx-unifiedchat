import { defineConfig, devices } from '@playwright/test';
import { config } from 'dotenv';
import path from 'node:path';

/**
 * Dual-Mode Testing Configuration
 *
 * Tests run in two modes:
 * - with-db: Tests with PostgreSQL database (persistent mode)
 * - ephemeral: Tests without database (ephemeral mode)
 *
 * Set TEST_MODE environment variable:
 *   TEST_MODE=with-db (default) - Runs with database
 *   TEST_MODE=ephemeral - Runs without database
 *
 * Run both modes: npm test
 * Run specific mode: npm run test:with-db or npm run test:ephemeral
 */

const TEST_MODE = process.env.TEST_MODE || 'with-db';

// Load parent .env for database/Databricks config
if (TEST_MODE === 'with-db') {
  config({ path: [path.resolve(__dirname, '..', '.env'), '.env'] });
}

console.log(`[Playwright] Running in "${TEST_MODE}" mode`);

if (TEST_MODE === 'with-db') {
  const hasDatabaseVars =
    process.env.POSTGRES_URL || (process.env.PGHOST && process.env.PGDATABASE);

  if (!hasDatabaseVars) {
    console.error(
      '\n  ERROR: Running with-db tests but no database configuration found!',
    );
    console.error('Expected POSTGRES_URL or PGHOST+PGDATABASE in .env');
    console.error('\nPlease either:');
    console.error(
      '  1. Run ./scripts/dev-local.sh first to set up database config, or',
    );
    console.error(
      '  2. Run ephemeral tests instead: npm run test:ephemeral\n',
    );
    process.exit(0);
  }

  console.log('  Database configuration found, tests will use database');
}

const PORT = process.env.PORT || 3000;
const baseURL = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 3,
  workers: process.env.CI ? 2 : 8,
  reporter: 'html',
  use: {
    baseURL,
    trace: 'retain-on-failure',
  },

  timeout: 20 * 1000,
  expect: {
    timeout: 15 * 1000,
  },

  projects: [
    {
      name: 'e2e',
      testMatch: /e2e\/.*.test.ts/,
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'routes',
      testMatch: /routes\/.*(?<!\.api-proxy)\.test\.ts$/,
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'routes-api-proxy',
      testMatch: /routes\/.*\.api-proxy\.test\.ts$/,
      use: { baseURL: 'http://localhost:3003' },
    },
  ],

  webServer: [
    {
      command: 'npm run dev',
      url: `${baseURL}/ping`,
      timeout: 20 * 1000,
      reuseExistingServer: !process.env.CI,
      env: {
        PLAYWRIGHT: 'True',
        DATABRICKS_SERVING_ENDPOINT: 'mock-value',
        DATABRICKS_CLIENT_ID: 'mock-value',
        DATABRICKS_CLIENT_SECRET: 'mock-value',
        DATABRICKS_HOST: 'mock-value',
        ...(TEST_MODE === 'ephemeral'
          ? {
              POSTGRES_URL: '',
              PGHOST: '',
              PGDATABASE: '',
              PGUSER: '',
              PGPASSWORD: '',
              PGSSLMODE: '',
            }
          : {
              DATABRICKS_CLIENT_ID: '',
              DATABRICKS_CLIENT_SECRET: '',
              DATABRICKS_HOST: '',
              DATABRICKS_CONFIG_PROFILE:
                process.env.DATABRICKS_CONFIG_PROFILE ?? '',
              POSTGRES_URL: process.env.POSTGRES_URL ?? '',
              PGHOST: process.env.PGHOST ?? '',
              PGDATABASE: process.env.PGDATABASE ?? '',
              PGUSER: process.env.PGUSER ?? '',
              PGPASSWORD: process.env.PGPASSWORD ?? '',
              PGSSLMODE: process.env.PGSSLMODE ?? '',
            }),
      },
    },
    {
      command: 'npm run dev:server',
      url: 'http://localhost:3003/ping',
      timeout: 20 * 1000,
      reuseExistingServer: !process.env.CI,
      env: {
        PLAYWRIGHT: 'True',
        CHAT_APP_PORT: '3003',
        API_PROXY: 'http://mlflow-agent-server-mock/invocations',
        DATABRICKS_CLIENT_ID: 'mock-value',
        DATABRICKS_CLIENT_SECRET: 'mock-value',
        DATABRICKS_HOST: 'mock-value',
        POSTGRES_URL: '',
        PGHOST: '',
        PGDATABASE: '',
        PGUSER: '',
        PGPASSWORD: '',
        PGSSLMODE: '',
      },
    },
  ],
});
