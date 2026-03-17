import { test as baseTest } from '@playwright/test';
import { createAuthenticatedContext, type UserContext } from './helpers';

export const test = baseTest.extend<
  {},
  {
    adaContext: UserContext;
    babbageContext: UserContext;
  }
>({
  adaContext: [
    async ({ browser }, use, workerInfo) => {
      const ada = await createAuthenticatedContext({
        browser,
        name: `ada-${workerInfo.workerIndex}`,
      });
      await use(ada);
      await ada.context.close();
    },
    { scope: 'worker' },
  ],
  babbageContext: [
    async ({ browser }, use, workerInfo) => {
      const babbage = await createAuthenticatedContext({
        browser,
        name: `babbage-${workerInfo.workerIndex}`,
      });
      await use(babbage);
      await babbage.context.close();
    },
    { scope: 'worker' },
  ],
});

export { expect } from '@playwright/test';
