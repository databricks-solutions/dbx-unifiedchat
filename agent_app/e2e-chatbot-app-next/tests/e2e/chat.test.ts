import { test, expect } from '../fixtures';
import { ChatPage } from '../pages/chat';

test.describe('Chat', () => {
  test('should send a message and receive a streaming response', async ({
    adaContext,
  }) => {
    const chatPage = new ChatPage(adaContext.page);
    await chatPage.createNewChat();

    await chatPage.sendUserMessage('What is the most common diagnosis code?');
    await chatPage.isGenerationComplete();

    const { content } = await chatPage.getRecentAssistantMessage();
    await expect(content).toBeVisible();
    const text = await content.textContent();
    expect(text).toBeTruthy();
    expect(text!.length).toBeGreaterThan(0);
  });

  test('should redirect to /chat/:id after sending a message', async ({
    adaContext,
  }) => {
    const chatPage = new ChatPage(adaContext.page);
    await chatPage.createNewChat();

    await chatPage.sendUserMessage('Show me enrollment trends');
    await chatPage.isGenerationComplete();

    await chatPage.hasChatIdInUrl();
  });

  test('should display user message in the chat', async ({ adaContext }) => {
    const chatPage = new ChatPage(adaContext.page);
    await chatPage.createNewChat();

    const userText = 'How many patients are in the dataset?';
    await chatPage.sendUserMessage(userText);

    const userMsg = await chatPage.getRecentUserMessage();
    await expect(userMsg).toContainText(userText);
  });
});

test.describe('Multi-Agent Streaming', () => {
  test('should display assistant response with content from multi-agent workflow', async ({
    adaContext,
  }) => {
    const chatPage = new ChatPage(adaContext.page);
    await chatPage.createNewChat();

    await chatPage.sendUserMessage('Summarize the claims data');
    await chatPage.isGenerationComplete();

    const { content } = await chatPage.getRecentAssistantMessage();
    await expect(content).toBeVisible();
  });

  test('should handle multiple sequential messages', async ({
    adaContext,
  }) => {
    const chatPage = new ChatPage(adaContext.page);
    await chatPage.createNewChat();

    await chatPage.sendUserMessage('What tables are available?');
    await chatPage.isGenerationComplete();

    const count1 = await chatPage.getAssistantMessageCount();
    expect(count1).toBeGreaterThanOrEqual(1);

    await chatPage.sendUserMessage('Tell me more about the first one');
    await chatPage.isGenerationComplete();

    const count2 = await chatPage.getAssistantMessageCount();
    expect(count2).toBeGreaterThan(count1);
  });
});

test.describe('Ephemeral Mode', () => {
  test('should work without database (no chat history persistence)', async ({
    adaContext,
  }) => {
    const chatPage = new ChatPage(adaContext.page);
    await chatPage.createNewChat();

    await chatPage.sendUserMessage('Simple test query');
    await chatPage.isGenerationComplete();

    const { content } = await chatPage.getRecentAssistantMessage();
    await expect(content).toBeVisible();
  });
});
