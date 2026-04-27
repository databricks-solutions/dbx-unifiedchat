import { z } from 'zod';

const textPartSchema = z.object({
  type: z.enum(['text']),
  text: z.string().min(1),
});

const filePartSchema = z.object({
  type: z.enum(['file']),
  mediaType: z.enum(['image/jpeg', 'image/png']),
  name: z.string().min(1),
  url: z.string().url(),
});

const partSchema = z.union([textPartSchema, filePartSchema]);

// Schema for previous messages in ephemeral mode
// More permissive to handle various message types (user, assistant, tool calls, etc.)
const previousMessageSchema = z.object({
  // Accept any string ID: user messages have UUIDs, but assistant messages
  // from the AI SDK use nanoid (16-char alphanumeric), not UUIDs.
  id: z.string(),
  role: z.enum(['user', 'assistant', 'system']),
  parts: z.array(z.any()), // Permissive to handle text, tool calls, tool results
});

const agentSettingsSchema = z
  .object({
    executionMode: z.enum(['parallel', 'sequential']).default('parallel'),
    synthesisRoute: z.enum(['auto', 'table_route', 'genie_route']).default('auto'),
    clarificationSensitivity: z
      .enum(['off', 'low', 'medium', 'high', 'on'])
      .default('medium'),
    countOnly: z.boolean().default(true),
  })
  .optional();

export const postRequestBodySchema = z.object({
  id: z.string().uuid(),
  message: z
    .object({
      id: z.string().uuid(),
      role: z.enum(['user']),
      parts: z.array(partSchema),
    })
    .optional(),
  selectedChatModel: z.enum(['chat-model', 'chat-model-reasoning']),
  selectedVisibilityType: z.enum(['public', 'private']),
  previousMessages: z.array(previousMessageSchema).optional(),
  agentSettings: agentSettingsSchema,
});

export type PostRequestBody = z.infer<typeof postRequestBodySchema>;
