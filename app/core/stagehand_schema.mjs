// Stagehand 3's nullable action object omits this nested JSON Schema keyword.
// OpenAI structured output requires closed objects, including nullable branches.
export function strictActionSchema(value) {
  if (Array.isArray(value)) return value.map(strictActionSchema);
  if (!value || typeof value !== 'object') return value;
  const result = Object.fromEntries(Object.entries(value).map(([k,v]) => [k,strictActionSchema(v)]));
  if (result.type === 'object' && result.properties) result.additionalProperties = false;
  return result;
}
