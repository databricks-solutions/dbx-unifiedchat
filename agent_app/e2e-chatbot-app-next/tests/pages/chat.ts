import type { Locator, Page } from '@playwright/test';
import { expect } from '@playwright/test';

type ExecutionMode = 'parallel' | 'sequential';
type SynthesisRoute = 'auto' | 'table_route' | 'genie_route';
type ClarificationSensitivity = 'off' | 'low' | 'medium' | 'high' | 'on';

/**
 * Page object for the chat interface.
 * Wraps common interactions with the chat UI.
 */
export class ChatPage {
  constructor(private page: Page) {}

  private agentSettingsTrigger(): Locator {
    return this.page
      .getByTestId('agent-settings-trigger')
      .or(this.page.getByRole('button', { name: 'Settings' }));
  }

  private agentSettingsPanel(): Locator {
    return this.page.getByTestId('agent-settings-panel');
  }

  private executionModeSection(): Locator {
    return this.page.locator('div').filter({ hasText: 'Execution Mode' }).first();
  }

  private executionModeValue(): Locator {
    return this.page.getByTestId('execution-mode-value');
  }

  private executionModeToggle(): Locator {
    return this.page.getByTestId('execution-mode-toggle');
  }

  private synthesisRouteButton(route: SynthesisRoute): Locator {
    const routeLabel = {
      auto: 'Auto',
      table_route: 'Table',
      genie_route: 'Genie',
    }[route];

    return this.page
      .getByTestId(`synthesis-route-${route}`)
      .or(this.page.getByRole('button', { name: routeLabel }));
  }

  private clarificationSensitivitySlider(): Locator {
    return this.page.getByTestId('clarification-sensitivity-slider');
  }

  private clarificationSensitivityValue(): Locator {
    return this.page.getByTestId('clarification-sensitivity-value');
  }

  private settingsCancelButton(): Locator {
    return this.page.getByTestId('agent-settings-cancel');
  }

  private settingsConfirmButton(): Locator {
    return this.page.getByTestId('agent-settings-confirm');
  }

  async createNewChat() {
    await this.page.goto('/');
    await this.page.waitForLoadState('networkidle');
  }

  async sendUserMessage(text: string) {
    const input = this.page.getByTestId('multimodal-input');
    await input.fill(text);
    await this.page.getByTestId('send-button').click();
  }

  async openAgentSettings() {
    const panel = this.agentSettingsPanel();

    if (!(await panel.isVisible().catch(() => false))) {
      await this.agentSettingsTrigger().click();
    }

    await expect(panel).toBeVisible();
  }

  async setExecutionMode(mode: ExecutionMode) {
    await this.openAgentSettings();

    const value = this.executionModeValue();
    await expect(this.executionModeToggle()).toBeDisabled();
    await expect(value).toHaveText('Parallel');
    expect(mode).toBe('parallel');
  }

  async setSynthesisRoute(route: SynthesisRoute) {
    await this.openAgentSettings();

    const routeButton = this.synthesisRouteButton(route);
    await routeButton.click();
    const ariaPressed = await routeButton.getAttribute('aria-pressed');

    if (ariaPressed !== null) {
      expect(ariaPressed).toBe('true');
      return;
    }

    await expect(routeButton).toHaveClass(/bg-blue-600/);
  }

  async setClarificationSensitivity(
    sensitivity: ClarificationSensitivity,
  ) {
    await this.openAgentSettings();

    const indexBySensitivity: Record<ClarificationSensitivity, string> = {
      off: '0',
      low: '1',
      medium: '2',
      high: '3',
      on: '4',
    };
    const labelBySensitivity: Record<ClarificationSensitivity, string> = {
      off: 'Off',
      low: 'Low',
      medium: 'Medium',
      high: 'High',
      on: 'On',
    };

    const slider = this.clarificationSensitivitySlider();
    await slider.fill(indexBySensitivity[sensitivity]);
    await expect(this.clarificationSensitivityValue()).toHaveText(
      labelBySensitivity[sensitivity],
    );
  }

  async configureAgentSettings(
    executionMode: ExecutionMode,
    synthesisRoute: SynthesisRoute,
    clarificationSensitivity: ClarificationSensitivity = 'medium',
  ) {
    await this.setExecutionMode(executionMode);
    await this.setSynthesisRoute(synthesisRoute);
    await this.setClarificationSensitivity(clarificationSensitivity);
    await this.settingsConfirmButton().click();
    await expect(this.agentSettingsPanel()).toBeHidden();
  }

  async cancelAgentSettings() {
    await this.openAgentSettings();
    await this.settingsCancelButton().click();
    await expect(this.agentSettingsPanel()).toBeHidden();
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
