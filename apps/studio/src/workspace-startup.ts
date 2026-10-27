/** Keep startup recoverable without assigning browser drafts to an unidentified profile. */
export async function workspaceProfile(
  request: () => Promise<Record<string, unknown>>,
  retry: (message: string) => Promise<void>,
): Promise<{ profile: string; review: boolean }> {
  for (;;) {
    try {
      const reply = await request();
      if (typeof reply.profile !== 'string' || !reply.profile) {
        throw new Error('The workspace profile could not be identified. Reconnect to the updated Studio backend.');
      }
      return { profile: reply.profile, review: reply.review === true };
    } catch (error) {
      await retry(error instanceof Error ? error.message : 'The workspace could not connect.');
    }
  }
}
