/** Server-side TwelveLabs config snapshot (never exposes secrets). */

export type TwelveLabsConfigStatus = {
  enabled: boolean;
  apiKeyConfigured: boolean;
  indexIdConfigured: boolean;
  failOpen: boolean;
  multimodalEnabled: boolean;
  /** Active model IDs (for the analysis dashboard). */
  models: {
    marengo: string;
    pegasus: string;
    geminiFlash: string;
    geminiPro: string;
    geminiMultimodal: string;
    geminiThinkingLevel: string;
  };
};

export function getTwelveLabsConfigStatus(): TwelveLabsConfigStatus {
  return {
    enabled: process.env.TWELVELABS_ENABLED === "true",
    apiKeyConfigured: Boolean(
      process.env.TWELVELABS_API_KEY?.trim() &&
        process.env.TWELVELABS_API_KEY !== "your_key",
    ),
    indexIdConfigured: Boolean(
      process.env.TWELVELABS_INDEX_ID?.trim() &&
        process.env.TWELVELABS_INDEX_ID !== "your_index_id",
    ),
    failOpen: process.env.TWELVELABS_FAIL_OPEN !== "false",
    multimodalEnabled: process.env.GEMINI_MULTIMODAL_ENABLED === "true",
    models: {
      marengo: process.env.TWELVELABS_MODEL_MARENGO || "marengo3.0",
      pegasus: process.env.TWELVELABS_MODEL_PEGASUS || "pegasus1.5",
      geminiFlash: process.env.GEMINI_FLASH_MODEL || "gemini-3.5-flash",
      geminiPro: process.env.GEMINI_PRO_MODEL || "gemini-3.1-pro-preview",
      geminiMultimodal:
        process.env.GEMINI_MULTIMODAL_MODEL || "gemini-3.1-pro-preview",
      geminiThinkingLevel: process.env.GEMINI_THINKING_LEVEL || "low",
    },
  };
}
