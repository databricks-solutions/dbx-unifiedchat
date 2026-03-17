import type { Page } from '@playwright/test';
import { expect } from '@playwright/test';

/**
 * Page object for the chat interface.
 * Wraps common interactions with the chat UI.
 */
export class ChatPage {
  constructor(private page: Page) {}

  async createNewChat() {
    await this.page.goto('/');
    await this.page.waitForLoadState('networkidle');
  }

  async sendUserMessage(text: string) {
    const input = this.page.getByTestId('multimodal-input');
    await input.fill(text);
    await this.page.getByTestId('send-button').click();
  }

  async isGenerationComplete() {
    const stopButton = this.page.getByTestId('stop-button');
    try {
      await stopButton.waitFor({ state: 'visible', timeout: 5000 });
    } catch {
      // stop button might never appear for fast responses
    }
    await stopButton.waitFor({ state: 'hidden', timeout: 15000 });
  }

  async hasChatIdInUrl() {
    await expect(this.page).toHaveURL(
      /\/chat\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/,
    );
  }

  async getRecentAssistantMessage() {
    const messages = this.page.getByTestId('message-assistant');
    const count = await messages.count();
    const last = messages.nth(count - 1);
    return {
      content: last.getByTestId('message-content'),
      element: last,
    };
  }

  async getRecentUserMessage() {
    const messages = this.page.getByTestId('message-user');
    const count = await messages.count();
    return messages.nth(count - 1);
  }

  async getAssistantMessageCount() {
    return this.page.getByTestId('message-assistant').count();
  }
}
