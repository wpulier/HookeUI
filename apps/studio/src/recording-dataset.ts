import type { DatasetState } from './protocol';

type Reply = {ok?: boolean; dataset?: DatasetState; error?: string | null};
/** A delivered command is not proof that collection changed state. */
export async function requestRecordingDataset(on: boolean, request: () => Promise<Reply>, timeoutMs = 5000): Promise<DatasetState> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const reply = await Promise.race([request(), new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error('the app did not answer')), timeoutMs);
    })]);
    if (reply.ok !== true || !reply.dataset || reply.dataset.on !== on) {
      throw new Error(reply.error || `the app did not confirm that collection ${on ? 'started' : 'stopped'}`);
    }
    return reply.dataset;
  } finally { clearTimeout(timer); }
}
